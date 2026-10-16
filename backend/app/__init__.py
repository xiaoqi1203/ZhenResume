"""ZhenResume（简甄）后端应用包。"""

from .logging_setup import setup_logging

# 任何入口点（uvicorn / scripts/*）只要 import 了 app 包，日志就是通的
setup_logging()

__version__ = "1.0.0"
