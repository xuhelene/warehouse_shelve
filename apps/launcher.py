# 文件说明：打包版桌面启动入口——选端口、起本地服务、自动打开浏览器，并保持控制台窗口给非技术使用者看提示。
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn

from apps.web_portal import app
from core.paths import RUNTIME_ROOT

HOST = "127.0.0.1"
PREFERRED_PORTS = (8000, 8010, 8080, 8088, 9000)


def pick_port() -> int:
    """优先用 8000，被占用就顺延；都不行则让系统随便给一个空闲端口。"""
    for port in PREFERRED_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((HOST, port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def open_browser_when_ready(url: str, port: int, timeout_s: float = 30.0) -> None:
    """等服务真正监听上再打开浏览器，避免用户看到「无法访问」。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((HOST, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.3)


def main() -> None:
    port = pick_port()
    url = f"http://{HOST}:{port}/"

    print("=" * 62)
    print("  仓储优化演示系统")
    print("=" * 62)
    print(f"  演示页面：{url}")
    print(f"  结果目录：{RUNTIME_ROOT / 'results'}")
    print()
    print("  浏览器会自动打开；如果没有，请手动复制上面的地址到浏览器。")
    print("  演示结束前请勿关闭本窗口，关闭即停止服务。")
    print("=" * 62, flush=True)
    print()

    if "--no-browser" not in sys.argv:  # 打包后冒烟测试用，不弹浏览器
        threading.Thread(target=open_browser_when_ready, args=(url, port), daemon=True).start()
    try:
        uvicorn.run(app, host=HOST, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback

        traceback.print_exc()
        input("\n服务启动失败，请把上面的错误信息发给技术同事。按回车键关闭...")


if __name__ == "__main__":
    main()
