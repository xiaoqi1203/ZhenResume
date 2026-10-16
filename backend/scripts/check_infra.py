"""基础设施自检：在**虚拟机**上切 PostgreSQL / Redis / Celery 之前先跑一次。

用法（在 VM 的 backend 目录下）：

    ./.venv/bin/python -m scripts.check_infra              # 只检查当前 .env 指向的东西
    ./.venv/bin/python -m scripts.check_infra --pg postgresql+psycopg://u:p@127.0.0.1:5432/zhenresume
    ./.venv/bin/python -m scripts.check_infra --broker redis://127.0.0.1:6379/1

它逐项做**真实连接**（不是看配置存不存在）：
1. 本地依赖是否装齐（celery / redis / psycopg）；
2. Redis：PING + 写入一个键再删掉（确认不是只读/只连得上）；
3. PostgreSQL：能不能连、能不能建表（`create_all`）、唯一索引能不能补上、写入回滚测试；
4. Celery：把 broker 里当前队列长度读出来（确认 worker 真在消费就再看一次）。

退出码 0 = 全绿，可以切换；非 0 = 有项没通，**别切**。
每一项都会打印"怎么修"，不需要回头查文档。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, hint: str = "") -> bool:
    RESULTS.append((name, bool(ok), hint))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {hint}" if hint and not ok else ""))
    return bool(ok)


def check_deps() -> None:
    import importlib.util as u

    for mod, hint in (
        ("celery", "pip install 'celery[redis]'"),
        ("redis", "pip install redis"),
        ("psycopg", "pip install 'psycopg[binary]'"),
    ):
        check(f"依赖 {mod} 已安装", bool(u.find_spec(mod)), hint)


def check_redis(url: str) -> None:
    try:
        import redis
    except ImportError:
        check("Redis 客户端可用", False, "pip install redis")
        return
    try:
        client = redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
        pong = client.ping()
        check(f"Redis 连通 {url}", bool(pong), "确认 redis-server 在跑、端口/密码/DB 编号正确")
        client.set("zhenresume:infra-check", "ok", ex=30)
        value = client.get("zhenresume:infra-check")
        client.delete("zhenresume:infra-check")
        check("Redis 可读写", value == b"ok", "实例可能只读，或 maxmemory 策略拒绝了写入")
    except Exception as exc:  # noqa: BLE001
        check(f"Redis 连通 {url}", False, f"{type(exc).__name__}: {exc}")
        print("     修：apt install -y redis-server && systemctl enable --now redis-server")


def check_postgres(dsn: str) -> None:
    """把 SQLAlchemy 的 DSN 变成 psycopg 原生 DSN 来连（避免依赖 ORM 配置）。"""
    if not dsn:
        print("[SKIP] PostgreSQL 未指定 DSN（用 --pg 或 .env 的 DATABASE_URL 传）")
        return
    native = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        import psycopg
    except ImportError:
        check("PostgreSQL 驱动可用", False, "pip install 'psycopg[binary]'")
        return
    try:
        with psycopg.connect(native, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                version = (cur.fetchone() or [""])[0]
                cur.execute("SELECT current_database()")
                dbname = (cur.fetchone() or [""])[0]
        check(f"PostgreSQL 连通（库={dbname}）", True)
        print(f"     {str(version)[:80]}")

        # 建表 + 唯一索引：直接跑应用自己的 init_db()，确保 DDL 在 PG 上成立
        import os

        os.environ["DATABASE_URL"] = dsn
        from importlib import reload

        from app import config as config_mod

        reload(config_mod)
        from app import db as db_mod

        reload(db_mod)
        db_mod.init_db()
        check("init_db() 在 PostgreSQL 上建表成功", True)

        from sqlalchemy import text

        with db_mod.engine.begin() as conn2:
            idx = conn2.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename='workflow_steps'")
            ).fetchall()
        names = {r[0] for r in idx}
        check("workflow_steps 唯一索引已存在", "uq_workflow_step_task_name" in names, f"实际索引：{sorted(names)}")

        # 写入回滚测试：确认不是只读账号
        from app.db import session_scope
        from app.models import User

        try:
            with session_scope() as session:
                session.add(User(device_id="infra-check", role="jobseeker", free_quota_used=0))
                session.flush()
                raise RuntimeError("rollback on purpose")
        except RuntimeError:
            pass
        check("PostgreSQL 可写（写入后已回滚）", True)
        print("     提示：确认完可以删掉这条自检数据 —— SELECT id FROM users WHERE device_id='infra-check';")
    except Exception as exc:  # noqa: BLE001
        check(f"PostgreSQL 连通 {dsn}", False, f"{type(exc).__name__}: {exc}")
        print("     修：apt install -y postgresql && systemctl enable --now postgresql")
        print("         sudo -u postgres psql -c \"CREATE USER zhenresume WITH PASSWORD '改成你的密码';\"")
        print("         sudo -u postgres psql -c 'CREATE DATABASE zhenresume OWNER zhenresume;'")


def check_celery(broker: str) -> None:
    try:
        from celery import Celery
    except ImportError:
        check("Celery 已安装", False, "pip install 'celery[redis]'")
        return
    app = Celery("infra-check", broker=broker)
    try:
        with app.connection_for_read() as conn:
            conn.ensure_connection(max_retries=1, timeout=5)
        check(f"Celery 能连上 broker {broker}", True)
        # 队列积压：worker 没起时数字会一直涨，起了就会被消费掉。
        # ⚠️ 队列名必须与 worker.py 的 task_default_queue 一致（默认 "default"），
        # 否则这里看的是另一个队列，会得出"没积压"的错误结论。
        try:
            import redis as redis_mod

            queue_name = "default"
            try:
                from app.worker import celery_app

                queue_name = celery_app.conf.task_default_queue or "celery"
            except Exception:  # noqa: BLE001
                pass
            client = redis_mod.from_url(broker, socket_connect_timeout=5)
            depth = client.llen(queue_name)
            print(f"     当前队列 {queue_name} 长度 = {depth}" + ("（>0 说明没有 worker 在消费）" if depth else ""))
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        check(f"Celery 能连上 broker {broker}", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    from app.config import settings

    parser = argparse.ArgumentParser(description="基础设施自检（PostgreSQL / Redis / Celery）")
    parser.add_argument("--pg", default="", help="PostgreSQL DSN；缺省用 .env 的 DATABASE_URL（仅当它是 postgresql）")
    parser.add_argument("--broker", default="", help="Celery broker；缺省用 .env 的 CELERY_BROKER_URL")
    args = parser.parse_args()

    pg = args.pg or (settings.database_url if settings.database_url.startswith("postgresql") else "")
    broker = args.broker or settings.celery_broker_url

    print("=" * 70)
    print(f"当前 .env：executor={settings.workflow_executor}　database={settings.database_url.split('://')[0]}")
    print(f"本次检查：broker={broker}　pg={'有' if pg else '(未指定)'}")
    print("=" * 70)

    check_deps()
    check_redis(broker)
    check_postgres(pg)
    check_celery(broker)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 70)
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        print("未通过：")
        for name, _ok, hint in RESULTS:
            if not _ok:
                print(f"  ✗ {name}" + (f"  —— {hint}" if hint else ""))
        print("\n⚠️ 有项没通，先别切 WORKFLOW_EXECUTOR=celery / DATABASE_URL。")
        return 1
    print("✅ 全部通过，可以切换（改 .env 后重启 uvicorn 与 worker）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
