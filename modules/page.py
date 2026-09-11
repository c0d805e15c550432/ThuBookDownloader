import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


LOGIN_URL = "https://ereserves.lib.tsinghua.edu.cn/login"
CHAPTERS_ENDPOINT = "/readkernel/KernelAPI/BookInfo/selectJgpBookChapters"
CHAPTER_ENDPOINT = "/readkernel/KernelAPI/BookInfo/selectJgpBookChapter"
CONFIRM_BOOK_SCRIPT = """
async (title) => {
    document.getElementById("get-book-confirm-dialog")?.remove();
    return await new Promise((resolve) => {
        const overlay = document.createElement("div");
        overlay.id = "get-book-confirm-dialog";
        overlay.style.cssText = "position:fixed;inset:0;z-index:2147483647;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.45)";
        const panel = document.createElement("div");
        panel.style.cssText = "width:min(440px,calc(100vw - 40px));padding:24px;border-radius:12px;background:white;color:#1f2937;box-shadow:0 20px 50px rgba(0,0,0,.3);font-family:sans-serif";
        const heading = document.createElement("h2");
        heading.textContent = "Confirm captured book";
        heading.style.margin = "0 0 12px";
        const message = document.createElement("p");
        message.textContent = `Add “${title}” to the download queue?`;
        message.style.cssText = "margin:0 0 20px;line-height:1.5;word-break:break-word";
        const actions = document.createElement("div");
        actions.style.cssText = "display:flex;justify-content:flex-end;gap:10px";
        const cancel = document.createElement("button");
        cancel.textContent = "Cancel";
        const confirm = document.createElement("button");
        confirm.textContent = "Confirm";
        for (const button of [cancel, confirm]) {
            button.style.cssText = "padding:9px 18px;border-radius:7px;border:1px solid #bbb;cursor:pointer";
        }
        confirm.style.cssText += ";background:#2563eb;color:white;border-color:#2563eb";
        const finish = (accepted) => { overlay.remove(); resolve(accepted); };
        cancel.onclick = () => finish(false);
        confirm.onclick = () => finish(true);
        actions.append(cancel, confirm);
        panel.append(heading, message, actions);
        overlay.append(panel);
        document.body.append(overlay);
        confirm.focus();
    });
}
"""


def find_microsoft_edge() -> Path | None:
    """Return the installed Microsoft Edge executable, when available."""
    executable = shutil.which("msedge") or shutil.which("microsoft-edge")
    if executable:
        return Path(executable)

    if os.name != "nt":
        executable = shutil.which("microsoft-edge-stable")
        return Path(executable) if executable else None

    candidates = []
    for environment_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(environment_name)
        if root:
            candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")

    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _extract_title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else "Unknown Title"


def _normalize_book_path(path: str) -> str:
    path = path.replace("\\", "/").split("/files", maxsplit=1)[0]
    path = re.sub(r"\d{2}$", "", path)
    match = re.search(r"(book\d+/.*)", path, re.IGNORECASE)
    return (match.group(1) if match else path.lstrip("/")).lower()


def _book_key(url: str) -> str:
    """Normalize a reader URL so different pages of one book share a key."""
    file_path = parse_qs(urlsplit(url).query).get("filePath", [""])[0]
    if file_path:
        return _normalize_book_path(file_path)

    normalized = re.sub(r"\d+(?=\.jpg)", "{page}", url, flags=re.IGNORECASE)
    return re.sub(
        r"(%2F\d*)\d{2}(%2Ffiles)",
        r"\1{chapter}\2",
        normalized,
        flags=re.IGNORECASE,
    )


def _parse_chapter_response(payload: object) -> tuple[str, dict[int, str]]:
    """Extract a normalized book key and chapter labels from the API payload."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Chapter response does not contain a data list.")

    book_key = ""
    chapters = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue

        fragment_url = item.get("EFRAGMENTURL")
        if not book_key and isinstance(fragment_url, str) and fragment_url:
            book_key = _normalize_book_path(fragment_url)

        try:
            chapter_number = int(item["SORT"])
        except (KeyError, TypeError, ValueError):
            continue

        chapter_name = str(item.get("EFRAGMENTNAME") or "").strip()
        if chapter_number >= 0 and chapter_name:
            chapters[chapter_number] = chapter_name

    if not chapters:
        raise ValueError("Chapter response does not contain named chapters.")
    return book_key, dict(sorted(chapters.items()))


class BookBrowser:
    """Keep one browser open while the user selects multiple books."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self.context = None
        self.page = None
        self._pending_info = None
        self._latest_title = "Unknown Title"
        self._last_book_key = None
        self._chapter_book_key = ""
        self._latest_chapters = {}
        self._chapter_records = {}
        self._chapter_details = {}

    def __enter__(self):
        self._playwright = sync_playwright().start()
        edge_path = find_microsoft_edge()
        launch_options = {
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        }

        if edge_path:
            launch_options["channel"] = "msedge"
            print(f"检测到 Microsoft Edge，正在使用 Edge：{edge_path}")
        else:
            print("未检测到 Microsoft Edge，正在使用 Playwright Chromium。")

        try:
            self._browser = self._playwright.chromium.launch(**launch_options)
            # A new, nonpersistent context starts without cookies or storage.
            self.context = self._browser.new_context(no_viewport=True)
        except PlaywrightError as exc:
            self.__exit__(None, None, None)
            if not edge_path and "executable doesn't exist" in str(exc).lower():
                raise RuntimeError(
                    "未安装 Playwright Chromium。请运行：playwright install chromium"
                ) from exc
            raise

        self.context.on("response", self._on_response)
        self.context.on("page", self._on_page)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        print("正在打开登录页面...")
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("请登录本次新会话，搜索教材并点击“开始阅读”。")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.context is not None:
            try:
                self.context.close()
            except PlaywrightError:
                pass
            self.context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except PlaywrightError:
                pass
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _on_page(self, new_page):
        self.page = new_page
        print("检测到新页面打开。")

    def _open_pages(self) -> list:
        try:
            return [page for page in self.context.pages if not page.is_closed()]
        except PlaywrightError:
            return []

    def _pump_events_once(self) -> bool:
        """Pump events, switching tabs when only the current tab closes."""
        open_pages = self._open_pages()
        if not open_pages:
            return False

        preferred_pages = []
        if self.page in open_pages:
            preferred_pages.append(self.page)
        preferred_pages.extend(
            page for page in reversed(open_pages) if page not in preferred_pages
        )

        for candidate in preferred_pages:
            try:
                candidate.wait_for_timeout(500)
                self.page = candidate
                return True
            except PlaywrightError:
                # This tab may have closed while other tabs remain usable.
                continue
        return bool(self._open_pages())

    def _confirm_book(self, title: str) -> bool | None:
        """Show a modal confirmation; None means every tab has closed."""
        while True:
            open_pages = self._open_pages()
            if not open_pages:
                return None

            for candidate in reversed(open_pages):
                try:
                    accepted = candidate.evaluate(CONFIRM_BOOK_SCRIPT, title)
                    self.page = candidate
                    return bool(accepted)
                except PlaywrightError:
                    # The dialog tab closed; retry on another open tab.
                    continue

    def _on_response(self, response):
        if (response.request.method == "POST"
                and urlsplit(response.url).path.endswith(CHAPTER_ENDPOINT)):
            if response.status == 200:
                try:
                    data = response.json()["data"]
                    key = _normalize_book_path(data["EFRAGMENTURL"])
                    form = parse_qs(response.request.post_data or "")
                    self._chapter_details.setdefault(key, {})[str(data["EMID"])] = (
                        data, response.url, {name: values[0] for name, values in form.items()}
                    )
                except (PlaywrightError, ValueError, KeyError, TypeError):
                    pass
            return

        if (
            response.request.method == "POST"
            and urlsplit(response.url).path.endswith(CHAPTERS_ENDPOINT)
        ):
            if response.status == 200:
                try:
                    payload = response.json()
                    (
                        self._chapter_book_key,
                        self._latest_chapters,
                    ) = _parse_chapter_response(payload)
                    self._chapter_records[self._chapter_book_key] = payload["data"]
                    print(f"已捕获 {len(self._latest_chapters)} 个章节名称。")
                except (PlaywrightError, ValueError):
                    self._chapter_book_key = ""
                    self._latest_chapters = {}
            return

        if response.request.method != "GET":
            return

        if "JPGJsNetPage" in response.url and "DownJPGJsNetPage" not in response.url:
            if response.status == 200:
                try:
                    self._latest_title = _extract_title(response.text())
                except PlaywrightError:
                    self._latest_title = "Unknown Title"
            return

        if "DownJPGJsNetPage" not in response.url or response.status != 200:
            return

        book_key = _book_key(response.url)
        if self._pending_info is not None or book_key == self._last_book_key:
            return

        botu_cookie = next(
            (
                cookie
                for cookie in self.context.cookies()
                if cookie.get("name") == "BotuReadKernel"
            ),
            None,
        )
        self._pending_info = {
            "url": response.url,
            "cookies": (
                f"BotuReadKernel={botu_cookie.get('value', '')}"
                if botu_cookie
                else ""
            ),
            "title": self._latest_title,
            "book_key": book_key,
            "chapters": (
                self._latest_chapters.copy()
                if self._chapter_book_key == book_key
                else {}
            ),
        }
        print(f"已捕获教材：{self._latest_title}")

    def _collect_page_paths(self, book_key):
        """Resolve every chapter through the same API used by the reader."""
        records = self._chapter_records.get(book_key, [])
        details = self._chapter_details.get(book_key, {})
        if not records or not details:
            raise ValueError("未捕获完整章节 API 数据，请重新打开教材。")
        _, endpoint, template = next(iter(details.values()))
        paths = {}
        for record in records:
            chapter = int(record["SORT"])
            emid = str(record["EMID"])
            if emid in details:
                data = details[emid][0]
            else:
                form = dict(template, EMID=emid)
                response = self.context.request.post(endpoint, form=form)
                try:
                    if response.status != 200:
                        raise ValueError(f"章节 API 请求失败：HTTP {response.status}")
                    data = response.json()["data"]
                finally:
                    response.dispose()
            if str(data["EMID"]) != emid:
                raise ValueError("章节 API 返回了不匹配的章节。")
            pages = {}
            for item in data["JGPS"]:
                match = re.fullmatch(r"(\d+)\.jpg", item["fileName"], re.IGNORECASE)
                if not match or not isinstance(item.get("hfsKey"), str):
                    raise ValueError("章节 API 返回了无效的图片路径。")
                number = int(match.group(1))
                if number < 1 or number in pages:
                    raise ValueError("章节 API 返回了无效或重复的页码。")
                pages[number] = item["hfsKey"]
            if not pages or chapter in paths:
                raise ValueError("章节 API 返回了空章节或重复章节。")
            paths[chapter] = pages
        return paths

    def wait_for_book(self, timeout_seconds: float | None = None) -> dict | None:
        """Wait for a newly selected book without closing the browser."""
        started_at = time.monotonic()
        print("等待选择教材；关闭浏览器或按 Ctrl+C 可结束程序。")

        while True:
            while self._pending_info is None:
                if not self._pump_events_once():
                    print("所有标签页或浏览器窗口已关闭。")
                    return None

                if (
                    timeout_seconds is not None
                    and time.monotonic() - started_at > timeout_seconds
                ):
                    print("等待超时，未捕获到教材请求。")
                    return None

            result = self._pending_info
            accepted = self._confirm_book(result["title"])
            self._last_book_key = result["book_key"]
            self._pending_info = None

            if accepted is None:
                print("所有标签页或浏览器窗口已关闭。")
                return None
            if not accepted:
                print(f"已取消下载：{result['title']}")
                continue

            try:
                page_paths = self._collect_page_paths(result["book_key"])
            except (PlaywrightError, ValueError, KeyError, TypeError) as exc:
                print(f"无法获取教材图片列表：{exc}")
                self._last_book_key = None
                continue
            return {
                "page_paths": page_paths,
                **{
                key: result[key]
                for key in ("url", "cookies", "title", "chapters")
                },
            }


def get_info() -> dict:
    """Compatibility helper for callers that only need one book."""
    with BookBrowser() as browser:
        return browser.wait_for_book(timeout_seconds=300) or {
            "url": "",
            "cookies": "",
            "title": "",
            "chapters": {},
        }


if __name__ == "__main__":
    result = get_info()
    if result["url"]:
        print("\n--- 抓取成功 ---")
        print("书名:", result["title"])
    else:
        print("\n--- 抓取失败 ---")
