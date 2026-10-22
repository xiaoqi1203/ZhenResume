"""LLM 适配层（多供应商链）。

设计要点（对应 aaa.md §四 技术栈「LLM 路由」与 §3.6「支持失败重试与降级」）：

1. **单一入口**：所有 Agent 只调用 `LLMClient.complete_json()`，不直接碰 HTTP。
   加供应商 = 加配置，Agent 代码零改动。
2. **所有供应商都走 OpenAI 兼容协议**（`POST {base_url}/chat/completions` +
   `response_format=json_object`）——阿里云百炼、DeepSeek、任何兼容网关都能直接接。
3. **降级链是两层结构**：
   `供应商阶梯（aliyun → deepseek）` × `同层模型阶梯（deepseek-v4.1-flash-1017 → …）`
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
from dataclasses import dataclass, field

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


class LlmUnavailable(LlmError):
    """**确定性**的不可用错误：配额耗尽 / 鉴权失败 / 模型下线。

    为什么必须单独一类（实测代价很大）：主模型 `deepseek-v4.1-flash` 的免费额度
    耗尽后每次调用都返回 `403 Free quota exhausted`。它是**确定性**的 —— 重试 3 次
    只会得到同样结果，却会让每个节点白等约 3s（1.3s + 2.3s 退避 + 请求本身）。
    8 个节点串行就是 **约 23s 纯浪费**，一份报告从 80s 变 103s。

    正确处理：**立刻换下一个模型，且不做层内重试**；同时把该模型记进进程级
    黑名单（见 `_UNAVAILABLE`），后续节点直接跳过它 —— 否则 8 个节点每个都要
    先撞一次 403 才知道不行。
    """


# 进程级"已知不可用模型"黑名单：key = (provider, model)，value = 失效时间戳。
# 一次 403 之后，后续节点不再尝试它（跳过那 1.3s 的往返）。
# TTL 而不是永久：用户充值/换 Key 后不需要重启服务。
_UNAVAILABLE: dict[tuple[str, str], float] = {}
_UNAVAILABLE_TTL_S = 600.0

# 全局网络熔断：所有供应商都连不上时，说明是 DNS/出口网络挂了，不是某个模型的问题。
# 实测代价（2026-10-17 一次 DNS 抖动）：suggestion 撞完 4 个模型 × 3 次重试用掉 34s，
# 紧接着 report 又撞 9 次（9s）、qc 又撞 9 次（33s）—— **三个节点共 76s 全花在
# "再确认一次网络确实不通"上**，而报告还全部降级为空。
# 熔断后这些调用直接快速失败（走各 Agent 的规则兜底，几毫秒），用户拿到的是
# 一份完整但标注"降级"的报告，而不是干等 76 秒后拿到同样一份降级报告。
_NETWORK_DOWN_UNTIL: float = 0.0
_NETWORK_DOWN_TTL_S = 15.0

# 这些 HTTP 状态码表示"这个模型/Key 现在就是不能用"，重试与换层重试都无意义。
_TERMINAL_STATUS = {401, 403, 404}


def _is_terminal_failure(exc: BaseException) -> bool:
    """该失败是否属于"确定性失败"——重试同一个模型只会再失败一次。"""
    if isinstance(exc, (httpx.TimeoutException, LlmTruncated, LlmUnavailable)):
        return True
    msg = str(exc)
    # LlmError 的消息是 "{provider} {status}: {body}"，直接读状态码前缀
    return any(f" {code}:" in msg[:60] for code in _TERMINAL_STATUS)


def _is_network_error(exc: BaseException) -> bool:
    """是否是"整个网络/出口不可用"类错误（而不是某个模型拒绝服务）。

    `httpx.ConnectError` 覆盖 DNS 解析失败（容器里见过的 `[Errno -5] No address
    associated with hostname` / `[Errno -2] Name or service not known`）与连接被拒；
    `httpx.ConnectTimeout` 覆盖链路不通。注意**不含** ReadTimeout ——
    那是"模型在生成但太慢"，属于单模型问题，不该触发全局熔断。
    """
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def _network_is_down() -> bool:
    return time.monotonic() < _NETWORK_DOWN_UNTIL


def _trip_network_breaker() -> None:
    global _NETWORK_DOWN_UNTIL
    _NETWORK_DOWN_UNTIL = time.monotonic() + _NETWORK_DOWN_TTL_S


def _mark_unavailable(spec: "LlmProviderSpec", model: str) -> None:
    _UNAVAILABLE[(spec.name, model)] = time.monotonic() + _UNAVAILABLE_TTL_S


def _is_unavailable(spec: "LlmProviderSpec", model: str) -> bool:
    until = _UNAVAILABLE.get((spec.name, model))
    if until is None:
        return False
    if time.monotonic() >= until:
        _UNAVAILABLE.pop((spec.name, model), None)
        return False
    return True


def reset_unavailable_cache() -> None:
    """清空黑名单与网络熔断（测试与"换了 Key / 网络恢复"之后手动恢复用）。"""
    global _NETWORK_DOWN_UNTIL
    _UNAVAILABLE.clear()
    _NETWORK_DOWN_UNTIL = 0.0


def _degraded_result(mock_factory: Callable[[], dict], model: str, note: str) -> "LLMResult":
    """构造"降级为规则引擎"的返回值（两处失败出口共用，避免提示文案分叉）。"""
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
        model=model,
        mode="mock",
        degraded=True,
        note=note,
    )


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
    # 原生 function calling（V2.0）：[{id, name, arguments(dict), protocol}]
    # protocol = "native"（供应商原生 tools）| "json"（回退协议，由提示词约定 action/arguments）
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = ""


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


def _safe_json_args(raw: object) -> dict:
    """把 tool_call 的 arguments 解析成 dict。

    供应商可能给：JSON 字符串（标准）、已是 dict（部分网关）、空串、坏 JSON。
    一律不抛异常——解析失败返回 {}，由调用方决定如何处理（通常是让模型重试或降级）。
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


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

        # 全局网络熔断：刚确认过"整条出口都不通"时，直接走规则兜底。
        # 这一条是 2026-10-17 一次 DNS 抖动换来的：三个节点为了确认同一件事白等 76s。
        if _network_is_down():
            return _degraded_result(
                mock_factory,
                self.model,
                "网络出口不可用（熔断中，15s 内不再尝试）：已快速降级为规则引擎",
            )

        network_errors = 0
        for spec in self.chain:
            for model in self._models_for(spec, agent):
                # 已知不可用（配额耗尽/鉴权失败）：跳过，不再为它浪费一次往返。
                if _is_unavailable(spec, model):
                    failures.append(f"{spec.name}/{model}：已知不可用，跳过")
                    continue
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
                        # 网络整体不通：同一个节点内也没必要继续撞下一个模型
                        if _is_network_error(exc):
                            network_errors += 1
                            failures.append(f"{spec.name}/{model}：网络不可达")
                            _trip_network_breaker()
                            break
                        # 确定性失败：超时 / 截断 / 配额耗尽 / 鉴权失败 / 模型下线。
                        # 重试只会再等一轮同样的结果 → 立刻换下一个模型。
                        if _is_terminal_failure(exc):
                            if isinstance(exc, LlmTruncated) or isinstance(exc, httpx.TimeoutException):
                                reason = "输出被截断" if isinstance(exc, LlmTruncated) else "超时"
                            else:
                                reason = "该模型/Key 不可用（配额或鉴权）"
                                _mark_unavailable(spec, model)
                            logger.warning(
                                "→ %s/%s %s，跳过重试直接换下一个模型",
                                spec.name, model, reason,
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
                else:
                    failures.append(
                        f"{spec.name}/{model}：{type(last_error).__name__}: {str(last_error)[:120]}"
                        if last_error is not None
                        else f"{spec.name}/{model}：不可用"
                    )
                    continue
                if _network_is_down():
                    # 内层 break 出来（网络错误）—— 立刻结束整个链，不再换模型
                    break
            if _network_is_down():
                break

        # 全部层与模型都失败 → 降级到规则引擎，保证任务不整体失败，但绝不静默
        note = (
            f"网络出口不可用（{network_errors} 次连接失败，已熔断 15s）：已快速降级为规则引擎"
            if network_errors
            else "LLM 全部层失败，已降级为规则引擎：" + "；".join(failures[-3:])
        )
        return _degraded_result(mock_factory, self.model, note)

    # ------------------------------------------------------------------ #
    # 原生 function calling（V2.0：语义判定 Agent 用）
    # ------------------------------------------------------------------ #
    async def complete_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | dict = "auto",
        agent: str = "unknown",
    ) -> LLMResult:
        """一次「带工具的模型调用」——只负责一次往返，**循环由 Agent 负责**。

        设计要点：
        1. **复用现有降级链**：供应商阶梯、同层模型阶梯、层内指数退避、截断/超时跳过重试
           的语义与 `complete_json` 完全一致（同一份事故经验，不能有两套行为）。
        2. **JSON 协议回退**：部分网关/模型不认 `tools` 字段（400）。此时**对同一个模型
           去掉 tools 重试一次**，改用提示词约定 `{"action": ..., "arguments": {...}}`，
           返回的 tool_calls 标 `protocol="json"`。绝不因为供应商差异让整条链失败。
        3. 返回值里的 `tool_calls` 已把 arguments 解析成 dict（`{}` 表示解析失败）。
        """
        if self.mode == "mock":
            return LLMResult(
                data={}, text="", tokens_in=0, tokens_out=0, cost=0.0, latency_ms=0,
                model=self.model, mode="mock", degraded=True,
                note="mock 模式：无可用供应商，工具调用不可用（调用方应走规则兜底）",
            )

        system = _ensure_json_hint(system, json.dumps(messages, ensure_ascii=False)[:200])

        failures: list[str] = []
        attempts = max(1, settings.llm_max_retries + 1)

        if _network_is_down():
            return LLMResult(
                data={}, text="", tokens_in=0, tokens_out=0, cost=0.0, latency_ms=0,
                model=self.model, mode="mock", degraded=True,
                note="工具调用：网络出口不可用（熔断中）→ 调用方应走规则兜底",
            )

        for spec in self.chain:
            for model in self._models_for(spec, agent):
                if _is_unavailable(spec, model):
                    failures.append(f"{spec.name}/{model}：已知不可用，跳过")
                    continue
                last_error: Exception | None = None
                for attempt in range(1, attempts + 1):
                    try:
                        return await self._call_with_tools(
                            spec=spec, model=model, system=system, messages=messages,
                            tools=tools, tool_choice=tool_choice, agent=agent,
                        )
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        logger.warning(
                            "tools 调用失败 %s/%s 第 %d/%d 次 [%s]：%s: %s",
                            spec.name, model, attempt, attempts, agent,
                            type(exc).__name__, str(exc)[:200] or "(无消息)",
                        )
                        if _is_network_error(exc):
                            _trip_network_breaker()
                            failures.append(f"{spec.name}/{model}：网络不可达")
                            break
                        if _is_terminal_failure(exc):
                            # 与 complete_json 同一套语义：确定性失败不重试。
                            if not isinstance(exc, (httpx.TimeoutException, LlmTruncated)):
                                _mark_unavailable(spec, model)
                            break  # 确定性失败，换模型
                        if attempt < attempts:
                            await asyncio.sleep(min(2 ** (attempt - 1), 4))
                        continue
                failures.append(f"{spec.name}/{model}：{type(last_error).__name__}: {str(last_error)[:120]}")
                if _network_is_down():
                    break
            if _network_is_down():
                break

        return LLMResult(
            data={}, text="", tokens_in=0, tokens_out=0, cost=0.0, latency_ms=0,
            model=self.model, mode="mock", degraded=True,
            note="工具调用全部层失败（调用方应走规则兜底）：" + "；".join(failures[-3:]),
        )

    async def _call_with_tools(
        self,
        *,
        spec: LlmProviderSpec,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | dict,
        agent: str,
    ) -> LLMResult:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": 0.2,
            "max_tokens": settings.llm_max_tokens,
        }
        # 供应商级 + 模型级额外字段（例：qwen3.8-2.4t-a95b 必须 enable_thinking=true）
        extra_body = {**(spec.extra_body or {}), **((getattr(spec, "model_extra_body", {}) or {}).get(model) or {})}
        if extra_body:
            payload.update(extra_body)

        headers = {"Authorization": f"Bearer {spec.api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
            resp = await client.post(
                f"{spec.base_url.rstrip('/')}/chat/completions", json=payload, headers=headers
            )
            protocol = "native"
            if resp.status_code == 400 and (extra_body or resp.text):
                # ① 先试：去掉供应商级/模型级额外字段（与本项目既有行为一致）
                trimmed = {k: v for k, v in payload.items() if k not in extra_body}
                resp2 = await client.post(
                    f"{spec.base_url.rstrip('/')}/chat/completions", json=trimmed, headers=headers
                )
                if resp2.status_code < 400:
                    resp, payload = resp2, trimmed
                else:
                    # ② 再试：整个去掉 tools，改走 JSON 协议
                    logger.warning(
                        "%s/%s 不接受 tools 字段，退化为 JSON 协议（action/arguments）", spec.name, model
                    )
                    protocol = "json"
                    json_system = (
                        system
                        + "\n\n若需调用工具，只输出一个 JSON 对象："
                        '{"action":"<工具名>","arguments":{...}}；'
                        '若任务已完成，只输出：{"final":{...}}。不要输出其他文字。'
                    )
                    no_tools = {k: v for k, v in payload.items() if k not in ("tools", "tool_choice")}
                    no_tools["messages"] = [{"role": "system", "content": json_system}, *messages]
                    no_tools["response_format"] = {"type": "json_object"}
                    for k in extra_body:
                        no_tools.pop(k, None)
                    resp = await client.post(
                        f"{spec.base_url.rstrip('/')}/chat/completions", json=no_tools, headers=headers
                    )
        latency = int((time.perf_counter() - started) * 1000)

        if resp.status_code >= 400:
            raise LlmError(f"{spec.name} {resp.status_code}: {resp.text[:300]}")

        body = resp.json()
        try:
            choice = body["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError) as exc:
            raise LlmError(f"{spec.name} 响应结构异常: {str(body)[:300]}") from exc

        finish_reason = str(choice.get("finish_reason") or "")
        if finish_reason == "length":
            raise LlmTruncated(
                f"{spec.name}/{model} 输出被 max_tokens={settings.llm_max_tokens} 截断（工具调用不完整）"
            )

        content = message.get("content") or ""
        tool_calls: list[dict] = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            tool_calls.append(
                {
                    "id": call.get("id") or f"call_{len(tool_calls)}",
                    "name": fn.get("name") or "",
                    "arguments": _safe_json_args(fn.get("arguments")),
                    "protocol": "native",
                }
            )
        if not tool_calls and protocol == "json":
            data = _extract_json(content)
            if data.get("action"):
                tool_calls.append(
                    {
                        "id": "call_json_0",
                        "name": str(data["action"]),
                        "arguments": data.get("arguments") if isinstance(data.get("arguments"), dict) else {},
                        "protocol": "json",
                    }
                )

        usage = body.get("usage") or {}
        return LLMResult(
            data=_extract_json(content) if content.strip().startswith("{") else {},
            text=content,
            tokens_in=int(usage.get("prompt_tokens") or estimate_tokens(system)),
            tokens_out=int(usage.get("completion_tokens") or estimate_tokens(content)),
            cost=compute_cost(
                int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
                price_in=spec.price_in, price_out=spec.price_out,
            ),
            latency_ms=latency,
            model=body.get("model", model),
            mode="live",
            provider=spec.name,
            note=f"{spec.name}/{body.get('model', model)}/{protocol}",
            tool_calls=tool_calls,
            finish_reason=finish_reason,
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
        # 供应商级 + 模型级额外字段（例：阿里云 qwen3 的 {"enable_thinking": false}）
        extra_body = {**(spec.extra_body or {}), **((getattr(spec, "model_extra_body", {}) or {}).get(model) or {})}
        if extra_body:
            payload.update(extra_body)
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
            if resp.status_code == 400 and extra_body:
                logger.warning(
                    "%s/%s 拒绝额外字段 %s（400），去掉后重试一次",
                    spec.name, model, list(extra_body),
                )
                payload = {k: v for k, v in payload.items() if k not in extra_body}
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


__all__ = [
    "LLMClient",
    "LLMResult",
    "LlmTruncated",
    "LlmUnavailable",
    "get_llm",
    "reset_unavailable_cache",
]
