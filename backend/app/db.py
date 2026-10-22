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
    ensure_task_mode_column()
    ensure_auth_columns()
    ensure_device_id_unique()


def ensure_task_mode_column() -> bool:
    """给已存在的 `analysis_tasks` 表补上 `mode` 列（fixed / agentic）。

    与 `ensure_resume_dedup_columns` 同样的理由：`create_all` 不会给老表加列。
    """
    import logging

    from sqlalchemy import inspect, text

    logger = logging.getLogger(__name__)
    try:
        insp = inspect(engine)
        if "analysis_tasks" not in insp.get_table_names():
            return True
        have = {c["name"] for c in insp.get_columns("analysis_tasks")}
        if "mode" in have:
            return True
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE analysis_tasks ADD COLUMN mode VARCHAR(20) DEFAULT 'fixed'"))
        logger.info("analysis_tasks.mode 列已补齐")
        return True
    except Exception as exc:  # noqa: BLE001 —— 宁可少一列也不能让服务起不来
        logger.warning("未能补上 analysis_tasks.mode 列（%s）。模式切换会退化为全局默认。", exc)
        return False


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
def ensure_auth_columns() -> bool:
    """给已存在的 `users` 表补上账号体系新增的两列。

    与 `ensure_resume_dedup_columns` 同理：`create_all` 只建新表、不给老表加列。
    SQLite 不支持 `ADD COLUMN IF NOT EXISTS`，所以先用 inspector 查现有列。
    """
    import logging

    from sqlalchemy import inspect, text

    logger = logging.getLogger(__name__)
    # 方言差异：SQLite 接受 `BOOLEAN DEFAULT 0`，PostgreSQL 要求 `DEFAULT FALSE`
    # （`DEFAULT 0` 在 PG 上报 DatatypeMismatch，这列会永远补不上、登录态相关接口全 500）。
    bool_default = "FALSE" if engine.dialect.name == "postgresql" else "0"
    wanted = [
        ("auto_created", f"BOOLEAN DEFAULT {bool_default}"),
        ("token_version", "INTEGER DEFAULT 0"),
    ]
    try:
        insp = inspect(engine)
        if "users" not in insp.get_table_names():
            return True  # 表还没建：create_all 会带上新列
        have = {c["name"] for c in insp.get_columns("users")}
        statements = [f"ALTER TABLE users ADD COLUMN {name} {type_}" for name, type_ in wanted if name not in have]
        if statements:
            with engine.begin() as conn:
                for ddl in statements:
                    conn.execute(text(ddl))
            logger.info("users 账号列已补齐（%d 列）", len(statements))
        return True
    except Exception as exc:  # noqa: BLE001 —— 宁可少一列也不能让服务起不来
        logger.warning("未能补上 users 账号列（%s）。自动建号标记与改密失效会退化。", exc)
        return False

def ensure_device_id_unique() -> bool:
    """给 users.device_id 去重并加唯一索引。

    背景：匿名设备用户是「查不到就建」，并发首请求会写出同一个 device_id 的两行；
    之后 `scalar_one_or_none()` 直接抛 MultipleResultsFound → 接口 500、连接池被拖垮。
    这里做两件事：
      1. 把重复行的数据合并到最早那条（简历/任务等外键整体改指向，不丢数据）；
      2. 建立唯一索引，从数据库层根治。

    SQLite / PostgreSQL 都支持 `CREATE UNIQUE INDEX`；重复数据已清理，索引不会建失败。
    """
    import logging

    from sqlalchemy import inspect, text

    logger = logging.getLogger(__name__)
    try:
        insp = inspect(engine)
        if "users" not in insp.get_table_names():
            return True
        have_cols = {c["name"] for c in insp.get_columns("users")}
        if "device_id" not in have_cols:
            return True

        with engine.begin() as conn:
            dupes = conn.execute(
                text(
                    "SELECT device_id, COUNT(*) AS n FROM users "
                    "WHERE device_id IS NOT NULL GROUP BY device_id HAVING COUNT(*) > 1"
                )
            ).all()
            merged = 0
            for device_id, _count in dupes:
                ids = [
                    r[0]
                    for r in conn.execute(
                        text(
                            "SELECT id FROM users WHERE device_id = :d "
                            "ORDER BY created_at ASC, id ASC"
                        ),
                        {"d": device_id},
                    ).all()
                ]
                if len(ids) < 2:
                    continue
                keep, extras = ids[0], ids[1:]
                for extra in extras:
                    # 把挂在重复用户上的业务数据整体改指向留下的那条
                    # 只改那些**确实有 user_id 列**的表：analysis_reports 这类是经由 task 关联的，
                    # 硬写 UPDATE 会报 "no such column" 并把整个迁移回滚（真实踩过）。
                    for table in (
                        "resumes",
                        "job_descriptions",
                        "analysis_tasks",
                        "payments",
                        "subscriptions",
                        "hr_batches",
                        "feedbacks",
                    ):
                        if table not in insp.get_table_names():
                            continue
                        cols = {c["name"] for c in insp.get_columns(table)}
                        if "user_id" not in cols:
                            continue
                        conn.execute(
                            text(f"UPDATE {table} SET user_id = :keep WHERE user_id = :extra"),
                            {"keep": keep, "extra": extra},
                        )
                    conn.execute(text("DELETE FROM users WHERE id = :extra"), {"extra": extra})
                    merged += 1
            if merged:
                logger.warning("已合并 %d 个重复的匿名设备用户", merged)

            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_device_id "
                    "ON users (device_id) WHERE device_id IS NOT NULL"
                )
            )
        return True
    except Exception as exc:  # noqa: BLE001 —— 索引建不上也不能让服务起不来
        logger.warning("未能为 users.device_id 建立唯一索引（%s）。", exc)
        return False
