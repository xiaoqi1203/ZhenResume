"""知识库种子数据灌入脚本。

用法（在 backend 目录下）：
    python -m scripts.seed_knowledge           # 仅在知识库为空时写入
    python -m scripts.seed_knowledge --force   # 清空并重建
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, session_scope  # noqa: E402
from app.services.seed import seed_if_empty  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="灌入 ZhenResume 知识库种子数据")
    parser.add_argument("--force", action="store_true", help="清空现有知识库并重建")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        created = seed_if_empty(db, force=args.force)

    if created:
        print(f"[OK] 已写入 {created} 篇知识库文档（已切片并生成向量）")
    else:
        print("[SKIP] 知识库已有数据，未做改动（需要重建请加 --force）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
