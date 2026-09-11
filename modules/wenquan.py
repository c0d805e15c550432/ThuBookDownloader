import os
import time
import random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

driver = webdriver.Chrome()
driver.get("https://lib-tsinghua.wqxuetang.com/deep/read/pdf?bid=3256239")

time.sleep(60)  # 等待页面加载完成
# 创建截图存放文件夹
if not os.path.exists("screenshots"):
    os.makedirs("screenshots")

TOTAL_PAGES = 368
wait = WebDriverWait(driver, 10)

try:
    for page in range(362, TOTAL_PAGES + 1):
        print(f"正在跳转至第 {page} 页...")

        # 1. 点击页码显示区域（激活输入框）
        # 使用 class 定位那个 "5 / 368" 的元素
        page_head = wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "page-head-tol")))
        page_head.click()

        # 2. 定位输入框并输入页数
        # 根据你提供的 HTML，输入框 ID 是 "input"
        page_input = wait.until(EC.presence_of_element_located((By.ID, "input")))
        
        # 清除原有内容并输入新页码
        # page_input.send_keys(Keys.CONTROL + "a") # 全选
        # page_input.send_keys(Keys.BACKSPACE)    # 删除
        page_input.send_keys(str(page))         # 输入目标页
        
        # 3. 模拟回车键执行跳转
        page_input.send_keys(Keys.ENTER)

        # 4. 等待页面加载（关键：跳转后需要时间渲染图片）
        # 建议等待局部容器内的图片 src 发生变化或直接硬等待
        time.sleep(random.uniform(1.5, 2.5)) 

        # 5. 截图保存
        file_path = f"screenshots/page_{page:03d}.png"
        driver.save_screenshot(file_path)
        time.sleep(2)
        
        print(f"已完成第 {page} 页截图")

    print("--- 368页任务全部完成 ---")

finally:
    pass
