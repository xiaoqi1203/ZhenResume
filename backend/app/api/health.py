"""健康检查（契约 §1）。"""

from __future__ import annotations

from fastapi import APIRouter

from ..config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": settings.version,
        "llm_mode": settings.llm_mode,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.active_model,
        # 契约 §1 之外的新增字段（向前兼容）：完整降级链，运维一眼看出配了哪些层。
        # 例：["aliyun:qwen3.8-max", "aliyun:qwen3.7-flash", "deepseek:deepseek-flash"]
        "llm_chain": settings.llm_chain_summary,
        # 检索配置：排查"知识库引用突然为空"时先看这几项（provider 与维度必须与库里一致）
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model if settings.embedding_provider == "http" else "local-hash",
        "embedding_dim": settings.embedding_dim,
        # 供应商链：主层 → 回退层（例 ["primary:qwen3.7-text-embedding-flash",
        # "fallback:mxbai-embed-large:latest"]）。两层维度必须一致，否则启动后第一次检索就会报错。
        "embedding_chain": settings.embedding_chain_summary,
    }
