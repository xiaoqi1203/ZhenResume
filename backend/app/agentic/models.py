"""模型装配：把 `settings.llm_chain` 的供应商×模型阶梯转成一组 `ChatOpenAI`。

抽出来单独一个模块的原因：Agent 循环（本包）、旧的一次性实验（`services/agent_langchain`）
都要求"同一套阶梯语义"，否则换模型/降级行为会在两条链路上分叉。
"""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from ..config import settings


def _price_of(spec: Any) -> tuple[float, float]:
    return spec.price_in, spec.price_out


def build_models(agent: str = "agent") -> list[ChatOpenAI]:
    """按配置顺序返回模型列表：第 1 个为主，其余作为回退。

    - 供应商顺序由 `LLM_PROVIDERS` 决定，层内按 `*_MODEL_FALLBACKS` 顺序；
    - 关闭 SDK 自动重试（`max_retries=0`）：与 `services.llm` 的
      "截断/超时不重试、直接换模型"语义保持一致；
    - 供应商级 `extra_body` 原样带上（例：百炼的 `{"enable_thinking": false}`）。
    """
    from ..services.llm import LLMClient

    client = LLMClient()
    models: list[ChatOpenAI] = []
    for spec in settings.llm_chain:
        for model in client._models_for(spec, agent):  # noqa: SLF001 —— 复用节点级路由
            kwargs: dict[str, Any] = {
                "model": model,
                "base_url": spec.base_url,
                "api_key": spec.api_key,
                "temperature": 0.2,
                "max_tokens": settings.llm_max_tokens,
                "timeout": settings.llm_timeout_s,
                "max_retries": 0,
                "streaming": False,
                "metadata": {"provider": spec.name},
            }
            if spec.extra_body:
                kwargs["extra_body"] = dict(spec.extra_body)
            models.append(ChatOpenAI(**kwargs))
    return models


__all__ = ["build_models"]
