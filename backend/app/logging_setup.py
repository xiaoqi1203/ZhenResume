"""日志初始化（供任何入口点共用：uvicorn、scripts/*、将来接入的其他进程）。

为什么单独一个模块：
`logging.basicConfig` 原先只写在 `app/main.py` 里，于是**只有通过 FastAPI 启动时**才生效。
`scripts/inspect.py` / `scripts/verify.py` 这些离线入口不 import `main`，
结果 LLM 层的 INFO 日志（哪个模型、多久、多少 token）全部被丢掉 ——
排查"live 模式为什么这么慢"时，正是最需要这些日志的时候。
放在 `app/__init__.py` 里调用，保证只要 import 了 app 包就有日志。
"""

from __future__ import annotations

import logging

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _configured = True
