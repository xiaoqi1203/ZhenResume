"""清理 `scripts/verify.py` 每次运行残留的知识库「验证用文档」。

背景：`scripts/verify.py` 的「7. 知识库 RAG」小节会 `POST /api/v1/knowledge/docs`
建一篇 39 字的测试文档（`role_family='test'`、`title='验证用文档'`），
早期版本跑完不删除 —— 于是每跑一次 verify，知识库就多一篇垃圾文档、多一个切片，
`eval_rag` 依赖的「11 篇种子文档 / 16 个切片」基线被悄悄污染。

用法（在 backend 目录下）：
    python -m scripts.purge_test_kb --dry-run   # 只统计，不删除
    python -m scripts.purge_test_kb             # 真的删除

判定口径（任一命中即删，宁可只删「验证用文档」也不误伤种子/用户文档）：
    1. `knowledge_docs.role_family == 'test'`
    2. `knowledge_docs.title == '验证用文档'`

只删知识库文档及其切片，**不重建种子**（重建用 `scripts/seed_knowledge --force`，
那会清空整库，风险更高）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, or_, select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import KnowledgeChunk, KnowledgeDoc  # noqa: E402

# 与 scripts/verify.py 里创建测试文档用的字面量保持一致（那里改这里也要改）
TEST_ROLE_FAMILY = "test"
TEST_TITLE = "验证用文档"


def _counts(db) -> tuple[int, int]:
    """(文档数, 切片数) —— 直接 count，不受会话里已删除但未提交的行影响。"""
    docs = db.execute(select(func.count()).select_from(KnowledgeDoc)).scalar_one()
    chunks = db.execute(select(func.count()).select_from(KnowledgeChunk)).scalar_one()
    return int(docs), int(chunks)


def _test_docs(db) -> list[KnowledgeDoc]:
    stmt = select(KnowledgeDoc).where(
        or_(KnowledgeDoc.role_family == TEST_ROLE_FAMILY, KnowledgeDoc.title == TEST_TITLE)
    )
    return list(db.execute(stmt.order_by(KnowledgeDoc.created_at)).scalars().all())


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 verify.py 残留的知识库验证用文档")
    parser.add_argument("--dry-run", action="store_true", help="只打印将被删除的文档，不实际删除")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        docs_before, chunks_before = _counts(db)
        targets = _test_docs(db)

        print(f"[清理前] knowledge_docs={docs_before}  knowledge_chunks={chunks_before}")
        print(f"[待删除] {len(targets)} 篇（role_family='{TEST_ROLE_FAMILY}' 或 title='{TEST_TITLE}'）")
        for doc in targets:
            print(
                f"  - {doc.id}  title={doc.title!r}  role_family={doc.role_family!r}  "
                f"scope={doc.scope!r}  created_at={doc.created_at}"
            )

        if not targets:
            print("[SKIP] 没有需要清理的测试文档，知识库已是干净状态")
            return 0

        if args.dry_run:
            print("[DRY-RUN] 未做任何删除（去掉 --dry-run 即执行）")
            return 0

        # 走 ORM 删除：KnowledgeDoc.chunks 是 cascade="all, delete-orphan"，切片会跟着删；
        # SQLite 侧还有 FK ON DELETE CASCADE 兜底。不新增任何 API 端点。
        for doc in targets:
            db.delete(doc)
        db.flush()

        docs_after, chunks_after = _counts(db)
        print(
            f"[已删除] {len(targets)} 篇文档、{chunks_before - chunks_after} 个切片"
        )
        print(f"[清理后] knowledge_docs={docs_after}  knowledge_chunks={chunks_after}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
