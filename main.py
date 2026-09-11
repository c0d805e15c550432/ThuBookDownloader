import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from modules.get_jpg import download_image
from modules.images_to_pdf import IMAGE_NAME_RE, images_to_pdf
from modules.page import BookBrowser


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "outputs"


def _safe_pdf_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")
    if name.lower().endswith(".pdf"):
        name = name[:-4].rstrip(" .")
    return (name or "Unknown Book")[:120]


def _available_pdf_path(title: str) -> Path:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stem = _safe_pdf_name(title)
    candidate = OUTPUT_ROOT / f"{stem}.pdf"
    if not candidate.exists():
        return candidate
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return OUTPUT_ROOT / f"{stem}_{timestamp}.pdf"


def _new_download_directory() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return OUTPUT_ROOT / timestamp


def delete_downloaded_images(image_directory: str | Path) -> int:
    """Delete only recognized download images from one generated output folder."""
    directory = Path(image_directory).resolve()
    output_root = OUTPUT_ROOT.resolve()
    if directory.parent != output_root:
        raise ValueError(f"Refusing to delete images outside {output_root}")

    deleted = 0
    for path in directory.iterdir():
        if path.is_file() and IMAGE_NAME_RE.match(path.name):
            path.unlink()
            deleted += 1

    if not any(directory.iterdir()):
        directory.rmdir()
    return deleted


def process_book(info: dict) -> None:
    """Download and convert one book on the background worker."""
    title = info.get("title", "Unknown Book")
    print(f"开始后台处理：{title}")
    download_directory = _new_download_directory()

    try:
        downloaded_path = download_image(
            info=info,
            save_path=download_directory,
        )
    except Exception as exc:
        print(f"《{title}》下载失败：{exc}")
        return

    if downloaded_path is None:
        print(f"《{title}》下载未完成。")
        return

    pdf_path = _available_pdf_path(title)
    try:
        images_to_pdf(
            downloaded_path,
            pdf_path,
            chapter_names=info.get("chapters", {}),
        )
    except Exception as exc:
        print(f"《{title}》PDF 生成失败：{exc}")
        print(f"原始图片已保留在：{downloaded_path}")
        return

    try:
        deleted = delete_downloaded_images(downloaded_path)
    except (OSError, ValueError) as exc:
        print(f"PDF 已生成，但清理《{title}》的原始图片失败：{exc}")
    else:
        print(f"PDF 已生成：{pdf_path}")
        print(f"已删除 {deleted} 张原始图片。")


def main() -> None:
    try:
        with BookBrowser() as browser, ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="book-pipeline"
        ) as pipeline:
            while True:
                info = browser.wait_for_book()
                if info is None:
                    break

                pipeline.submit(process_book, info.copy())
                print(
                    f"《{info.get('title', 'Unknown Book')}》已加入后台队列；"
                    "现在可以继续操作网站并选择下一本教材。"
                )

            print("浏览器已关闭，正在等待后台队列完成。")
    except RuntimeError as exc:
        print(exc)
    except KeyboardInterrupt:
        print("\n用户已结束程序。")


if __name__ == "__main__":
    main()
