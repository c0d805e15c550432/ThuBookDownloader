import os
from PIL import Image

# 1. 配置路径
input_folder = "screenshots"     # 原始截图存放文件夹
output_folder = "cropped_pages"   # 裁剪后图片存放文件夹

# 2. 定义裁剪范围 (left, upper, right, lower)
# 为了包含 1377 和 1934 像素点，右/下边界需要 +1
crop_box = (113, 172, 1377 + 1, 1934 + 1)

# 创建输出目录
if not os.path.exists(output_folder):
    os.makedirs(output_folder)

def process_images():
    # 获取文件夹下所有 png 文件并排序
    files = [f for f in os.listdir(input_folder) if f.endswith('.png')]
    files.sort()
    
    total = len(files)
    print(f"找到 {total} 张图片，开始裁剪...")

    for index, filename in enumerate(files, 1):
        img_path = os.path.join(input_folder, filename)
        
        with Image.open(img_path) as img:
            # 执行裁剪
            cropped_img = img.crop(crop_box)
            
            # 保存到新文件夹，保持原文件名
            save_path = os.path.join(output_folder, filename)
            cropped_img.save(save_path)
            
        if index % 50 == 0 or index == total:
            print(f"进度: {index}/{total} 已处理")

    print(f"全部处理完成！裁剪后的图片已保存至: {output_folder}")

if __name__ == "__main__":
    process_images()