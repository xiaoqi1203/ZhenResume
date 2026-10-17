"""把 DB 中的真实简历/JD 文本导出为标注集素材（**只读 DB，不写任何表**）。

为什么需要这一步：
1. 标注集必须是**真实简历**（用户要求），而不是合成数据；
2. 直接读 ORM 会失败 —— 现网 `data/zhenresume.db` 是旧 schema
   （`resumes` 缺 `file_sha256` / `content_sha256` / `duplicate_of` 等列），
   `select(Resume)` 会抛 `no such column`；
3. 因此这里用**原生 SQL 只读取已有的列**，导出到 `eval/source_*.jsonl`，
   后续标注与评估都读文件，不再碰 DB。

输出（均为新增文件，位于 backend/eval/）：
    source_resumes.jsonl   {id, title, file_name, mime_type, raw_text, parsed_json}
    source_jds.jsonl       {id, title, company, raw_text, parsed_json}

用法（在 backend 目录下）：
    python -m scripts.dump_samples --limit 60
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

EVAL_DIR = BACKEND_DIR / "eval"
DEFAULT_DB = BACKEND_DIR / "data" / "zhenresume.db"


def _load_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def dump(db_path: Path, limit: int) -> tuple[int, int]:
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}")
    # mode=ro 只读打开，绝不写入
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    resumes = conn.execute(
        """
        SELECT id, title, file_name, mime_type, raw_text, parsed_json
        FROM resumes
        WHERE raw_text IS NOT NULL AND LENGTH(raw_text) > 0
        ORDER BY created_at
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    jds = conn.execute(
        """
        SELECT id, title, company, raw_text, parsed_json
        FROM job_descriptions
        WHERE raw_text IS NOT NULL AND LENGTH(raw_text) > 0
        ORDER BY created_at
        """
    ).fetchall()
    conn.close()

    with (EVAL_DIR / "source_resumes.jsonl").open("w", encoding="utf-8") as fh:
        for r in resumes:
            fh.write(
                json.dumps(
                    {
                        "id": r["id"],
                        "title": r["title"],
                        "file_name": r["file_name"],
                        "mime_type": r["mime_type"],
                        "raw_text": r["raw_text"],
                        "parsed_json": _load_json(r["parsed_json"]),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    with (EVAL_DIR / "source_jds.jsonl").open("w", encoding="utf-8") as fh:
        for j in jds:
            fh.write(
                json.dumps(
                    {
                        "id": j["id"],
                        "title": j["title"],
                        "company": j["company"],
                        "raw_text": j["raw_text"],
                        "parsed_json": _load_json(j["parsed_json"]),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    return len(resumes), len(jds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()

    n_res, n_jd = dump(Path(args.db), args.limit)
    print(f"[OK] 导出 {n_res} 份简历 → {EVAL_DIR / 'source_resumes.jsonl'}")
    print(f"[OK] 导出 {n_jd} 个 JD   → {EVAL_DIR / 'source_jds.jsonl'}")


if __name__ == "__main__":
    main()
