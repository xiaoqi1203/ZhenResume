"""数据库引擎与会话。

MVP 用 SQLite；升级到 PostgreSQL + pgvector 时只改 DATABASE_URL。
表结构刻意与 aaa.md《三、数据库设计》保持同构，迁移时不需要改业务代码。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    # timeout：并发写时 SQLite 只有一个写者，默认 5 秒会抛 database is locked；
    # 并行工作流 / HR 批量会同时写 workflow_steps，所以必须放宽。
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record) -> None:  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db() -> None:
    """建表。生产环境应替换为 Alembic 迁移。"""
    from . import models  # noqa: F401  确保模型已注册到 metadata

    Base.metadata.create_all(bind=engine)
    ensure_workflow_step_index()
    ensure_resume_dedup_columns()


def ensure_resume_dedup_columns() -> bool:
    """给已存在的 `resumes` 表补上去重用的三列与索引（方案 v2 §1）。

    `create_all` 只建新表，**不给已存在的表加列** —— 老库（尤其已上线的 PostgreSQL）
    必须走这一步。

    ⚠️ 方言差异：**SQLite 不支持 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`**（PostgreSQL 才支持）。
    所以先用 inspector 查现有列，只对缺的列发 `ADD COLUMN` —— 两种库都能用且可重复执行。
    """
    import logging

    from sqlalchemy import inspect, text

    logger = logging.getLogger(__name__)
    wanted = [
        ("file_sha256", "VARCHAR(64)"),
        ("content_sha256", "VARCHAR(64)"),
        ("duplicate_of", "VARCHAR(36)"),
    ]
    try:
        insp = inspect(engine)
        if "resumes" not in insp.get_table_names():
            return True  # 表还不存在：create_all 会直接带上新列
        have = {c["name"] for c in insp.get_columns("resumes")}
        statements = [f"ALTER TABLE resumes ADD COLUMN {name} {type_}" for name, type_ in wanted if name not in have]
        statements += [
            "CREATE INDEX IF NOT EXISTS idx_resumes_file_sha ON resumes (file_sha256)",
            "CREATE INDEX IF NOT EXISTS idx_resumes_content_sha ON resumes (content_sha256)",
        ]
        with engine.begin() as conn:
            for ddl in statements:
                conn.execute(text(ddl))
        if any(s.startswith("ALTER") for s in statements):
            logger.info("resumes 去重列已补齐（%d 列）", sum(1 for s in statements if s.startswith("ALTER")))
        return True
    except Exception as exc:  # noqa: BLE001
        # 与唯一索引同样的取舍：宁可少两列也不能让服务起不来（去重会退化为"每次都当新简历"）
        logger.warning("未能补上 resumes 去重列/索引（%s）。去重会暂时失效，请手工 ALTER TABLE。", exc)
        return False


def ensure_workflow_step_index() -> bool:
    """给已存在的库补上 `uq_workflow_step_task_name` 唯一索引。

    `create_all` 只建新表，**不会给已存在的表加约束**，所以老库需要这一步。
    库里已经有重复行时创建会失败 —— 这时不崩，只打警告并给出清理语句：
    宁可不带索引运行（upsert 逻辑仍然正确），也不要让服务起不来。
    """
    import logging

    from sqlalchemy import text

    logger = logging.getLogger(__name__)
    ddl = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_step_task_name "
        "ON workflow_steps (task_id, step_name)"
    )
    try:
        with engine.begin() as conn:
            conn.execute(text(ddl))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "未能创建 workflow_steps 唯一索引（%s）。库里多半已有重复行，"
            "可用下面语句清理后重启：\n"
            "  DELETE FROM workflow_steps WHERE id NOT IN ("
            "SELECT MIN(id) FROM workflow_steps GROUP BY task_id, step_name);",
            exc,
        )
        return False


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """后台任务（工作流）里使用，独立于请求生命周期。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
