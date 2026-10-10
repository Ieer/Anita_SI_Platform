import os

def integrate_python_files(directory, output_file, identifier='---'):
    # 确保输出文件是空的
    open(output_file, 'w', encoding='utf-8').close()

    # 遍历目录
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                # 构造文件的绝对路径
                full_path = os.path.join(root, file)

                # 获取文件的相对路径
                relative_path = os.path.relpath(full_path, directory)
                
                # 读取文件内容
                with open(full_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()

                # 写入整合文件
                with open(output_file, 'a', encoding='utf-8') as out_f:
                    # 标识符和文件的路径信息
                    out_f.write(f"{identifier} {relative_path}\n")
                    out_f.write(file_content)
                    out_f.write("\n")  # 添加一个行进行分隔


if __name__ == "__main__":
	# 调用函数进行整合
	directory_path = 'E:\\Smart-RPA-Platform\\src'  # 指定的根目录路径
	output_file_path = 'integrated_code.txt'  # 最终的输出路径
	integrate_python_files(directory_path, output_file_path, identifier='*******')
