"""把 `services/web_corpus.py` 的外部权威语料灌入知识库（可重复运行、可回滚）。

## 为什么需要单独脚本

`seed_knowledge.py` 走的是 `seed_if_empty`（库为空才灌），而本模块的语料要**增量追加**：
- 已有 11 篇自撰规范不能动；
- 外部语料按 `source_url` 判重，重复运行不会产生副本；
- `--force` 只清掉**本模块**灌的文档（按 source_url 识别），不碰自撰规范。

## 元数据约定（让每篇都能被追问"出处"）

`doc_metadata` 里写入：
```json
{"source": "web", "source_url": "...", "publisher": "...", "fetched_at": "2026-10-21",
 "seeded": true}
```
报告页的"知识库依据"卡片可以据此展示出处，而不是只显示一个标题。

## 用法（在 backend 目录下）

```bash
python -m scripts.seed_web_corpus --dry-run   # 只看会写入什么
python -m scripts.seed_web_corpus             # 增量灌入（已存在的跳过）
python -m scripts.seed_web_corpus --force     # 重建本模块全部文档
```
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models import KnowledgeChunk, KnowledgeDoc  # noqa: E402
from app.services import rag  # noqa: E402
from app.services.web_corpus import FETCHED_AT, WEB_DOCS  # noqa: E402


def _counts(db) -> tuple[int, int]:
    from sqlalchemy import func

    docs = db.execute(select(func.count()).select_from(KnowledgeDoc)).scalar_one()
    chunks = db.execute(select(func.count()).select_from(KnowledgeChunk)).scalar_one()
    return int(docs), int(chunks)


def _existing_by_url(db) -> dict[str, KnowledgeDoc]:
    """已灌入的本模块文档：按 source_url 索引。"""
    rows = db.execute(select(KnowledgeDoc)).scalars().all()
    out: dict[str, KnowledgeDoc] = {}
    for doc in rows:
        meta = doc.doc_metadata or {}
        if meta.get("source") == "web" and meta.get("source_url"):
            out[str(meta["source_url"])] = doc
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="删除本模块已有文档后重建")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    args = parser.parse_args()

    with session_scope() as db:
        before_docs, before_chunks = _counts(db)
        existing = _existing_by_url(db)
        print(f"[灌入前] knowledge_docs={before_docs}  knowledge_chunks={before_chunks}")
        print(f"[已有外部语料] {len(existing)} 篇（按 source_url 识别）")

        if args.force and existing:
            for doc in existing.values():
                db.delete(doc)  # chunks 随 cascade 删除
            db.flush()
            print(f"[--force] 已删除本模块旧文档 {len(existing)} 篇")
            existing = {}

        added = skipped = 0
        for spec in WEB_DOCS:
            url = spec["source_url"]
            if url in existing:
                skipped += 1
                print(f"  [SKIP] {spec['title'][:44]}")
                continue
            if args.dry_run:
                print(f"  [WOULD ADD] [{spec['scope']}] {spec['title'][:44]}")
                added += 1
                continue
            doc = KnowledgeDoc(
                scope=spec["scope"],
                role_family=spec.get("role_family"),
                doc_type=spec.get("doc_type"),
                title=spec["title"],
                content=spec["content"],
                doc_metadata={
                    "seeded": True,
                    "source": "web",
                    "source_url": url,
                    "publisher": spec.get("publisher") or "",
                    "fetched_at": FETCHED_AT,
                },
            )
            db.add(doc)
            db.flush()
            n_chunks = rag.index_document(db, doc)
            added += 1
            print(f"  [ADD] [{spec['scope']}] {spec['title'][:44]} → {n_chunks} 切片")

        if args.dry_run:
            print(f"\n[DRY-RUN] 将新增 {added} 篇，跳过 {skipped} 篇（未做任何写入）")
            return 0

        db.commit()
        after_docs, after_chunks = _counts(db)

    print(f"\n[灌入后] knowledge_docs={after_docs}  knowledge_chunks={after_chunks}")
    print(f"新增 {added} 篇，跳过 {skipped} 篇；切片 {before_chunks} → {after_chunks}")
    if after_chunks <= before_chunks and added:
        print("✗ 新增了文档但切片数没增加 —— 检查 index_document 是否失败")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
