"""LangGraph checkpoint 清理：TTL 过期回收 + 孤儿回收。

**为什么必须有这个脚本**：checkpoint 是"每个节点、每次状态变更都写一行"，
开着 `LANGGRAPH_CHECKPOINT=sqlite|postgres` 后它会无限增长 ——
一个诊断任务 8 个节点写十几行，几万份简历就是几十万行，
几个月后既占磁盘又拖慢 checkpoint 表的读写。而 **99% 的 checkpoint 在任务完成后再也不会被读**
（它只用于崩溃续跑与重放）。

清理两类：
1. **TTL 过期**：`analysis_tasks.created_at` 早于 N 天的任务 → 其 checkpoint 删除
   （业务上已归档，续跑无意义）；
2. **孤儿**：`thread_id` 在 `analysis_tasks` 里**不存在**的任务（任务被删了）→ 删除。
   `thread_id` 严格等于 `task_id`（见 workflow/graph.py 的 config），所以能精确对应。

用法（在 backend 目录下）：

    python -m scripts.prune_checkpoints                      # 干跑：只报告，不删（默认！）
    python -m scripts.prune_checkpoints --days 3 --apply      # 保留 3 天，真删
    python -m scripts.prune_checkpoints --sqlite /path/x.db --apply
    python -m scripts.prune_checkpoints --apply --force       # 跳过"全部是孤儿"的安全拦截

建议做成每日定时任务（VM 上）：

    # crontab -e
    30 4 * * * cd /home/xiaoqi/ZhenResume/backend && ./.venv/bin/python -m scripts.prune_checkpoints --days 7 --apply >> /home/xiaoqi/prune.log 2>&1
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

from app.config import DATA_DIR, settings  # noqa: E402

CHUNK = 500


def _checkpoint_engine():
    """返回 (engine, 描述)。checkpoint 可能在与业务库不同的 SQLite 文件里。"""
    mode = (settings.langgraph_checkpoint or "off").lower()
    if mode == "off":
        return None, "off"
    if mode == "sqlite":
        path = settings.langgraph_checkpoint_url or str(DATA_DIR / "zhenresume_checkpoints.db")
        if path.startswith("sqlite"):
            url = path
        else:
            url = f"sqlite+pysqlite:///{path}"
        return create_engine(url, future=True), f"sqlite:{path}"
    dsn = settings.langgraph_checkpoint_url or settings.database_url
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(dsn, future=True), "postgres"


def _checkpoint_tables(engine) -> list[str]:
    """动态发现哪些表带 thread_id 列（不同后端/版本的 checkpoint 表名不一样）。"""
    insp = inspect(engine)
    tables = []
    for name in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns(name)}
        if "thread_id" in cols:
            tables.append(name)
    return tables


def _all_thread_ids(engine, tables: list[str]) -> set[str]:
    ids: set[str] = set()
    with engine.connect() as conn:
        for table in tables:
            for (tid,) in conn.execute(text(f'SELECT DISTINCT thread_id FROM "{table}"')):
                if tid:
                    ids.add(str(tid))
    return ids


def _task_ages(ids: set[str]) -> dict[str, datetime]:
    """从业务库取这些 task_id 的创建时间；查不到的即孤儿。"""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import AnalysisTask

    out: dict[str, datetime] = {}
    items = sorted(ids)
    with session_scope() as db:
        for start in range(0, len(items), CHUNK):
            chunk = items[start : start + CHUNK]
            for tid, created in db.execute(
                select(AnalysisTask.id, AnalysisTask.created_at).where(AnalysisTask.id.in_(chunk))
            ):
                if created is not None:
                    out[str(tid)] = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
    return out


def _delete(engine, tables: list[str], ids: list[str]) -> int:
    deleted = 0
    with engine.begin() as conn:
        for table in tables:
            for start in range(0, len(ids), CHUNK):
                chunk = ids[start : start + CHUNK]
                stmt = text(f'DELETE FROM "{table}" WHERE thread_id IN ({",".join(f":i{i}" for i in range(len(chunk)))})')
                params = {f"i{i}": v for i, v in enumerate(chunk)}
                deleted += conn.execute(stmt, params).rowcount or 0
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="checkpoint TTL / 孤儿清理")
    parser.add_argument("--days", type=int, default=settings.langgraph_checkpoint_ttl_days,
                        help="保留最近 N 天的任务 checkpoint（默认取配置）")
    parser.add_argument("--apply", action="store_true", help="真的删除；不加则只报告（干跑）")
    parser.add_argument("--force", action="store_true", help='跳过"全部是孤儿"的安全拦截')
    parser.add_argument("--sqlite", default="", help="直接指定 checkpoint 的 sqlite 文件路径")
    args = parser.parse_args()

    if args.sqlite:
        settings.langgraph_checkpoint = "sqlite"
        settings.langgraph_checkpoint_url = args.sqlite

    engine, desc = _checkpoint_engine()
    if engine is None:
        print("LANGGRAPH_CHECKPOINT=off —— 没有 checkpoint 需要清理。")
        print("（要用崩溃续跑就设 LANGGRAPH_CHECKPOINT=sqlite|postgres，本脚本会自动适配）")
        return 0

    tables = _checkpoint_tables(engine)
    if not tables:
        print(f"checkpoint 后端 {desc}：没找到带 thread_id 的表（可能还没跑过带 checkpoint 的任务）。")
        return 0

    print(f"checkpoint 后端：{desc}")
    print(f"涉及表：{', '.join(tables)}")

    thread_ids = _all_thread_ids(engine, tables)
    print(f"checkpoint 里的任务数：{len(thread_ids)}")

    ages = _task_ages(thread_ids)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    orphans = sorted(t for t in thread_ids if t not in ages)
    expired = sorted(t for t in thread_ids if t in ages and ages[t] < cutoff)
    keep = len(thread_ids) - len(orphans) - len(expired)

    print(f"  · 保留（{args.days} 天内）：{keep}")
    print(f"  · TTL 过期：{len(expired)}")
    print(f"  · 孤儿（任务已不存在）：{len(orphans)}")

    targets = orphans + expired
    if not targets:
        print("没有需要清理的行 ✅")
        return 0

    # 安全拦截：thread_id 与 task_id 的对应关系若被改坏，会让"全部"看起来都是孤儿 —— 那时宁可不动手
    if not args.force and len(orphans) == len(thread_ids) and len(thread_ids) > 0:
        print("\n⚠️ 所有任务都查不到对应记录。可能是 thread_id 命名规则变了，或业务库与当前 DATABASE_URL 不一致。")
        print("   为避免误删全部 checkpoint，本次不做任何删除。确认无误可加 --force。")
        return 2

    if not args.apply:
        print(f"\n[干跑] 将删除 {len(targets)} 个任务的 checkpoint。确认无误后加 --apply 执行。")
        print("   示例：" + ", ".join(targets[:5]))
        return 0

    deleted = _delete(engine, tables, targets)
    print(f"\n[已执行] 删除 {len(targets)} 个任务的 checkpoint，共 {deleted} 行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
