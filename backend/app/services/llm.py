"""LLM 适配层（多供应商链）。

设计要点（对应 aaa.md §四 技术栈「LLM 路由」与 §3.6「支持失败重试与降级」）：

1. **单一入口**：所有 Agent 只调用 `LLMClient.complete_json()`，不直接碰 HTTP。
   加供应商 = 加配置，Agent 代码零改动。
2. **所有供应商都走 OpenAI 兼容协议**（`POST {base_url}/chat/completions` +
   `response_format=json_object`）——阿里云百炼、DeepSeek、任何兼容网关都能直接接。
3. **降级链是两层结构**：
   `供应商阶梯（aliyun → deepseek）` × `同层模型阶梯（qwen3.8-max → … → qwen3.7-flash）`
   每一层内部再做 `llm_max_retries` 次指数退避；
   全部失败才回落到 Agent 提供的 `mock_factory`（标记 degraded，原因写进 note，绝不静默）。
4. 成功时 `note` 记录"是哪一层哪个模型答的"，会落进 `workflow_steps.notes` ——
   这样换模型/降级发生后，看板能直接查到每一步的真实来源。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..config import LlmProviderSpec, settings
from ..errors import LlmError
from .cost import compute_cost, estimate_tokens

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LlmTruncated(LlmError):
    """输出被 `max_tokens` 截断（finish_reason=length），JSON 必然不完整。

    为什么单独一个异常：截断是**确定性**失败 —— 同一个模型重试三次还是同样截断，
    却会白等三轮。实测把 `LLM_MAX_TOKENS` 降到 1200 时，suggestion 节点因此
    从 ~27s 变成 **239s 后降级**（5 个模型 × 3 次重试全被截断）。
    所以这类失败要像超时一样"跳过重试、直接换下一个模型"。
    """


@dataclass
class LLMResult:
    data: dict
    text: str
    tokens_in: int
    tokens_out: int
    cost: float
    latency_ms: int
    model: str
    mode: str
    provider: str = ""
    degraded: bool = False
    note: str | None = None


def _ensure_json_hint(system: str, user: str) -> str:
    """保证 messages 里出现 `json`，否则阿里云百炼/DeepSeek 会因 response_format 直接 400。

    只在缺失时补一行，不改动已有提示词的语义；补了会记一条 warning，
    这样"后台把提示词改坏了"这件事在看板上是可见的，而不是静默降级。
    """
    if "json" in f"{system} {user}".lower():
        return system
    logger.warning(
        "提示词未出现 'json' 但要求了 response_format=json_object，已自动补一句"
        "（否则阿里云/DeepSeek 会 400 并导致该节点降级）。建议在后台提示词里保留 json 字样。"
    )
    return f"{system}\n\n务必只输出一个 JSON 对象，不要输出任何解释性文字。"


def _extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON 对象，容忍 markdown 代码块与前后废话。"""
    cleaned = _FENCE_RE.sub("", (text or "").strip()).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise LlmError("模型未返回合法 JSON")


class LLMClient:
    """异步 LLM 客户端。实例可复用，内部无状态。"""

    def __init__(self) -> None:
        self.chain: list[LlmProviderSpec] = settings.llm_chain
        self.mode = settings.llm_mode
        self.provider = settings.llm_provider
        self.model = settings.active_model

    @property
    def chain_summary(self) -> list[str]:
        return [f"{spec.name}:{m}" for spec in self.chain for m in spec.models]

    def _models_for(self, spec: LlmProviderSpec, agent: str) -> list[str]:
        """节点级模型路由：把 `LLM_FAST_MODEL` 提到该供应商阶梯的最前面。

        ⚠️ **实测结论：按模型名换"快模型"是无效甚至反效果的，默认关闭。**
        用 `deepseek-v4-pro-0813` 替换 `qwen3.8-max` 跑 ats/impact/risk/qc，同一份简历对比：
            ats    20432ms/1649 token → 25479ms/2403 token   （更慢，输出膨胀 46%）
            impact 17783ms/1442       → 18979ms/1935         （更慢）
            risk   23212ms/1871       → 23553ms/2264         （持平）
            qc     10304ms/1865       →  7154ms/1737         （更快 ✓）
        结论：这个模型**每 token 更快，但写得更啰嗦**，只在输出量本来就小的节点（qc）上划算。
        真正的杠杆是**压输出长度**（见 prompts.py 的长度硬约束），不是换模型。

        保留这个开关是为了以后 A/B 新模型时有现成机制 —— 但请先看上面的数字，
        别再凭"某模型单任务快 1.8×"的探针结论就开（那个 1.8× 是在 parse 上测的，不通用）。

        安全约束：只在该供应商阶梯里确实有这个模型时才提前，否则原样返回 ——
        绝不因为路由而引入新的失败路径。
        """
        models = list(spec.models)
        fast = (settings.llm_fast_model or "").strip()
        if not fast or fast not in models:
            return models
        if not agent or agent not in settings.llm_fast_node_set:
            return models
        return [fast] + [m for m in models if m != fast]

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #
    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        mock_factory: Callable[[], dict],
        agent: str = "unknown",
    ) -> LLMResult:
        """要求模型输出 JSON 对象。

        `mock_factory` 在 mock 模式或 live 失败时被调用，必须返回契约合规的 dict。
        """
        if self.mode == "mock":
            started = time.perf_counter()
            data = mock_factory()
            latency = int((time.perf_counter() - started) * 1000)
            return LLMResult(
                data=data,
                text=json.dumps(data, ensure_ascii=False),
                tokens_in=0,
                tokens_out=0,
                cost=0.0,
                latency_ms=latency,
                model=self.model,
                mode="mock",
                note="mock 模式：由规则引擎生成（未配置任何 LLM Key）",
            )

        # 多供应商链：逐层、逐模型尝试；每层内部做 N 次指数退避重试。
        #
        # ⚠️ 一个必须由适配层兜住的坑（实测踩到）：
        # 阿里云百炼与 DeepSeek 都强制要求"用 response_format=json_object 时，
        # messages 里必须出现 json 这个词"，否则直接 400
        # （InternalError.Algo.InvalidParameter: 'messages' must contain the word 'json'）。
        # 而 prompt 是**可以被后台覆盖**的（POST /admin/prompts）——
        # 一旦管理员写的提示词里没有 "json"，该节点就会在**所有模型**上 400 并静默降级到规则引擎。
        # 所以在这一层统一兜住，而不是指望每个提示词作者都记得写。
        system = _ensure_json_hint(system, user)

        failures: list[str] = []
        attempts = max(1, settings.llm_max_retries + 1)
        for spec in self.chain:
            for model in self._models_for(spec, agent):
                last_error: Exception | None = None
                for attempt in range(1, attempts + 1):
                    try:
                        result = await self._call_openai_compatible(
                            spec=spec, model=model, system=system, user=user, agent=agent
                        )
                    except Exception as exc:  # noqa: BLE001 —— 网络/解析/限流都归为可重试
                        last_error = exc
                        logger.warning(
                            "LLM 失败 %s/%s 第 %d/%d 次 [%s]：%s: %s",
                            spec.name, model, attempt, attempts, agent,
                            type(exc).__name__, str(exc)[:200] or "(无消息)",
                        )
                        # 超时说明"这个模型就是太慢"、截断说明"这个模型就是太长"——
                        # 两者重试都只会再等一轮同样的结果，直接换下一个模型。
                        if isinstance(exc, (httpx.TimeoutException, LlmTruncated)):
                            logger.warning(
                                "→ %s/%s %s，跳过重试直接换下一个模型",
                                spec.name, model,
                                "超时" if isinstance(exc, httpx.TimeoutException) else "输出被截断",
                            )
                            break
                        if attempt < attempts:
                            await asyncio.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    logger.info(
                        "LLM ok %s/%s [%s] %dms in=%d out=%d ¥%.6f",
                        spec.name, result.model, agent, result.latency_ms,
                        result.tokens_in, result.tokens_out, result.cost,
                    )
                    if failures:
                        result.note = f"前 {len(failures)} 次失败后切换：{'；'.join(failures[-2:])}"
                    return result
                failures.append(
                    f"{spec.name}/{model}：{type(last_error).__name__}: {str(last_error)[:120]}"
                )

        # 全部层与模型都失败 → 降级到规则引擎，保证任务不整体失败，但绝不静默
        started = time.perf_counter()
        data = mock_factory()
        latency = int((time.perf_counter() - started) * 1000)
        return LLMResult(
            data=data,
            text=json.dumps(data, ensure_ascii=False),
            tokens_in=0,
            tokens_out=0,
            cost=0.0,
            latency_ms=latency,
            model=self.model,
            mode="mock",
            degraded=True,
            note="LLM 全部层失败，已降级为规则引擎：" + "；".join(failures[-3:]),
        )

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    async def _call_openai_compatible(
        self,
        *,
        spec: LlmProviderSpec,
        model: str,
        system: str,
        user: str,
        agent: str,
    ) -> LLMResult:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": settings.llm_max_tokens,
        }
        # 供应商级额外字段（例：阿里云 qwen3 的 {"enable_thinking": false}）
        if spec.extra_body:
            payload.update(spec.extra_body)
        headers = {
            "Authorization": f"Bearer {spec.api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
            resp = await client.post(
                f"{spec.base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            # 供应商级额外字段可能不被某个模型/网关接受（400）。这时**去掉额外字段重试一次**，
            # 而不是让整条链降级 —— 否则换一个模型就会莫名"全部层失败"。
            if resp.status_code == 400 and spec.extra_body:
                logger.warning(
                    "%s/%s 拒绝额外字段 %s（400），去掉后重试一次",
                    spec.name, model, list(spec.extra_body),
                )
                payload = {k: v for k, v in payload.items() if k not in spec.extra_body}
                resp = await client.post(
                    f"{spec.base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        latency = int((time.perf_counter() - started) * 1000)

        if resp.status_code >= 400:
            raise LlmError(f"{spec.name} {resp.status_code}: {resp.text[:300]}")

        body = resp.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LlmError(f"{spec.name} 响应结构异常: {str(body)[:300]}") from exc

        # 截断检测：finish_reason=length 说明输出被 max_tokens 砍断，JSON 一定不完整。
        # 此时重试同一个模型没有意义（会得到同样的截断），必须立刻换下一个模型。
        try:
            finish_reason = body["choices"][0].get("finish_reason") or ""
        except (KeyError, IndexError):
            finish_reason = ""
        if finish_reason == "length":
            raise LlmTruncated(
                f"{spec.name}/{model} 输出被 max_tokens={settings.llm_max_tokens} 截断，JSON 不完整。"
                "正确做法是改提示词让输出更短（限制条数与字数），而不是硬砍 max_tokens。"
            )

        data = _extract_json(content)
        usage = body.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or estimate_tokens(system + user))
        tokens_out = int(usage.get("completion_tokens") or estimate_tokens(content))

        return LLMResult(
            data=data,
            text=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=compute_cost(tokens_in, tokens_out, price_in=spec.price_in, price_out=spec.price_out),
            latency_ms=latency,
            model=body.get("model", model),
            mode="live",
            provider=spec.name,
            note=f"{spec.name}/{body.get('model', model)}",
        )


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
