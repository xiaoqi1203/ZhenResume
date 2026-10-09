"""LLM 适配层。

设计要点（对应 aaa.md §四 技术栈「LLM 路由」与 §3.6「支持失败重试与降级」）：

1. **单一入口**：所有 Agent 只调用 `LLMClient.complete_json()`，不直接碰 HTTP。
   以后要加 GPT-4o / Claude，只需在这里加一个 provider 分支，Agent 代码零改动。
2. **DeepSeek 走 OpenAI 兼容协议**（/chat/completions + response_format=json_object），
   所以适配层天然兼容任何 OpenAI 兼容网关，把 base_url 指过去即可。
3. **三重降级链**：live 调用 → 重试 N 次 → 回落到 Agent 提供的 `mock_factory`（标记 degraded）。
   因此没有 API Key 时整条工作流依然能产出契约合规的报告，便于开发与演示。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..config import settings
from ..errors import LlmError
from .cost import compute_cost, estimate_tokens

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


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
    degraded: bool = False
    note: str | None = None


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
        self.mode = settings.llm_mode
        self.provider = settings.llm_provider
        self.model = settings.llm_model if self.mode == "live" else "mock-rule-engine"

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
                note="mock 模式：由规则引擎生成（未配置 DEEPSEEK_API_KEY）",
            )

        last_error: Exception | None = None
        attempts = max(1, settings.llm_max_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                return await self._call_deepseek(system=system, user=user, agent=agent)
            except Exception as exc:  # noqa: BLE001 —— 网络/解析/限流都归为可重试
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 4))

        # 全部重试失败 → 降级到规则引擎，保证任务不整体失败
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
            note=f"LLM 调用失败，已降级为规则引擎：{type(last_error).__name__}: {str(last_error)[:200]}",
        )

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    async def _call_deepseek(self, *, system: str, user: str, agent: str) -> LLMResult:
        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 4096,
        }
        headers = {
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
            resp = await client.post(
                f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
        latency = int((time.perf_counter() - started) * 1000)

        if resp.status_code >= 400:
            raise LlmError(f"DeepSeek {resp.status_code}: {resp.text[:300]}")

        body = resp.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LlmError(f"DeepSeek 响应结构异常: {str(body)[:300]}") from exc

        data = _extract_json(content)
        usage = body.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or estimate_tokens(system + user))
        tokens_out = int(usage.get("completion_tokens") or estimate_tokens(content))

        return LLMResult(
            data=data,
            text=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=compute_cost(tokens_in, tokens_out),
            latency_ms=latency,
            model=body.get("model", settings.llm_model),
            mode="live",
            note=None,
        )


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
