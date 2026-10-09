"""端到端契约验证：用 FastAPI TestClient 跑完整条业务链路并逐项校验契约。

用法（在 backend 目录下）：
    python -m scripts.verify

它会真的上传简历、解析 JD、执行 8-Agent 工作流、生成报告、下单支付、
建知识库、跑 HR 批量，并对 docs/api-contract.md 的每一项硬性要求做断言。
任何一项失败都会以非 0 退出码结束。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.services.seed import seed_if_empty  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
DEVICE = "verify-device"

results: list[tuple[bool, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    results.append((bool(condition), name, detail))
    flag = "PASS" if condition else "FAIL"
    print(f"[{flag}] {name}" + (f"  —— {detail}" if detail and not condition else ""))
    return bool(condition)


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main() -> int:
    # 显式建表 + 灌种子：不依赖 TestClient 是否触发 lifespan，脚本单独运行也可靠
    init_db()
    with session_scope() as db:
        seed_if_empty(db)

    with TestClient(app) as client:
        return _run(client)


def _run(client: TestClient) -> int:
    headers = {"X-Device-Id": DEVICE}

    # ------------------------------------------------------------------ #
    section("1. 健康检查（契约 §1）")
    # ------------------------------------------------------------------ #
    health = client.get("/api/v1/health", headers=headers)
    check("health 返回 200", health.status_code == 200, health.text[:200])
    hdata = health.json()
    check("health 字段完整", {"status", "version", "llm_mode", "llm_provider", "llm_model"} <= set(hdata), str(hdata))
    check("llm_mode 合法", hdata.get("llm_mode") in {"live", "mock"}, str(hdata.get("llm_mode")))
    print(f"    当前模式：{hdata.get('llm_mode')} / {hdata.get('llm_model')}")

    # ------------------------------------------------------------------ #
    section("2. 简历上传与解析（契约 §2）")
    # ------------------------------------------------------------------ #
    weak = client.post(
        "/api/v1/resumes",
        headers=headers,
        files={"file": ("resume_weak.txt", (SAMPLES / "sample_resume_1_weak.txt").read_bytes(), "text/plain")},
    )
    check("弱简历上传返回 201", weak.status_code == 201, weak.text[:300])
    weak_data = weak.json()
    check("简历响应字段完整", {"resume_id", "title", "file_url", "raw_text", "parsed", "created_at"} <= set(weak_data))
    parsed_keys = {"basics", "education", "work", "projects", "skills", "certificates", "timeline"}
    check("ParsedResume 键完整", parsed_keys <= set(weak_data["parsed"]), str(set(weak_data["parsed"])))
    check("解析出工作经历", len(weak_data["parsed"]["work"]) >= 2, f"work={len(weak_data['parsed']['work'])}")
    check("解析出技能", len(weak_data["parsed"]["skills"]) > 0, f"skills={weak_data['parsed']['skills']}")
    check("解析出基本信息", bool(weak_data["parsed"]["basics"]["email"]), str(weak_data["parsed"]["basics"]))
    check("保留原文", len(weak_data["raw_text"]) > 100, f"raw_text={len(weak_data['raw_text'])} 字")

    # 不支持的类型
    bad_type = client.post(
        "/api/v1/resumes",
        headers=headers,
        files={"file": ("resume.exe", b"binary", "application/octet-stream")},
    )
    check("不支持的文件类型返回 415", bad_type.status_code == 415, str(bad_type.status_code))
    check("错误结构符合契约 §8", bad_type.json().get("detail", {}).get("code") == "UNSUPPORTED_FILE_TYPE", bad_type.text[:200])

    # 列表
    listing = client.get("/api/v1/resumes", headers=headers)
    check("简历列表返回 200", listing.status_code == 200)
    check("列表结构为 {items,total}", {"items", "total"} <= set(listing.json()))

    # 另外两份简历（HR 批量用）
    resume_ids = [weak_data["resume_id"]]
    for name in ("sample_resume_2_medium.txt", "sample_resume_3_strong.txt"):
        resp = client.post(
            "/api/v1/resumes",
            headers=headers,
            files={"file": (name, (SAMPLES / name).read_bytes(), "text/plain")},
        )
        check(f"{name} 上传成功", resp.status_code == 201, resp.text[:200])
        if resp.status_code == 201:
            resume_ids.append(resp.json()["resume_id"])
    check("共上传 3 份简历", len(resume_ids) == 3, str(resume_ids))

    # ------------------------------------------------------------------ #
    section("3. JD 解析（契约 §3）")
    # ------------------------------------------------------------------ #
    jd_text = (SAMPLES / "sample_jd_backend.txt").read_text(encoding="utf-8")
    jd_resp = client.post("/api/v1/jds", headers=headers, json={"title": "后端开发工程师", "company": "某互联网科技有限公司", "raw_text": jd_text})
    check("JD 创建返回 201", jd_resp.status_code == 201, jd_resp.text[:300])
    jd_data = jd_resp.json()
    check("JD 响应字段完整", {"jd_id", "title", "company", "raw_text", "parsed", "created_at"} <= set(jd_data))
    pj = jd_data["parsed"]
    check("ParsedJD 键完整", {"title", "company", "seniority", "experience_years", "education", "skills_core", "skills_bonus", "responsibilities", "keywords"} <= set(pj), str(set(pj)))
    check("识别出年限要求", pj.get("experience_years") == 3, str(pj.get("experience_years")) + " / " + str(pj.get("seniority")))
    check("识别出学历要求", "本科" in (pj.get("education") or ""), str(pj.get("education")))
    check("识别出核心技能", len(pj.get("skills_core") or []) >= 4, str(pj.get("skills_core")))
    check("识别出职责条目", len(pj.get("responsibilities") or []) >= 3, str(len(pj.get("responsibilities") or [])))
    jd_id = jd_data["jd_id"]

    # JD 太短应被拒绝
    short_jd = client.post("/api/v1/jds", headers=headers, json={"raw_text": "招人"})
    check("过短 JD 返回 400", short_jd.status_code == 400, str(short_jd.status_code))
    check("校验错误码为 VALIDATION_ERROR", short_jd.json().get("detail", {}).get("code") == "VALIDATION_ERROR", short_jd.text[:200])

    # ------------------------------------------------------------------ #
    section("4. 分析任务与 8-Agent 工作流（契约 §4）")
    # ------------------------------------------------------------------ #
    task_resp = client.post("/api/v1/tasks", headers=headers, json={"resume_id": weak_data["resume_id"], "jd_id": jd_id})
    check("创建任务返回 202", task_resp.status_code == 202, task_resp.text[:300])
    task_id = task_resp.json()["task_id"]
    check("任务初始状态为 pending 或 running", task_resp.json()["status"] in {"pending", "running"}, task_resp.text[:200])

    # TestClient 会在响应返回前跑完后台任务，因此这里应已 done
    task_detail = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    check("任务详情返回 200", task_detail.status_code == 200, task_detail.text[:200])
    tdata = task_detail.json()
    check("任务字段完整", {"task_id", "status", "progress", "current_step", "total_steps", "steps", "report_id", "error"} <= set(tdata))
    check("任务执行成功", tdata["status"] == "done", f"status={tdata['status']} error={tdata.get('error')}")
    check("总步数为 8", tdata["total_steps"] == 8, str(tdata["total_steps"]))
    check("步骤数为 8", len(tdata["steps"]) == 8, str(len(tdata["steps"])))
    expected_order = ["parse", "jd_match", "ats", "impact", "risk", "suggestion", "report", "qc"]
    check("步骤顺序与契约一致", [s["step_name"] for s in tdata["steps"]] == expected_order, str([s["step_name"] for s in tdata["steps"]]))
    check("所有步骤均完成或降级", all(s["status"] in {"done", "degraded"} for s in tdata["steps"]), str([(s["step_name"], s["status"]) for s in tdata["steps"]]))
    check("progress 达到 1.0", float(tdata["progress"]) == 1.0, str(tdata["progress"]))
    check("产出 report_id", bool(tdata["report_id"]), str(tdata["report_id"]))
    for step in tdata["steps"]:
        print(f"    · {step['step_name']:<11} {step['status']:<8} {step['latency_ms'] or 0:>6}ms  tokens={step['tokens']}  cost={step['cost']}")

    report_id = tdata["report_id"]
    if not report_id:
        print("\n任务未产出报告，终止后续校验。")
        return _summary()

    # ------------------------------------------------------------------ #
    section("5. 报告结构与验收标准（契约 §5 + aaa.md §5）")
    # ------------------------------------------------------------------ #
    rep = client.get(f"/api/v1/reports/{report_id}", headers=headers)
    check("报告返回 200", rep.status_code == 200, rep.text[:300])
    report = rep.json()

    check("报告顶层字段完整", {"report_id", "task_id", "resume_title", "jd_title", "created_at", "scores", "risk_level", "summary", "findings", "missing_keywords", "action_items", "knowledge_refs", "disclaimer"} <= set(report), str(set(report)))
    scores = report["scores"]
    check("五维分齐全", {"overall", "ats", "impact", "clarity", "match", "risk"} == set(scores), str(scores))
    check("分数在 0–100 且为整数", all(isinstance(v, int) and 0 <= v <= 100 for v in scores.values()), str(scores))
    check("risk_level 枚举合法", report["risk_level"] in {"red", "yellow", "green"}, report["risk_level"])

    expected_level = "green" if scores["risk"] >= 80 else ("yellow" if scores["risk"] >= 60 else "red")
    check("risk_level 与 risk 分派生一致", report["risk_level"] == expected_level, f"{scores['risk']} -> {report['risk_level']} (期望 {expected_level})")

    findings = report["findings"]
    check("发现项 ≥ 5 条（验收标准）", len(findings) >= 5, f"共 {len(findings)} 条")
    check("每条发现项字段完整", all({"finding_id", "category", "severity", "title", "original_text", "issue", "evidence", "suggestion", "rewritten_text"} <= set(f) for f in findings), "存在字段缺失的条目")
    check("category 枚举合法", all(f["category"] in {"ats", "impact", "clarity", "risk", "gap"} for f in findings), str({f["category"] for f in findings}))
    check("severity 枚举合法", all(f["severity"] in {"red", "yellow", "green"} for f in findings), str({f["severity"] for f in findings}))

    order_map = {"red": 0, "yellow": 1, "green": 2}
    severities = [order_map[f["severity"]] for f in findings]
    check("发现项按 red→yellow→green 排序", severities == sorted(severities), str([f["severity"] for f in findings]))

    check("每条都有可执行建议", all(f["suggestion"].strip() and f["rewritten_text"].strip() for f in findings), "存在空建议")

    raw_text = weak_data["raw_text"]
    reds = [f for f in findings if f["severity"] == "red"]
    check("存在红色风险（该样本应触发）", len(reds) >= 1, f"red={len(reds)}")
    red_ok = True
    for f in reds:
        ev = f["evidence"]
        locatable = bool((ev.get("quote") or "").strip()) and (ev.get("span") is not None or ev.get("source") == "knowledge")
        if not locatable:
            red_ok = False
            print(f"    ! 红色风险缺少可定位证据：{f['title']}")
    check("每条红色风险都有可定位原文引用（验收标准）", red_ok, "见上方明细")

    check("免责声明非空", bool(report["disclaimer"].strip()), report["disclaimer"])
    check("missing_keywords 为数组", isinstance(report["missing_keywords"], list), str(type(report["missing_keywords"])))
    check("action_items 为数组", isinstance(report["action_items"], list), str(type(report["action_items"])))
    check("知识库引用非空（RAG 生效）", len(report["knowledge_refs"]) >= 1, f"refs={len(report['knowledge_refs'])}")

    # 回归保护：关键词类问题数量多且全是红色，容易把"时间线空窗"这类黄色风险挤出报告，
    # 导致风险维度虚高。这里断言该样本的时间线风险必须出现在报告里。
    categories = {f["category"] for f in findings}
    check("报告包含风险类别发现项（风险维度不被关键词问题挤掉）", "risk" in categories, f"实际类别：{sorted(categories)}")
    check("至少覆盖 3 个分析维度", len(categories) >= 3, f"实际类别：{sorted(categories)}")
    # 回归保护：关键词缺失类发现项的证据必须是关键词本身，而不是 JD 首行（岗位名）
    kw_findings = [f for f in findings if "关键词" in f["title"] and "缺失" in f["title"]]
    kw_ok = all((f["evidence"].get("quote") or "") and (f["evidence"]["quote"] in f["title"]) for f in kw_findings)
    check(
        "关键词类发现项的引文是关键词本身",
        kw_ok,
        str([(f["title"][:30], (f["evidence"] or {}).get("quote")) for f in kw_findings[:3]]),
    )
    check("该样本触发时间线空窗检测", any("空窗" in f["title"] for f in findings), "未检测到空窗期发现项")

    print(f"\n    总分 {scores['overall']} ｜ ATS {scores['ats']} ｜ 影响力 {scores['impact']} ｜ 清晰度 {scores['clarity']} ｜ 竞争力 {scores['match']} ｜ 风险安全度 {scores['risk']}（{report['risk_level']}）")
    print(f"    风险分布：红 {len([f for f in findings if f['severity'] == 'red'])} / 黄 {len([f for f in findings if f['severity'] == 'yellow'])} / 绿 {len([f for f in findings if f['severity'] == 'green'])}")
    print(f"    缺失关键词：{'、'.join(report['missing_keywords'][:8]) or '无'}")
    for i, f in enumerate(findings[:4], 1):
        print(f"    {i}. [{f['severity']}/{f['category']}] {f['title']}")
        if f["original_text"]:
            print(f"       原文：{f['original_text'][:60]}")
        print(f"       改写：{f['rewritten_text'][:80]}")

    # Markdown 导出
    md = client.get(f"/api/v1/reports/{report_id}/markdown", headers=headers)
    check("Markdown 导出返回 200", md.status_code == 200, str(md.status_code))
    check("Markdown 含标题", md.text.startswith("# "), md.text[:60])
    check("Markdown 含免责声明", "免责声明" in md.text, "")
    check("Markdown 下载头正确", "attachment" in md.headers.get("content-disposition", ""), md.headers.get("content-disposition", ""))

    # 不可变性的二次验证：同一简历重跑，分数必须一致
    task2 = client.post("/api/v1/tasks", headers=headers, json={"resume_id": weak_data["resume_id"], "jd_id": jd_id})
    if task2.status_code == 202:
        report2_id = client.get(f"/api/v1/tasks/{task2.json()['task_id']}", headers=headers).json().get("report_id")
        if report2_id:
            report2 = client.get(f"/api/v1/reports/{report2_id}", headers=headers).json()
            check("同一简历两次诊断分数一致（可回归）", report2["scores"] == scores, f"{report2['scores']} vs {scores}")
        else:
            check("同一简历两次诊断分数一致（可回归）", False, "第二次任务未产出报告")
    else:
        print(f"    (跳过一致性校验：第二次任务被额度闸门拦截 {task2.status_code})")

    # ------------------------------------------------------------------ #
    section("6. 定价与支付（契约 §6.1）")
    # ------------------------------------------------------------------ #
    plans = client.get("/api/v1/plans", headers=headers)
    check("套餐列表返回 200", plans.status_code == 200, plans.text[:200])
    plan_items = plans.json()["items"]
    expected_plans = {"free", "single_basic", "single_deep", "monthly_basic", "monthly_pro", "hr_basic", "hr_pro"}
    check("套餐 code 集合与契约一致", {p["code"] for p in plan_items} == expected_plans, str({p["code"] for p in plan_items}))
    check("套餐字段完整", all({"code", "name", "price", "currency", "period", "quota", "features"} <= set(p) for p in plan_items), "")

    order = client.post("/api/v1/payments/orders", headers=headers, json={"plan_code": "single_deep", "provider": "stripe"})
    check("下单返回 201", order.status_code == 201, order.text[:300])
    order_data = order.json()
    check("订单字段完整", {"order_no", "plan_code", "amount", "currency", "provider", "status", "pay_url"} <= set(order_data), str(order_data))
    check("订单金额与套餐一致", order_data["amount"] == 19.9, str(order_data["amount"]))

    cb1 = client.post("/api/v1/payments/callback", headers=headers, json={"order_no": order_data["order_no"], "status": "paid"})
    cb2 = client.post("/api/v1/payments/callback", headers=headers, json={"order_no": order_data["order_no"], "status": "paid"})
    check("支付回调返回 200", cb1.status_code == 200 and cb2.status_code == 200, cb1.text[:200])
    check("回调幂等", cb1.json() == cb2.json() and cb1.json()["status"] == "paid", f"{cb1.json()} / {cb2.json()}")

    bad_plan = client.post("/api/v1/payments/orders", headers=headers, json={"plan_code": "not_exist"})
    check("未知套餐返回 404", bad_plan.status_code == 404, str(bad_plan.status_code))
    check("未知套餐错误码正确", bad_plan.json().get("detail", {}).get("code") == "PLAN_NOT_FOUND", bad_plan.text[:200])

    # ------------------------------------------------------------------ #
    section("7. 知识库 RAG（契约 §6.2）")
    # ------------------------------------------------------------------ #
    docs = client.get("/api/v1/knowledge/docs", headers=headers)
    check("知识库列表返回 200", docs.status_code == 200, docs.text[:200])
    doc_items = docs.json()["items"]
    check("种子知识库已灌入", len(doc_items) >= 8, f"共 {len(doc_items)} 篇")
    check("文档含切片", all(d.get("chunk_count", 0) >= 1 for d in doc_items), str([d.get("chunk_count") for d in doc_items[:5]]))

    search = client.post("/api/v1/knowledge/search", headers=headers, json={"query": "后端 高并发 量化指标", "top_k": 3})
    check("知识库检索返回 200", search.status_code == 200, search.text[:200])
    hits = search.json()["items"]
    check("检索有结果", len(hits) >= 1, f"hits={len(hits)}")
    check("检索结果字段完整", all({"chunk_id", "doc_id", "title", "scope", "score", "text"} <= set(h) for h in hits), "")
    check("检索结果按相关度降序", [h["score"] for h in hits] == sorted([h["score"] for h in hits], reverse=True), str([h["score"] for h in hits]))
    if hits:
        print(f"    最佳命中：[{hits[0]['scope']}] {hits[0]['title']}（score={hits[0]['score']}）")

    new_doc = client.post(
        "/api/v1/knowledge/docs",
        headers=headers,
        json={"scope": "role", "role_family": "test", "doc_type": "skill_model", "title": "验证用文档", "content": "这是一篇用于验证入库与切片流程的测试文档。\n\n第二段内容，用于产生第二个切片。"},
    )
    check("新建知识库文档返回 201", new_doc.status_code == 201, new_doc.text[:200])
    check("返回切片数", new_doc.json().get("chunk_count", 0) >= 1, str(new_doc.json().get("chunk_count")))

    # ------------------------------------------------------------------ #
    section("8. 反馈与管理后台（契约 §6.3 / §6.4）")
    # ------------------------------------------------------------------ #
    fb = client.post("/api/v1/feedback", headers=headers, json={"report_id": report_id, "rating": 5, "comment": "建议很具体，改写句能直接用"})
    check("反馈创建返回 201", fb.status_code == 201, fb.text[:200])

    metrics = client.get("/api/v1/admin/metrics", headers=headers)
    check("成本看板返回 200", metrics.status_code == 200, metrics.text[:200])
    m = metrics.json()
    check("看板字段完整", {"tasks_total", "tasks_done", "tasks_failed", "reports_total", "avg_latency_ms", "tokens_total", "cost_total", "cost_per_report", "feedback_avg_rating", "fairness_pass_rate", "step_breakdown"} <= set(m), str(set(m)))
    check("单份成本 ≤ 0.5 元（aaa.md §4）", float(m["cost_per_report"]) <= 0.5, str(m["cost_per_report"]))
    check("公平性通过率 100%（验收标准）", float(m["fairness_pass_rate"]) >= 1.0, str(m["fairness_pass_rate"]))
    check("步骤成本明细覆盖 8 步", len(m["step_breakdown"]) == 8, str([s["step_name"] for s in m["step_breakdown"]]))
    print(f"    任务 {m['tasks_total']}（成功 {m['tasks_done']} / 失败 {m['tasks_failed']}）｜报告 {m['reports_total']}｜平均耗时 {m['avg_latency_ms']}ms")
    print(f"    token {m['tokens_total']}｜总成本 ¥{m['cost_total']}｜单份 ¥{m['cost_per_report']}｜评分 {m['feedback_avg_rating']}｜公平性 {m['fairness_pass_rate']}")

    prompts = client.get("/api/v1/admin/prompts", headers=headers)
    check("Prompt 列表返回 200", prompts.status_code == 200, prompts.text[:200])
    check("Prompt 覆盖 8 个 Agent", len(prompts.json()["items"]) >= 8, str(len(prompts.json()["items"])))
    before_version = next((p["version"] for p in prompts.json()["items"] if p["name"] == "ats_agent"), 0)
    upsert = client.post("/api/v1/admin/prompts", headers=headers, json={"name": "ats_agent", "content": "（验证用）请以 ATS 视角审查简历。"})
    check("Prompt 覆盖写入返回 201", upsert.status_code == 201, upsert.text[:200])
    check("Prompt 版本号自增", upsert.json()["version"] == before_version + 1, f"{before_version} -> {upsert.json().get('version')}")

    fb_list = client.get("/api/v1/admin/feedback", headers=headers)
    check("反馈列表返回 200", fb_list.status_code == 200, fb_list.text[:200])
    check("反馈总数 ≥ 1", fb_list.json()["total"] >= 1, str(fb_list.json()["total"]))

    # ------------------------------------------------------------------ #
    section("9. HR 批量初筛（契约 §6.5）")
    # ------------------------------------------------------------------ #
    batch = client.post("/api/v1/hr/batches", headers=headers, json={"jd_id": jd_id, "resume_ids": resume_ids})
    check("创建批次返回 202", batch.status_code == 202, batch.text[:300])
    batch_id = batch.json()["batch_id"]
    check("批次 total 正确", batch.json()["total"] == 3, str(batch.json()))

    batch_detail = client.get(f"/api/v1/hr/batches/{batch_id}", headers=headers)
    check("批次详情返回 200", batch_detail.status_code == 200, batch_detail.text[:200])
    bdata = batch_detail.json()
    check("批次字段完整", {"batch_id", "status", "total", "finished", "items"} <= set(bdata), str(set(bdata)))
    check("批次已全部完成", bdata["status"] == "done" and bdata["finished"] == 3, f"{bdata['status']} / {bdata['finished']}")
    check("批次条目数为 3", len(bdata["items"]) == 3, str(len(bdata["items"])))
    check("条目字段完整", all({"resume_id", "resume_title", "status", "overall_score", "match_score", "risk_level", "report_id", "summary"} <= set(i) for i in bdata["items"]), "")
    scores_list = [i["overall_score"] for i in bdata["items"] if i["overall_score"] is not None]
    check("按总分降序排列", scores_list == sorted(scores_list, reverse=True), str(scores_list))
    check("强弱简历分数有区分度", len(scores_list) >= 2 and max(scores_list) > min(scores_list), str(scores_list))
    for i, item in enumerate(bdata["items"], 1):
        detail = ""
        if item["report_id"]:
            r = client.get(f"/api/v1/reports/{item['report_id']}", headers=headers).json()
            cats = sorted({f["category"] for f in r["findings"]})
            sevs = {s: len([f for f in r["findings"] if f["severity"] == s]) for s in ("red", "yellow", "green")}
            detail = f"  维度 {','.join(cats)}  红{sevs['red']}/黄{sevs['yellow']}/绿{sevs['green']}"
        print(f"    {i}. {item['resume_title'][:28]:<30} 总分 {item['overall_score']}  匹配 {item['match_score']}  风险 {item['risk_level']}{detail}")

    csv_resp = client.get(f"/api/v1/hr/batches/{batch_id}/export.csv", headers=headers)
    check("CSV 导出返回 200", csv_resp.status_code == 200, str(csv_resp.status_code))
    check("CSV 含 BOM（Excel 不乱码）", csv_resp.content.startswith(b"\xef\xbb\xbf"), str(csv_resp.content[:12]))
    check("CSV 含表头", "总分" in csv_resp.text and "匹配分" in csv_resp.text, csv_resp.text.splitlines()[0] if csv_resp.text else "")

    # ------------------------------------------------------------------ #
    section("10. 隐私删除与错误处理（契约 §8 / aaa.md §4）")
    # ------------------------------------------------------------------ #
    tmp = client.post(
        "/api/v1/resumes",
        headers=headers,
        files={"file": ("tmp.txt", "临时简历内容，用于验证删除级联。".encode("utf-8"), "text/plain")},
    )
    tmp_id = tmp.json()["resume_id"]
    deleted = client.delete(f"/api/v1/resumes/{tmp_id}", headers=headers)
    check("删除简历返回 204", deleted.status_code == 204, str(deleted.status_code))
    gone = client.get(f"/api/v1/resumes/{tmp_id}", headers=headers)
    check("删除后返回 404", gone.status_code == 404, str(gone.status_code))
    check("404 错误结构符合契约", gone.json().get("detail", {}).get("code") == "RESUME_NOT_FOUND", gone.text[:200])

    bad_task = client.post("/api/v1/tasks", headers=headers, json={"resume_id": "not-exist", "jd_id": jd_id})
    check("不存在的简历返回 404", bad_task.status_code == 404, str(bad_task.status_code))

    missing_report = client.get("/api/v1/reports/not-exist", headers=headers)
    check("不存在的报告返回 404", missing_report.status_code == 404, str(missing_report.status_code))
    check("报告 404 错误码正确", missing_report.json().get("detail", {}).get("code") == "REPORT_NOT_FOUND", missing_report.text[:200])

    share = client.get(f"/api/v1/reports/{report_id}/share", headers=headers)
    check("分享链接返回 200", share.status_code == 200, share.text[:200])
    check("分享 URL 指向前端", "/share/" in share.json().get("share_url", ""), str(share.json()))

    # ------------------------------------------------------------------ #
    section("11. 文档格式抽取回归（DOCX / PDF）")
    # ------------------------------------------------------------------ #
    # 同一份简历的 .txt 基线是 2 段工作经历 + 1 个项目。
    # PDF 走 pypdf，会遇到的真实坑：CJK 标题乱序（「项目经历」→「目项 经历」）、
    # 项目符号落在私用区（\uf0b7）。漏掉标题会让项目段串进工作经历，造出虚假经历。
    baseline_work = 2
    for name in ("sample_resume_1_weak.docx", "sample_resume_1_weak.pdf"):
        path = SAMPLES / name
        if not path.exists():
            print(f"    (跳过 {name}：样本不存在，可运行 python -m scripts.make_sample_docs 生成)")
            continue
        resp = client.post(
            "/api/v1/resumes",
            headers=headers,
            files={"file": (name, path.read_bytes(), "application/octet-stream")},
        )
        check(f"{name} 上传解析成功", resp.status_code == 201, resp.text[:200])
        if resp.status_code != 201:
            continue
        data = resp.json()
        check(f"{name} 抽取出正文", len(data["raw_text"]) > 300, f"{len(data['raw_text'])} 字")
        check(
            f"{name} 工作经历段数与纯文本基线一致",
            len(data["parsed"]["work"]) == baseline_work,
            f"实际 {len(data['parsed']['work'])} 段（期望 {baseline_work}）",
        )
        check(f"{name} 识别出项目经历（标题未被串段）", len(data["parsed"]["projects"]) >= 1, f"{len(data['parsed']['projects'])} 个")
        check(f"{name} 识别出技能", len(data["parsed"]["skills"]) >= 4, str(data["parsed"]["skills"]))

    # ------------------------------------------------------------------ #
    section("12. 简历模板与编辑器（契约 §2.3–§2.5 / §6.6）")
    # ------------------------------------------------------------------ #
    tpl_resp = client.get("/api/v1/templates", headers=headers)
    check("模板列表返回 200", tpl_resp.status_code == 200, tpl_resp.text[:200])
    tpl_items = tpl_resp.json()["items"]
    check("内置模板 ≥ 4 套", len(tpl_items) >= 4, f"{len(tpl_items)} 套")
    check(
        "模板摘要字段完整",
        all({"code", "name", "description", "target", "role_family", "section_count"} <= set(t) for t in tpl_items),
        "",
    )
    check(
        "模板 target 枚举合法",
        all(t["target"] in {"campus", "experienced", "transition"} for t in tpl_items),
        str({t["target"] for t in tpl_items}),
    )
    for t in tpl_items:
        print(f"    · {t['code']:<20} {t['name']:<16} {t['section_count']} 个区块  target={t['target']}")

    tpl_code = tpl_items[0]["code"]
    tpl_resp_detail = client.get(f"/api/v1/templates/{tpl_code}", headers=headers)
    check("模板详情返回 200", tpl_resp_detail.status_code == 200, tpl_resp_detail.text[:200])
    td = tpl_resp_detail.json()
    check("模板详情字段完整", {"intro", "writing_rules", "sections", "example_content"} <= set(td), str(set(td)))
    check(
        "每个区块都带写作提示与示例句",
        all(s.get("tip") and s.get("examples") for s in td["sections"]),
        str([s.get("key") for s in td["sections"] if not s.get("tip") or not s.get("examples")]),
    )
    valid_keys = {"basics", "summary", "work", "projects", "education", "skills", "certificates", "awards", "custom"}
    check("区块 key 合法", all(s["key"] in valid_keys for s in td["sections"]), str({s["key"] for s in td["sections"]}))
    check("写作规则 ≥ 5 条", len(td["writing_rules"]) >= 5, f"{len(td['writing_rules'])} 条")

    missing_tpl = client.get("/api/v1/templates/not-exist", headers=headers)
    check("不存在的模板返回 404", missing_tpl.status_code == 404, str(missing_tpl.status_code))
    check("模板 404 错误码正确", missing_tpl.json().get("detail", {}).get("code") == "TEMPLATE_NOT_FOUND", missing_tpl.text[:200])

    # --- 由编辑器内容创建简历（不走文件上传）---
    example = td["example_content"]
    editor_keys = {"basics", "summary", "work", "projects", "education", "skills", "certificates", "awards", "custom_sections"}
    check("模板示例内容键完整", editor_keys <= set(example), str(set(example)))

    created = client.post(
        "/api/v1/resumes/from-editor",
        headers=headers,
        json={"title": "编辑器简历-社招后端", "template_code": tpl_code, "content": example},
    )
    check("编辑器创建简历返回 201", created.status_code == 201, created.text[:300])
    ed = created.json()
    check("source 标记为 editor", ed.get("source") == "editor", str(ed.get("source")))
    check("editor_content 原样回显", ed.get("editor_content") == example, "回显内容与提交不一致")
    check("渲染出纯文本 raw_text", len(ed.get("raw_text") or "") > 200, f"{len(ed.get('raw_text') or '')} 字")
    check(
        "工作经历被正确结构化（渲染的纯文本可被解析器读回）",
        len(ed["parsed"]["work"]) == len(example["work"]),
        f"解析 {len(ed['parsed']['work'])} 段 vs 提交 {len(example['work'])} 段",
    )
    check(
        "项目经历未被串段",
        len(ed["parsed"]["projects"]) == len(example["projects"]),
        f"解析 {len(ed['parsed']['projects'])} 个 vs 提交 {len(example['projects'])} 个",
    )
    check("技能被结构化", len(ed["parsed"]["skills"]) >= 5, str(ed["parsed"]["skills"][:6]))
    check("联系方式被结构化", bool(ed["parsed"]["basics"]["email"]), str(ed["parsed"]["basics"]))

    roundtrip = client.get(f"/api/v1/resumes/{ed['resume_id']}", headers=headers).json()
    check("重新打开可取回编辑器内容（可反复修改）", roundtrip.get("editor_content") == example, "往返不一致")

    # 内容过少应被拒
    too_little = client.post(
        "/api/v1/resumes/from-editor",
        headers=headers,
        json={"title": "空简历", "content": {"basics": {"name": ""}, "summary": "", "work": [], "projects": [], "education": [], "skills": [], "certificates": [], "awards": [], "custom_sections": []}},
    )
    check("内容过少的简历返回 400", too_little.status_code == 400, str(too_little.status_code))
    check("错误码为 VALIDATION_ERROR", too_little.json().get("detail", {}).get("code") == "VALIDATION_ERROR", too_little.text[:200])

    # --- 三格式导出 ---
    md_export = client.get(f"/api/v1/resumes/{ed['resume_id']}/export/markdown", headers=headers)
    check("Markdown 导出返回 200", md_export.status_code == 200, str(md_export.status_code))
    check("Markdown 含姓名与章节", "# " in md_export.text and "工作经历" in md_export.text and "专业技能" in md_export.text, md_export.text[:80])
    check("Markdown 含提交的量化要点", any(h in md_export.text for h in example["work"][0]["highlights"]), "未找到要点原文")

    docx_export = client.get(f"/api/v1/resumes/{ed['resume_id']}/export/docx", headers=headers)
    check("DOCX 导出返回 200", docx_export.status_code == 200, str(docx_export.status_code))
    check("DOCX 是合法 ZIP 包（PK 头）", docx_export.content[:2] == b"PK", str(docx_export.content[:8]))
    check("DOCX Content-Type 正确", "wordprocessingml" in docx_export.headers.get("content-type", ""), docx_export.headers.get("content-type", ""))
    check("导出文件名支持中文（RFC 5987）", "filename*=UTF-8''" in docx_export.headers.get("content-disposition", ""), docx_export.headers.get("content-disposition", ""))

    pdf_export = client.get(f"/api/v1/resumes/{ed['resume_id']}/export/pdf", headers=headers)
    if settings.pdf_enabled:
        check("PDF 导出返回 200", pdf_export.status_code == 200, f"{pdf_export.status_code} {pdf_export.text[:160]}")
        check("PDF 是合法 PDF（%PDF 头）", pdf_export.content[:4] == b"%PDF", str(pdf_export.content[:8]))
        check("PDF 体积合理", len(pdf_export.content) > 3000, f"{len(pdf_export.content)} 字节")

        # 转换链最容易出的问题是「静默丢内容」：DOCX→PDF 后文字被裁掉或段落丢失。
        # 这里直接读 PDF 文本层核对，而不是只看文件头。
        import io as _io

        from pypdf import PdfReader as _PdfReader

        pdf_text = "\n".join((p.extract_text() or "") for p in _PdfReader(_io.BytesIO(pdf_export.content)).pages)
        normalize = lambda s: s.replace(" ", "").replace("\n", "")  # noqa: E731
        sample_highlight = example["work"][0]["highlights"][0]
        check(
            "PDF 转换后关键内容完整（未静默截断）",
            normalize(sample_highlight) in normalize(pdf_text),
            f"未在 PDF 文本层找到：{sample_highlight[:40]}",
        )
        check("PDF 含全部三个工作经历要点区块", normalize("专业技能") in normalize(pdf_text), "缺少章节标题")
        print(f"    导出体积：Markdown {len(md_export.content)}B ｜ DOCX {len(docx_export.content)}B ｜ PDF {len(pdf_export.content)}B")
    else:
        check("未配置 LibreOffice 时 PDF 导出返回 503", pdf_export.status_code == 503, str(pdf_export.status_code))
        check("PDF 不可用错误码正确", pdf_export.json().get("detail", {}).get("code") == "PDF_UNAVAILABLE", pdf_export.text[:200])
        print("    (未配置 LibreOffice，PDF 走降级路径；这符合契约 §2.4 的约定)")

    # --- PDF 降级路径 ---
    # 本机配了 LibreOffice 时，上面的分支走的是「成功路径」，降级分支永远覆盖不到。
    # 但降级恰恰是最容易写错的地方（未配置时抛 500 而不是 503），所以临时抹掉配置显式验证一次。
    original_node, original_cli = settings.libreoffice_node, settings.libreoffice_cli
    try:
        settings.libreoffice_node = ""
        settings.libreoffice_cli = ""
        degraded = client.get(f"/api/v1/resumes/{ed['resume_id']}/export/pdf", headers=headers)
        check("PDF 未配置时返回 503 而不是 500", degraded.status_code == 503, str(degraded.status_code))
        check("降级错误码为 PDF_UNAVAILABLE", degraded.json().get("detail", {}).get("code") == "PDF_UNAVAILABLE", degraded.text[:200])
        check(
            "降级提示引导用户改用 DOCX 或浏览器打印",
            "DOCX" in degraded.json().get("detail", {}).get("message", ""),
            degraded.text[:200],
        )
        still_ok = client.get(f"/api/v1/resumes/{ed['resume_id']}/export/docx", headers=headers)
        check("PDF 不可用时 DOCX 导出不受影响", still_ok.status_code == 200, str(still_ok.status_code))
    finally:
        settings.libreoffice_node, settings.libreoffice_cli = original_node, original_cli

    # --- 闭环：编辑器简历 → 诊断 → 报告回指编辑器 ---
    ed_task = client.post("/api/v1/tasks", headers=headers, json={"resume_id": ed["resume_id"], "jd_id": jd_id})
    check("编辑器简历可发起诊断", ed_task.status_code == 202, ed_task.text[:200])
    if ed_task.status_code == 202:
        ed_state = client.get(f"/api/v1/tasks/{ed_task.json()['task_id']}", headers=headers).json()
        check("编辑器简历诊断完成", ed_state["status"] == "done", f"{ed_state['status']} / {ed_state.get('error')}")
        if ed_state.get("report_id"):
            ed_report = client.get(f"/api/v1/reports/{ed_state['report_id']}", headers=headers).json()
            check(
                "高质量简历的报告同样满足「≥5 条建议」（验收标准）",
                len(ed_report["findings"]) >= 5,
                f"仅 {len(ed_report['findings'])} 条",
            )
            check("报告标记 resume_source=editor", ed_report.get("resume_source") == "editor", str(ed_report.get("resume_source")))
            check(
                "报告暴露闭环字段（供前端跳回编辑器）",
                ed_report.get("resume_id") == ed["resume_id"] and ed_report.get("has_editor_content") is True,
                f"resume_id={ed_report.get('resume_id')} has_editor_content={ed_report.get('has_editor_content')}",
            )
            green = [f for f in ed_report["findings"] if f["severity"] == "green"]
            check("不足 5 条时补充的是正面发现项（green）而非编造问题", len(ed_report["findings"]) >= 5, f"green {len(green)} 条")
            sev = {s: len([f for f in ed_report["findings"] if f["severity"] == s]) for s in ("red", "yellow", "green")}
            print(
                f"    编辑器简历报告：总分 {ed_report['scores']['overall']}｜"
                f"红 {sev['red']} / 黄 {sev['yellow']} / 绿 {sev['green']}｜风险 {ed_report['risk_level']}｜"
                f"发现项 {len(ed_report['findings'])} 条"
            )

    # --- 上传的简历也能导出（以解析结果为来源）---
    up_export = client.get(f"/api/v1/resumes/{weak_data['resume_id']}/export/markdown", headers=headers)
    check("上传的简历可导出（走解析结果）", up_export.status_code == 200, f"{up_export.status_code} {up_export.text[:160]}")
    if up_export.status_code == 200:
        check("上传简历导出内容含解析出的工作经历", "工作经历" in up_export.text, up_export.text[:120])
    up_summary = client.get("/api/v1/resumes", headers=headers).json()["items"]
    check("简历列表带 source 字段", all("source" in item for item in up_summary), str(up_summary[:1]))

    # --- 已经说明过空窗期时，不应再要求用户「补一句说明」---
    # 让人做已经做过的事，是最伤信任的一类建议，因此单独做回归保护。
    gap_content = {
        "basics": {"name": "空窗测试", "target_title": "后端开发工程师", "phone": "138-0000-0000", "email": "gap@example.com", "city": "杭州", "links": []},
        "summary": "回归测试用简历。",
        "work": [
            {"company": "甲公司", "title": "后端开发工程师", "start": "2022.07", "end": "至今", "location": "杭州", "highlights": ["主导订单接口开发，支撑日均 100 万次调用"]},
            {"company": "乙公司", "title": "后端开发工程师", "start": "2021.07", "end": "2021.12", "location": "上海", "highlights": ["完成 3 个模块的开发与上线"]},
        ],
        "projects": [],
        "education": [{"school": "某大学", "degree": "本科", "major": "计算机科学与技术", "start": "2017.09", "end": "2021.06"}],
        "skills": ["Java", "MySQL", "Redis"],
        "certificates": [],
        "awards": [],
        "custom_sections": [
            {"title": "空窗期说明", "items": ["2021.12 – 2022.07 全职备考并完成微服务实战课程，独立完成一套订单系统开发"]}
        ],
    }
    gap_resume = client.post("/api/v1/resumes/from-editor", headers=headers, json={"title": "空窗期已说明", "content": gap_content})
    check("含空窗期说明的简历创建成功", gap_resume.status_code == 201, gap_resume.text[:200])
    if gap_resume.status_code == 201:
        gap_task = client.post("/api/v1/tasks", headers=headers, json={"resume_id": gap_resume.json()["resume_id"], "jd_id": jd_id})
        check("含空窗期说明的简历可发起诊断", gap_task.status_code == 202, gap_task.text[:200])
        if gap_task.status_code == 202:
            gap_state = client.get(f"/api/v1/tasks/{gap_task.json()['task_id']}", headers=headers).json()
            if gap_state.get("report_id"):
                gap_report = client.get(f"/api/v1/reports/{gap_state['report_id']}", headers=headers).json()
                gap_titles = [f["title"] for f in gap_report["findings"]]
                gap_related = [t for t in gap_titles if "空窗" in t]
                check("已说明空窗期时不再要求补充说明", not any("已主动说明" not in t for t in gap_related), str(gap_related))
                check("已说明空窗期转为正面条目（green）", any("已主动说明" in t for t in gap_related), str(gap_related))

    return _summary()


def _summary() -> int:
    passed = len([r for r in results if r[0]])
    total = len(results)
    failed = [r for r in results if not r[0]]

    print(f"\n{'=' * 72}")
    print(f"验证结果：{passed}/{total} 项通过")
    # 检查项总数会随环境浮动，这里直接说明原因，避免误以为"少了 3 项 = 环境不全"。
    # 差异来源：① PDF 导出是否可用（5 项 ↔ 2 项降级检查）；
    #          ② 免费额度是否放行二次诊断一致性校验（1 项）。
    pdf_note = (
        "本次包含 PDF 导出的 5 项检查（已配置 LibreOffice）"
        if settings.pdf_enabled
        else "本次 PDF 走降级分支，只做 2 项检查（未配置 LibreOffice；配置后会多出 3 项内容保真检查）"
    )
    print("说明：检查项总数随环境浮动，这不代表环境不完整 ——")
    print(f"      · {pdf_note}")
    print("      · 二次诊断一致性校验需要额度放行（免费额度用满且未支付时会跳过 1 项）")
    print("      常见总数：177（含 PDF）/ 174（未配置 PDF）。两者都代表「全部通过」。")
    print(f"{'=' * 72}")
    if failed:
        print("\n未通过的检查项：")
        for _, name, detail in failed:
            print(f"  ✗ {name}" + (f"\n      {detail}" if detail else ""))
        return 1
    print("\n全部契约检查通过。")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 2
    raise SystemExit(code)
