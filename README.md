# 清华大学电子教学参考书服务平台下载器


从清华大学图书馆电子教参阅读器下载教材，并合成为带章节书签的 PDF。通过浏览器登录并选择教材，确认后加入后台处理队列。

## 安装与启动

需要 Python 3.10 或以上版本。优先使用已安装的 Microsoft Edge；没有 Edge 时安装 Playwright Chromium。

### 安装依赖
```powershell
pip install -r S:\Projects\get_book\requirements.txt
```

### 仅未安装 Microsoft Edge 时需要：
```powershell
playwright install chromium
```
### 启动
```powershell
S:\Projects\get_book\.venv\Scripts\python.exe S:\Projects\get_book\main.py
```
或双击start.bat（仅Windows）


## 使用方法

1. 在打开的浏览器中登录，搜索教材并进入阅读页面。
2. 点击 **Confirm** 加入下载队列，或点击 **Cancel** 跳过。
3. 后台下载、转换期间可以继续选择其他教材。
4. 关闭全部标签页后停止选择教材，程序会等待已加入队列的任务完成。

每次启动均创建空白浏览器上下文，不加载已保存的 Cookie 或本地存储，因此每次运行需要重新登录。同一次运行中的标签页共享当前会话；项目不会再创建 `Userdata` 用户目录。

PDF 保存至 `outputs/<书名>.pdf`，重名时添加时间戳。中间图片保存在时间戳子目录中，仅在转换成功后删除；下载或转换失败时保留已有图片。图片按章节、页码进行数字排序，JPEG 按质量 75 重新压缩但不缩小像素尺寸，并使用捕获到的章节名称生成书签。



## 注意
平台中部分书籍受文泉书局版权保护，不支持下载。

