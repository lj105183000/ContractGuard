"""桌面端打包入口：由 Electron 拉起，只绑定本机回环地址。

开发模式仍然使用 `uvicorn app.main:app`，这个文件仅用于 PyInstaller。
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="ContractGuard desktop backend")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, required=True, help="监听端口")
    parser.add_argument("--log-level", default="info", help="uvicorn 日志级别")
    args = parser.parse_args()

    # 延后导入，保证 --help 不加载知识库与模型配置。
    import uvicorn

    from app.main import app

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
