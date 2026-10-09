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
import math
import re

from ..config import settings

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


def embed(text: str, dim: int | None = None) -> list[float]:
    dim = dim or settings.embedding_dim
    vec = [0.0] * dim
    for token in tokenize(text):
        idx, sign = _bucket(token, dim)
        vec[idx] += sign
    return l2_normalize(vec)


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
