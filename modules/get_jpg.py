import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qs, quote, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


MAX_IMAGE_BYTES = 50 * 1024 * 1024
RETRYABLE_STATUSES = (429, 500, 502, 503, 504)


@dataclass(frozen=True)
class FetchResult:
    page: int
    kind: str
    payload: bytes = b""
    detail: str = ""


def process_url(
    url: str, page: int = 1, chapter: int = 0, *, file_path: str | None = None
) -> str:
    """Build a page URL from the URL captured by Playwright."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("A non-empty image URL is required.")
    if page < 1:
        raise ValueError("page must be at least 1.")
    if not 0 <= chapter <= 99:
        raise ValueError("chapter must be between 0 and 99.")

    if file_path is not None:
        # Preserve all other query parameters and encode the API path exactly once.
        result, count = re.subn(
            r"([?&]filePath=)[^&#]*",
            lambda match: match.group(1) + quote(file_path, safe=""),
            url,
        )
        if count != 1:
            raise ValueError("Expected exactly one filePath query parameter.")
        return result

    captured_path = parse_qs(urlsplit(url).query).get("filePath", [""])[0]
    if re.match(r"^/[0-9a-fA-F]{32}/", captured_path):
        raise ValueError("Signed image paths require the page's exact API hfsKey.")

    result, page_replacements = re.subn(
        r"\d+(?=\.jpg)", str(page), url, count=1, flags=re.IGNORECASE
    )
    if page_replacements != 1:
        raise ValueError("The captured URL does not contain a JPEG page number.")

    chapter_str = f"{chapter:02d}"
    result, chapter_replacements = re.subn(
        r"%2F(\d+)%2Ffiles",
        lambda match: f"%2F{match.group(1)[:-2]}{chapter_str}%2Ffiles",
        result,
        count=1,
        flags=re.IGNORECASE,
    )
    if chapter_replacements != 1:
        raise ValueError("The captured URL does not contain an encoded chapter path.")

    return result


def _build_session(cookie: str) -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=RETRYABLE_STATUSES,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Cookie": cookie})
    return session


class _ThreadSessionPool:
    """Give every worker its own requests.Session."""

    def __init__(self, cookie: str):
        self.cookie = cookie
        self.local = threading.local()
        self.sessions = []
        self.lock = threading.Lock()

    def get(self) -> requests.Session:
        session = getattr(self.local, "session", None)
        if session is None:
            session = _build_session(self.cookie)
            self.local.session = session
            with self.lock:
                self.sessions.append(session)
        return session

    def close(self) -> None:
        for session in self.sessions:
            close = getattr(session, "close", None)
            if close:
                close()


def _read_limited(response: requests.Response) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > MAX_IMAGE_BYTES:
            raise ValueError("Response is larger than the 50 MiB safety limit.")

    content = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        content.extend(chunk)
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError("Response is larger than the 50 MiB safety limit.")
    return bytes(content)


def _classify_payload(payload: bytes, content_type: str) -> str:
    stripped = payload.lstrip()
    normalized_type = content_type.partition(";")[0].strip().lower()

    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if normalized_type in {"application/json", "text/json"} or stripped.startswith(
        (b"{", b"[")
    ):
        return "json"
    if normalized_type == "text/html" or stripped[:32].lower().startswith(
        (b"<!doctype html", b"<html")
    ):
        return "html"
    return "unknown"


def _json_detail(payload: bytes) -> str:
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "服务器返回了非图片数据"

    if not isinstance(message, dict):
        return "服务器返回 JSON"
    details = {
        key: str(message[key])[:200]
        for key in ("code", "message", "msg", "error")
        if key in message
    }
    return f"服务器返回 JSON：{details}" if details else "服务器返回 JSON"


def _fetch_page(session: requests.Session, target_url: str, page: int) -> FetchResult:
    try:
        with session.get(
            target_url,
            timeout=(5, 30),
            stream=True,
            allow_redirects=True,
        ) as response:
            if response.status_code in (401, 403):
                return FetchResult(page, "auth", detail="登录已失效或请求被拒绝")
            if response.status_code in (404, 410):
                return FetchResult(page, "end")
            if response.status_code != 200:
                return FetchResult(page, "error", detail=f"状态码 {response.status_code}")

            payload = _read_limited(response)
            payload_kind = _classify_payload(
                payload, response.headers.get("Content-Type", "")
            )
    except (requests.RequestException, ValueError) as exc:
        return FetchResult(page, "error", detail=str(exc))

    if payload_kind == "jpeg":
        return FetchResult(page, "jpeg", payload=payload)
    if payload_kind == "json":
        return FetchResult(page, "end", detail=_json_detail(payload))
    if payload_kind == "html":
        return FetchResult(page, "auth", detail="收到登录页面，Cookie 可能已失效")
    return FetchResult(page, "error", detail="响应不是有效的 JPEG")


def _fetch_book_page(
    session_pool: _ThreadSessionPool,
    base_url: str,
    chapter: int,
    page: int,
) -> FetchResult:
    return _fetch_page(
        session_pool.get(), process_url(base_url, page, chapter), page
    )


def _write_atomically(file_path: Path, payload: bytes) -> None:
    partial_path = file_path.with_suffix(file_path.suffix + ".part")
    try:
        with partial_path.open("wb") as file:
            file.write(payload)
        os.replace(partial_path, file_path)
    finally:
        if partial_path.exists():
            partial_path.unlink()


def _download_manifest(info, output_dir, max_workers, request_delay):
    """Download the exact API page list; missing listed pages are failures."""
    page_paths = info["page_paths"]
    jobs = [
        (chapter, page, path)
        for chapter, pages in sorted(page_paths.items())
        for page, path in sorted(pages.items())
    ]
    if not jobs:
        raise ValueError("The chapter API returned no image paths.")
    output_dir.mkdir(parents=True, exist_ok=True)
    pool = _ThreadSessionPool(info["cookies"])

    def fetch(job):
        chapter, page, path = job
        url = process_url(info["url"], page, chapter, file_path=path)
        return _fetch_page(pool.get(), url, page)

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for start in range(0, len(jobs), max_workers):
                batch = jobs[start:start + max_workers]
                for (chapter, page, _), result in zip(batch, executor.map(fetch, batch)):
                    if result.kind != "jpeg":
                        print(f"第 {chapter} 章第 {page} 页下载失败：{result.detail or result.kind}")
                        return None
                    name = f"chapter_{chapter:02d}_page_{page:04d}.jpg"
                    _write_atomically(output_dir / name, result.payload)
                    print(f"已下载: {name}")
                if request_delay:
                    time.sleep(request_delay)
    finally:
        pool.close()
    return str(output_dir)


def download_image(
    info: Mapping[str, object] | None = None,
    save_path: str | os.PathLike[str] | None = None,
    *,
    max_chapters: int = 99,
    max_pages: int = 999,
    max_consecutive_empty_chapters: int = 3,
    max_workers: int = 6,
    request_delay: float = 0.1,
) -> str | None:
    """Download book pages concurrently and return the output directory."""
    if info is None:
        print("请通过 main.py 启动程序，以便浏览器在多本教材之间保持打开。")
        return None

    base_url = info.get("url", "")
    cookie = info.get("cookies", "")
    if not base_url:
        print("未捕获到图片 URL，下载已取消。")
        return None
    if not cookie:
        print("未捕获到登录 Cookie，下载已取消。")
        return None
    if not 1 <= max_chapters <= 100:
        raise ValueError("max_chapters must be between 1 and 100.")
    if max_pages < 1 or max_consecutive_empty_chapters < 1:
        raise ValueError("Download limits must all be positive integers.")
    if not 1 <= max_workers <= 32:
        raise ValueError("max_workers must be between 1 and 32.")
    if request_delay < 0:
        raise ValueError("request_delay cannot be negative.")

    output_dir = Path(save_path) if save_path else Path("outputs") / datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )
    if "page_paths" in info:
        return _download_manifest(info, output_dir, max_workers, request_delay)

    process_url(base_url, page=1, chapter=0)
    output_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    consecutive_empty_chapters = 0
    session_pool = _ThreadSessionPool(cookie)

    try:
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="book-page"
        ) as executor:
            for chapter in range(max_chapters):
                chapter_downloaded = 0
                chapter_finished = False

                for batch_start in range(1, max_pages + 1, max_workers):
                    batch_pages = range(
                        batch_start, min(batch_start + max_workers, max_pages + 1)
                    )
                    futures = [
                        executor.submit(
                            _fetch_book_page,
                            session_pool,
                            base_url,
                            chapter,
                            page,
                        )
                        for page in batch_pages
                    ]
                    results = sorted(
                        (future.result() for future in futures), key=lambda result: result.page
                    )

                    for result in results:
                        file_name = f"chapter_{chapter:02d}_page_{result.page:04d}.jpg"
                        if result.kind == "end":
                            suffix = f"（{result.detail}）" if result.detail else ""
                            print(f"第 {chapter} 章在第 {result.page} 页结束{suffix}")
                            chapter_finished = True
                            break
                        if result.kind in {"auth", "error"}:
                            print(
                                f"{file_name} 下载失败：{result.detail}，下载已停止。"
                            )
                            return None

                        try:
                            _write_atomically(output_dir / file_name, result.payload)
                        except OSError as exc:
                            print(f"{file_name} 写入失败：{exc}，下载已停止。")
                            return None

                        downloaded += 1
                        chapter_downloaded += 1
                        print(f"已下载: {file_name}")

                    if chapter_finished:
                        break
                    if request_delay:
                        time.sleep(request_delay)

                if chapter_downloaded:
                    consecutive_empty_chapters = 0
                else:
                    consecutive_empty_chapters += 1
                    if consecutive_empty_chapters >= max_consecutive_empty_chapters:
                        print(
                            f"连续 {consecutive_empty_chapters} 个章节无图片，停止继续探测。"
                        )
                        break
    finally:
        session_pool.close()

    print(f"任务完成：共下载 {downloaded} 张图片到 {output_dir}")
    return str(output_dir) if downloaded else None


if __name__ == "__main__":
    print("请从项目目录运行：python main.py")
