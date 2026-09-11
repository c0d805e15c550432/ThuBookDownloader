# Tsinghua University Electronic Course Reserves Service Platform Downloader

[中文说明](README.zh-CN.md)

Download JPEG textbook pages from the Tsinghua library e-reserves reader and combine them into a PDF with chapter bookmarks. Sign in through the browser and select a book; a confirmation dialog adds it to a background queue.

## Setup

Requires Python 3.10 or newer. Microsoft Edge is preferred when installed; otherwise install Playwright Chromium. Example for PowerShell:

```powershell
Set-Location S:\Projects\get_book
python -m venv S:\Projects\get_book\.venv
S:\Projects\get_book\.venv\Scripts\python.exe -m pip install -r S:\Projects\get_book\requirements.txt
# Only needed if Microsoft Edge is unavailable:
S:\Projects\get_book\.venv\Scripts\python.exe -m playwright install chromium
S:\Projects\get_book\.venv\Scripts\python.exe S:\Projects\get_book\main.py
```

## Usage

1. Sign in, search for a textbook, and open its reader.
2. Choose **Confirm** to queue the captured book, or **Cancel** to skip it.
3. Continue selecting books while the background worker downloads and converts the queue.
4. Close all browser tabs to stop selecting books. The program waits for queued work to finish.

Every launch creates a fresh browser context without saved cookies or storage. Sign in again each run. Tabs share the current session until exit; no project `Userdata` profile is created.

PDFs are saved to `outputs/<book title>.pdf`; name collisions receive a timestamp. Intermediate images live in a timestamped subfolder and are deleted only after successful conversion. Failed downloads or conversions retain downloaded images. Pages are sorted numerically, JPEGs are recompressed at quality 75 without resizing, and available chapter labels become bookmarks.
