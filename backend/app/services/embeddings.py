"""文本向量化。

**重要取舍**：DeepSeek 目前不提供 embedding 接口（只有 chat/completions）。
为了让 RAG 在 MVP 阶段零成本、可离线、结果可复现，这里用**本地确定性哈希向量**：

- 中文按「单字 + 相邻双字」切分，英文按词切分（覆盖中文岗位知识库的主要检索场景）
- 用 blake2b 哈希到固定维度（不用内置 hash()，因为其对 str 带随机种子，跨进程不稳定）
- L2 归一化后可直接用点积当余弦相似度

升级路径：把 `embed()` 换成 bge-m3 或 text-embedding-3-small 的调用即可，
`knowledge_chunks.embedding` 换成 pgvector 的 Vector(1536) 列，检索层无需改动。
"""

from __future__ import annotations

import hashlib
import logging
import math
import re

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class EmbeddingDimMismatch(RuntimeError):
    """模型输出维度与 EMBEDDING_DIM 不一致。

    **这种错误绝不允许静默回落哈希向量**：那会让库里同时存在两种维度的向量，
    `cosine()` 遇到长度不一致直接返回 0.0 → 检索"有结果但全 0 分"，
    表现为"知识库引用变空"，很难排查。
    """

_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_LATIN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]*|\d+[a-zA-Z]*")
_STOPWORDS = {
    "的", "了", "和", "与", "及", "或", "在", "是", "有", "为", "对", "等",
    "the", "and", "for", "with", "you", "are", "our", "will", "that", "this",
}


def tokenize(text: str) -> list[str]:
    """产出检索用 token：中文单字 + 双字，英文/数字词。"""
    text = (text or "").lower()
    tokens: list[str] = []

    for run in _CJK_RUN_RE.findall(text):
        for ch in run:
            if ch not in _STOPWORDS:
                tokens.append(ch)
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram not in _STOPWORDS:
                tokens.append(bigram)

    for word in _LATIN_RE.findall(text):
        if word not in _STOPWORDS and len(word) > 1:
            tokens.append(word)

    return tokens


def _bucket(token: str, dim: int) -> tuple[int, float]:
    """哈希到 [0, dim)，并给一个确定性的正负号以降低碰撞偏置。"""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if (value >> 63) & 1 else -1.0


def _hash_embed(text: str, dim: int | None = None) -> list[float]:
    """本地确定性哈希向量（默认 provider）。"""
    dim = dim or settings.embedding_dim
    vec = [0.0] * dim
    for token in tokenize(text):
        idx, sign = _bucket(token, dim)
        vec[idx] += sign
    return l2_normalize(vec)


# --------------------------------------------------------------------------- #
# HTTP provider（真实语义向量）
# --------------------------------------------------------------------------- #
# 最近一次实际服务我们的模型名（rag 用它判断"查询向量与库内向量是否同一个模型"）
_last_served_model: str = ""


def last_served_model() -> str:
    return _last_served_model


def index_spec() -> "object | None":
    """建索引时**只允许用主层**：禁止在建索引过程中切换供应商。

    为什么必须这样：主层超时后自动回退到回退层，会让同一个索引里混入
    **两个不同模型**的向量（虽然维度相同，但向量空间不同）——
    跨模型的余弦相似度没有意义，检索会静默劣化，而且完全看不出来。
    所以建索引要么全用主层成功，要么整体失败让你明确处置。
    """
    chain = settings.embedding_chain
    return chain[0] if chain else None


def _embed_with_spec(spec, texts: list[str]) -> list[list[float]]:
    """用单个供应商批量向量化。维度不一致直接抛 EmbeddingDimMismatch。"""
    global _last_served_model
    out: list[list[float]] = []
    batch_size = max(1, settings.embedding_batch_size)
    with httpx.Client(timeout=settings.embedding_timeout_s) as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            resp = client.post(
                f"{spec.base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {spec.api_key}", "Content-Type": "application/json"},
                json={"model": spec.model, "input": batch},
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"{spec.name} {resp.status_code}: {resp.text[:200]}")
            data = resp.json().get("data") or []
            if len(data) != len(batch):
                raise RuntimeError(f"{spec.name} 返回条数不匹配：{len(data)} != {len(batch)}")
            for item in sorted(data, key=lambda d: d.get("index", 0)):
                vec = [float(v) for v in (item.get("embedding") or [])]
                if len(vec) != settings.embedding_dim:
                    raise EmbeddingDimMismatch(
                        f"{spec.name}（{spec.model}）输出 {len(vec)} 维，"
                        f"但当前 EMBEDDING_DIM={settings.embedding_dim}。"
                        f"请在 .env 里改成 EMBEDDING_DIM={len(vec)}，"
                        f"然后重建索引：python -m scripts.seed_knowledge --force"
                    )
                out.append(l2_normalize(vec))
    _last_served_model = spec.model
    return out


def embed_many(texts: list[str], dim: int | None = None, *, strict: bool = False) -> list[list[float]]:
    """批量向量化。

    - provider=hash：本地哈希；
    - provider=http：调远端。
      * `strict=False`（默认，用于**查询**）：按链回退（主层 → 回退层），
        全失败则回落哈希，保证"零 Key / 断网也能跑通"；
      * `strict=True`（用于**建索引**）：**只用主层，失败直接抛错**。
        原因：建索引时回退到另一个模型，会让同一个索引里混入**两种向量空间**的向量
        （维度可能一样，但余弦相似度没有意义），检索静默劣化且完全看不出来。
        宁可索引任务整体失败，让你明确处置。
      * 维度不一致一律抛错（见 EmbeddingDimMismatch），任何情况都不回退。
    """
    items = list(texts or [])
    if not items:
        return []
    if settings.embedding_provider == "http":
        specs = [index_spec()] if strict else settings.embedding_chain
        spec_list = [s for s in specs if s is not None]
        if not spec_list:
            raise RuntimeError("EMBEDDING_PROVIDER=http，但没有可用的 base_url / api_key")
        errors: list[str] = []
        for spec in spec_list:
            try:
                result = _embed_with_spec(spec, items)
                if errors:
                    logger.warning("embedding 已回退到 %s（%s）：%s", spec.name, spec.model, "；".join(errors[-2:]))
                return result
            except EmbeddingDimMismatch:
                raise
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{spec.name}({spec.model}): {type(exc).__name__}: {str(exc)[:120]}")
                if strict:
                    raise RuntimeError(
                        "建索引失败且**不允许回退**（回退会让同一索引混入两个模型的向量，"
                        "检索会静默劣化）。请确认主层可用后重跑，或把主层换成可用的模型：\n  "
                        + "；".join(errors)
                    ) from exc
        raise RuntimeError("embedding 全部层失败：" + "；".join(errors))
    return [_hash_embed(text, dim) for text in items]


def embed(text: str, dim: int | None = None) -> list[float]:
    return embed_many([text], dim=dim)[0]


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """向量已归一化，点积即余弦；此处仍做防御性归一化。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def lexical_overlap(query: str, text: str) -> float:
    """词面重合度，作为向量分的补充信号（对短关键词检索更稳）。"""
    q = set(tokenize(query))
    if not q:
        return 0.0
    t = set(tokenize(text))
    if not t:
        return 0.0
    return len(q & t) / len(q)
