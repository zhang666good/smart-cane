"""设备端启动入口.

部署到 EC200U 后, 用 QPYcom 将 client 目录内容置于 /usr 下, 运行本文件.
若直接置于 /usr (非子目录), 请去掉本文件内对包名 client. 的引用, 或保持 /usr/client/ 结构,
并在 QPYcom 中执行 /usr/client/app_main.py。
"""
from client.app_main import main

if __name__ == "__main__":
    main()
