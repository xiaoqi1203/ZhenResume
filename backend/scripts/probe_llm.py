"""LLM 降级链体检：逐层逐模型发一次最小请求，告诉你"到底哪一层、哪个模型能用"。

用法（在 backend 目录下）：

    .\\.venv\\Scripts\\python.exe -m scripts.probe_llm              # 探测整条链
    .\\.venv\\Scripts\\python.exe -m scripts.probe_llm --only aliyun
    .\\.venv\\Scripts\\python.exe -m scripts.probe_llm --embeddings  # 顺带探测 embedding 模型
    .\\.venv\\Scripts\\python.exe -m scripts.probe_llm --json > probe.json

为什么需要它：
`complete_json` 是"第一层成功就返回"，所以真实跑起来只会用到链头那个模型。
这个脚本绕过"短路"，把链上每一个 `供应商:模型` 都单独打一次，于是能回答三个问题：
    1. 主模型能不能用（不是等到线上才发现 404）；
    2. 备用层是否真的可用（否则"降级链"是纸面上的）；
    3. 每一步的真实延迟/tokens/成本是多少 —— 这是"单份 ≤ 0.5 元""60 秒内出报告"
       两个验收项唯一可靠的估算来源。

只读：不写数据库、不碰业务表。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import LlmProviderSpec, settings  # noqa: E402
from app.services.llm import LLMClient  # noqa: E402

SYSTEM = "你是一个严格的 JSON 生成器，只输出 JSON，不要任何解释。"
USER = '请输出：{"ok": true, "n": 1, "note": "probe"}'

# 一份真实报告大约要跑 8 次 LLM 调用（8 个节点各一次），
# 且各节点 prompt 比探针大得多，所以下面的"单份成本"只是**下限估算**。
REPORT_CALLS = 8


def build_prompt(chars: int) -> str:
    """把提示词填充到约 chars 字符，模拟真实节点（parse 会把简历前 12000 字塞进去）。"""
    if chars <= len(USER):
        return USER
    filler = (
        "【背景材料】负责后端接口开发与维护，参与订单系统重构，"
        "使用 Java / Spring Boot / MySQL / Redis，支撑日均 30 万单。\n"
    )
    body = filler * ((chars - len(USER)) // len(filler) + 1)
    return body[: chars - len(USER)] + USER


async def probe_model(spec: LlmProviderSpec, model: str, prompt: str, timeout: float | None) -> dict:
    """走生产同一条代码路径（LLMClient._call_openai_compatible）打一次真实请求。"""
    client = LLMClient()
    original_timeout = settings.llm_timeout_s
    if timeout is not None:
        settings.llm_timeout_s = timeout
    started = time.perf_counter()
    try:
        result = await client._call_openai_compatible(  # noqa: SLF001 —— 体检脚本刻意复用生产路径
            spec=spec, model=model, system=SYSTEM, user=prompt, agent="probe"
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "provider": spec.name,
            "model": model,
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:200] or '(无消息)'}",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        settings.llm_timeout_s = original_timeout
    return {
        "provider": spec.name,
        "model": model,
        "ok": True,
        "served_model": result.model,
        "latency_ms": result.latency_ms,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "cost": result.cost,
        "json_ok": isinstance(result.data, dict),
        "preview": json.dumps(result.data, ensure_ascii=False)[:120],
    }


async def probe_embeddings(candidates: list[str]) -> list[dict]:
    """探测阿里云百炼 OpenAI 兼容端点下可用的 embedding 模型（用于后续检索升级）。"""
    spec = next((s for s in settings.llm_chain if s.name == "aliyun"), None)
    if spec is None:
        return [{"ok": False, "error": "没有配置 aliyun 层，跳过"}]
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for model in candidates:
            try:
                resp = await client.post(
                    f"{spec.base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {spec.api_key}"},
                    json={"model": model, "input": ["岗位能力模型：后端工程师，熟悉高并发与性能优化"]},
                )
                if resp.status_code >= 400:
                    out.append({"model": model, "ok": False, "error": f"{resp.status_code}: {resp.text[:200]}"})
                    continue
                body = resp.json()
                vec = (body.get("data") or [{}])[0].get("embedding") or []
                usage = body.get("usage") or {}
                out.append({
                    "model": model,
                    "ok": True,
                    "dim": len(vec),
                    "usage": usage,
                })
            except Exception as exc:  # noqa: BLE001
                out.append({"model": model, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return out


async def bench_agent(agent_name: str, chain: list[LlmProviderSpec], resume: str, jd: str) -> list[dict]:
    """用**真实节点提示词**逐个模型跑一遍（例：parse）。

    比 --chars 更准：它用的是 Agent 自己构造的 system/user 提示词与输出结构，
    于是能回答"这个模型能不能在预算内干完这个节点"这个真问题。
    """
    from app.agents.base import AgentContext
    from app.agents.parse_agent import ParseAgent
    from app.schemas import ParsedJd, ParsedResume

    if agent_name != "parse":
        raise SystemExit(f"暂只支持 --agent parse（当前：{agent_name}）")

    rows: list[dict] = []
    for spec in chain:
        for model in spec.models:
            single = LlmProviderSpec(
                name=spec.name, base_url=spec.base_url, api_key=spec.api_key,
                models=(model,), price_in=spec.price_in, price_out=spec.price_out,
                extra_body=spec.extra_body,
            )
            client = LLMClient()
            client.chain = [single]
            ctx = AgentContext(
                task_id="probe", resume_text=resume, jd_text=jd,
                parsed_resume=ParsedResume.empty(), parsed_jd=ParsedJd.empty(),
            )
            started = time.perf_counter()
            try:
                out = await ParseAgent().run(ctx, client)
            except Exception as exc:  # noqa: BLE001
                rows.append({
                    "provider": spec.name, "model": model, "ok": False,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "error": f"{type(exc).__name__}: {str(exc)[:160] or '(无消息)'}",
                })
                continue
            llm = out.llm
            rows.append({
                "provider": spec.name, "model": model, "ok": True,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "tokens_in": llm.tokens_in if llm else 0,
                "tokens_out": llm.tokens_out if llm else 0,
                "cost": llm.cost if llm else 0.0,
                "work_count": len(ctx.parsed_resume.work),
                "project_count": len(ctx.parsed_resume.projects),
                "skill_count": len(ctx.parsed_resume.skills),
            })
    return rows


async def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 降级链体检")
    parser.add_argument("--only", default="", help="只探测某个供应商，如 aliyun / deepseek")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（便于归档）")
    parser.add_argument("--embeddings", action="store_true", help="顺带探测 embedding 模型是否可用")
    parser.add_argument("--chars", type=int, default=0, help="把提示词填充到约 N 字符（模拟真实节点）")
    parser.add_argument("--timeout", type=float, default=None, help="覆盖本次探测的超时秒数")
    parser.add_argument("--extra", default="", help='额外请求体 JSON，如 {"enable_thinking": false}')
    parser.add_argument("--agent", default="", help="用真实节点提示词基准测试，目前支持 parse")
    args = parser.parse_args()

    chain = settings.llm_chain
    if args.only:
        chain = [s for s in chain if s.name == args.only]

    if args.extra.strip():
        extra = settings._parse_extra_body(args.extra)  # noqa: SLF001
        chain = [
            LlmProviderSpec(
                name=s.name, base_url=s.base_url, api_key=s.api_key, models=s.models,
                price_in=s.price_in, price_out=s.price_out, extra_body={**s.extra_body, **extra},
            )
            for s in chain
        ]

    prompt = build_prompt(args.chars)

    if not chain:
        print("❌ 链上没有可用供应商：所有 Key 都是空的（当前为 mock 模式）。")
        print("   请检查 backend/.env 的 ALIYUN_API_KEY / DEEPSEEK_API_KEY。")
        return 2

    if not args.json:
        print(f"LLM 模式：{settings.llm_mode}｜链：{' → '.join(settings.llm_chain_summary)}")
        print(f"超时 {args.timeout or settings.llm_timeout_s}s｜每层重试 {settings.llm_max_retries} 次"
              f"｜提示词 {len(prompt)} 字符" + (f"｜extra={args.extra}" if args.extra.strip() else ""))
        print()

    # 真实节点基准（--agent parse）：用样本简历 + 样本 JD 跑真正的 parse 节点
    if args.agent:
        samples = Path(__file__).resolve().parent.parent / "samples"
        resume = (samples / "sample_resume_1_weak.txt").read_text(encoding="utf-8")
        jd = (samples / "sample_jd_backend.txt").read_text(encoding="utf-8")
        rows = await bench_agent(args.agent, chain, resume, jd)
        if args.json:
            print(json.dumps({"agent": args.agent, "results": rows}, ensure_ascii=False, indent=2))
            return 0 if any(r["ok"] for r in rows) else 1
        print(f"-- 真实节点基准：{args.agent}（简历 {len(resume)} 字符 + JD {len(jd)} 字符）--")
        for r in rows:
            if r["ok"]:
                print(
                    f"✅ {r['provider']:<9} {r['model']:<28} {r['elapsed_ms']:>7}ms  "
                    f"out={r['tokens_out']:<5} ¥{r['cost']:.5f}  "
                    f"解析出 工作{r['work_count']}/项目{r['project_count']}/技能{r['skill_count']}"
                )
            else:
                print(f"❌ {r['provider']:<9} {r['model']:<28} {r['elapsed_ms']:>7}ms  {r['error']}")
        ok = [r for r in rows if r["ok"]]
        if ok:
            fastest = min(ok, key=lambda r: r["elapsed_ms"])
            print(f"\n最快且成功：{fastest['provider']}:{fastest['model']}  {fastest['elapsed_ms']}ms")
            slow = [r for r in ok if r["elapsed_ms"] > 60000]
            if slow:
                print("⚠️ 超过 60 秒的模型（会拖爆「60 秒出报告」验收）：" +
                      "、".join(f"{r['provider']}:{r['model']}({r['elapsed_ms']}ms)" for r in slow))
        return 0 if ok else 1

    results: list[dict] = []
    for spec in chain:
        for model in spec.models:
            row = await probe_model(spec, model, prompt, args.timeout)
            results.append(row)
            if args.json:
                continue
            if row["ok"]:
                print(
                    f"✅ {row['provider']:<9} {row['model']:<28} "
                    f"{row['latency_ms']:>6}ms  in={row['tokens_in']:<5} out={row['tokens_out']:<4} "
                    f"¥{row['cost']:.6f}  json={'✓' if row['json_ok'] else '✗'}  {row['preview']}"
                )
            else:
                print(f"❌ {row['provider']:<9} {row['model']:<28} {row.get('elapsed_ms', 0):>6}ms  {row['error']}")

    ok_rows = [r for r in results if r["ok"]]
    summary = {
        "llm_mode": settings.llm_mode,
        "chain": settings.llm_chain_summary,
        "available": [f"{r['provider']}:{r['model']}" for r in ok_rows],
        "unavailable": [f"{r['provider']}:{r['model']}" for r in results if not r["ok"]],
        "probe_cost_total": round(sum(r["cost"] for r in ok_rows), 6),
    }

    if args.embeddings:
        emb = await probe_embeddings(
            ["text-embedding-v4", "text-embedding-v3", "text-embedding-v2", "text-embedding-v1"]
        )
        summary["embeddings"] = emb
        if not args.json:
            print("\n-- embedding 模型探测（用于后续把哈希向量换成真实语义向量）--")
            for row in emb:
                if row.get("ok"):
                    print(f"✅ {row['model']:<22} dim={row.get('dim')}  usage={row.get('usage')}")
                else:
                    print(f"❌ {row.get('model', '-'):<22} {row.get('error')}")

    if not args.json:
        print("\n-- 结论 --")
        if summary["available"]:
            print(f"可用：{'、'.join(summary['available'])}")
        if summary["unavailable"]:
            print(f"不可用：{'、'.join(summary['unavailable'])}")
        head_ok = bool(ok_rows) and ok_rows[0]["provider"] == chain[0].name and ok_rows[0]["model"] == chain[0].models[0]
        print(f"链头（{chain[0].name}:{chain[0].models[0]}）：{'可用 ✅' if head_ok else '不可用 ⚠️ 实际流量会走后面的层'}")
        if ok_rows:
            avg_cost = sum(r["cost"] for r in ok_rows) / len(ok_rows)
            print(
                f"探针单次平均成本 ¥{avg_cost:.6f}（探针很小，真实节点 prompt 大得多）；"
                f"按 {REPORT_CALLS} 次调用线性外推的下限 ≈ ¥{avg_cost * REPORT_CALLS:.4f}/份"
            )
            lat = [r["latency_ms"] for r in ok_rows]
            print(f"单次延迟：min {min(lat)}ms / avg {int(sum(lat)/len(lat))}ms / max {max(lat)}ms")
            print(f"（8 节点串行外推下限 ≈ {int(sum(lat)/len(lat)) * REPORT_CALLS / 1000:.1f}s；真实值用 scripts.verify 的看板核对）")
    else:
        print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))

    return 0 if ok_rows else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
