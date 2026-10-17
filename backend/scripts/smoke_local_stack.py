"""容器内冒烟测试：走真实 HTTP 接口 + celery 执行器，验证全栈连通。

在容器里跑：
    docker exec zhenresume-api python /app/scripts/smoke_local_stack.py

与 `scripts/verify.py` 的区别：verify 用 FastAPI TestClient（进程内、inline 语义），
本脚本走**真实 HTTP + Celery worker**，证明部署形态（api 收请求 → Redis → worker 执行）是通的。
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
HEADERS = {"X-Device-Id": "local-docker-smoke-1"}
POLL_MAX = 90
POLL_INTERVAL = 3


def main() -> int:
    resume = open("/app/samples/sample_resume_2_medium.txt", encoding="utf-8").read()
    jd = open("/app/samples/sample_jd_backend.txt", encoding="utf-8").read()
    c = httpx.Client(timeout=30)

    r = c.post(
        f"{BASE}/resumes",
        headers=HEADERS,
        files={"file": ("smoke.txt", resume.encode("utf-8"), "text/plain")},
    )
    print(f"上传简历      : HTTP {r.status_code}")
    if r.status_code >= 400:
        print(r.text[:300])
        return 1
    body = r.json()
    resume_id = body.get("id") or body.get("resume_id")

    r = c.post(f"{BASE}/jds", headers=HEADERS, json={"title": "后端开发工程师", "raw_text": jd})
    print(f"创建 JD       : HTTP {r.status_code}")
    if r.status_code >= 400:
        print(r.text[:300])
        return 1
    body = r.json()
    jd_id = body.get("id") or body.get("jd_id")

    r = c.post(f"{BASE}/tasks", headers=HEADERS, json={"resume_id": resume_id, "jd_id": jd_id})
    print(f"发起诊断任务  : HTTP {r.status_code}（celery 时应立刻 202）")
    if r.status_code >= 400:
        print(r.text[:300])
        return 1
    task_id = r.json()["task_id"]

    state: dict = {}
    for _ in range(POLL_MAX):
        state = c.get(f"{BASE}/tasks/{task_id}", headers=HEADERS).json()
        if state.get("status") in ("done", "failed"):
            break
        time.sleep(POLL_INTERVAL)

    print(f"任务最终状态  : {state.get('status')} | progress={state.get('progress')} | error={state.get('error')}")
    report_id = state.get("report_id")
    if not report_id:
        print("✗ 未产出报告")
        return 1

    rep = c.get(f"{BASE}/reports/{report_id}", headers=HEADERS).json()
    scores = rep["scores"]
    print(f"报告          : 总分 {scores['overall']} ｜ ATS {scores['ats']} ｜ 影响力 {scores['impact']} "
          f"｜ 清晰度 {scores['clarity']} ｜ 竞争力 {scores['match']} ｜ 风险 {scores['risk']}({rep['risk_level']})")
    print(f"发现项        : {len(rep['findings'])} 条 ｜ 知识库引用 {len(rep.get('knowledge_refs') or [])} 条")
    if rep["findings"]:
        print(f"首条问题      : {rep['findings'][0]['title'][:70]}")

    steps = state.get("steps") or []
    done = [s for s in steps if s.get("status") in ("done", "degraded")]
    print(f"工作流步骤    : {len(done)}/{len(steps)} 完成或降级")
    print("\n✓ 全栈连通：api → celery/Redis → worker → PG → 报告")
    return 0


if __name__ == "__main__":
    sys.exit(main())
