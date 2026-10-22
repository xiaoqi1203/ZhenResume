"""知识库 RAG：切片、入库、检索。

检索打分 = 0.6 × 向量余弦 + 0.4 × 词面重合度。
纯向量对短查询（如「Kubernetes」）召回不稳，混合打分的工程性价比更高。
MVP 在 Python 侧做暴力检索；换 pgvector 后改为 SQL `ORDER BY embedding <=> :q` 即可。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import KnowledgeChunk, KnowledgeDoc
from . import embeddings

logger = logging.getLogger(__name__)

_CHUNK_TARGET = 420
_CHUNK_OVERLAP = 60


def split_chunks(content: str, *, target: int = _CHUNK_TARGET, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """按段落聚合成定长切片，带少量重叠以保留跨段语义。

    ⚠️ 这是**兜底策略**，对结构化文档效果差。见 `split_chunks_by_heading`：
    它按 `## 标题` 切分，能避免"官方定义"与"官方任务"混在一片里互相稀释。

    为什么保留本函数：既有 11 篇自撰文档没有 `##` 标题结构，行为必须逐位不变
    （`verify.py` 有断言依赖切片数量与排序）。
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[#\-*\d])", content or "") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if buf and len(buf) + len(para) + 1 > target:
            chunks.append(buf)
            buf = (buf[-overlap:] + "\n" + para) if overlap else para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


# --------------------------------------------------------------------------- #
# 结构化切片（按标题切分）
# --------------------------------------------------------------------------- #
# ## 为什么需要它（实测依据）
#
# 扩库后 O*NET 文档被切成 3 片，其中 **#0 同时含"官方定义"与"任务清单开头"**：
#
#   #0 (407字) = "O*NET-SOC 15-1299.08 … 官方定义：为复杂应用问题…" + "## 官方任务（28 项）" 的前几条
#   #1 (380字) = 任务清单续 + "## 对简历的含义"
#   #2 (127字) = 含义段续
#
# 定义段含大量与 JD 重合的高频词（**系统 / 设计 / 开发 / 架构**），
# 于是 #0 在"和 JD 有多像"上天然占优 —— 但它回答的是"这个岗位是什么"，
# 不是"简历该怎么写"。真正有用的任务片被挤到后面。
#
# 按 `## 标题` 切开后，定义片与任务片各自独立参与排序，
# "JD 里的词"与"结论需要的依据"才有机会分别命中。
_HEADING_RE = re.compile(r"^#{1,3}\s+\S", re.MULTILINE)


def split_chunks_by_heading(
    content: str,
    *,
    target: int = _CHUNK_TARGET,
    overlap: int = _CHUNK_OVERLAP,
) -> list[str]:
    """按 Markdown 标题切分：每个 `## 小节` 一个切片，超长的再按段落聚合。

    ## 切分规则

    1. 以 `#`/`##`/`###` 标题为界切段；
    2. 标题前的导语（通常是"官方定义"）**单独成片**，不与后面的任务清单混合；
    3. 单节超过 `target` 字时，按段落聚合继续切（复用 `split_chunks` 的逻辑）；
    4. 整篇没有标题 → 退化为 `split_chunks`（行为与旧版一致）。

    ## 边界

    - 标题本身**保留在切片开头**：检索时"官方任务"这个词本身就是强信号；
    - 不做 overlap：跨节的语义重叠在结构化文档里没意义（节与节主题本就不同）。
    """
    text = (content or "").strip()
    if not text:
        return []
    if not _HEADING_RE.search(text):
        return split_chunks(text, target=target, overlap=overlap)

    # 按标题位置切段；第一个标题之前的导语单独成片
    positions = [m.start() for m in _HEADING_RE.finditer(text)]
    sections: list[str] = []
    if positions[0] > 0:
        lead = text[: positions[0]].strip()
        if lead:
            sections.append(lead)
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        sec = text[start:end].strip()
        if sec:
            sections.append(sec)

    # 超长小节继续按段落聚合（不跨节）
    chunks: list[str] = []
    for sec in sections:
        if len(sec) <= target:
            chunks.append(sec)
        else:
            chunks.extend(split_chunks(sec, target=target, overlap=0))
    return chunks


def index_document(db: Session, doc: KnowledgeDoc) -> int:
    """把文档切片并写入向量，返回切片数。重复入库前会清掉旧切片。

    切片**批量**向量化（provider=http 时一次请求多条），比逐条调用快一个量级。
    """
    db.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == doc.id).delete(synchronize_session=False)

    # ★ 结构化切片：有 `##` 标题的文档按小节切（定义片 / 任务片 / 含义片各自独立），
    #   没有标题的退化为定长聚合（自撰的 11 篇行为逐位不变）。
    #   实测依据见 `split_chunks_by_heading` 的 docstring。
    chunks = split_chunks_by_heading(doc.content or "")
    # strict=True：建索引只走主层、不允许回退（回退会让同一索引混入两个模型的向量）
    vectors = embeddings.embed_many(chunks, strict=True)
    served_model = embeddings.last_served_model()
    for i, (text, vector) in enumerate(zip(chunks, vectors)):
        db.add(
            KnowledgeChunk(
                doc_id=doc.id,
                chunk_text=text,
                embedding=vector,
                doc_metadata={
                    "index": i, "title": doc.title, "scope": doc.scope,
                    # 记下这个向量是哪个模型产的：换模型后没重建索引时，
                    # 检索侧靠它判断"查询向量与库内向量不是同一个模型"并降级
                    "embedding_model": served_model,
                    **(doc.doc_metadata or {}),
                },
            )
        )
    db.flush()
    return len(chunks)


def search(
    db: Session,
    query: str,
    *,
    top_k: int = 5,
    scope: str | None = None,
    role_family: str | None = None,
    min_score: float = 0.05,
    rerank: bool = False,
    rerank_weight: float = 0.5,
    mmr_lambda: float = 0.0,
) -> list[dict]:
    """混合检索，返回 [{chunk_id, doc_id, title, scope, role_family, score, text}]，按相关度降序。

    `role_family`（V2.0 新增，默认 None = 行为与改动前逐位相同）：
    按岗位族过滤知识库。该列在 `knowledge_docs` 上**本来就有索引**、`search` 也一直
    返回它，但此前没有任何调用点传过 —— 属于"能力已就绪、从未接线"。
    语义判定 Agent 与后续 agentic 检索会用它把候选集中到目标岗位族。

    ## 重排参数（`rerank`，默认 False = 行为与改动前逐位相同）

    - `rerank=True`：先取 `top_k * RERANK_POOL` 个候选，再用 **BM25** 重排。
      BM25 与初排的 `lexical_overlap` 关键差别：**带 IDF**（稀有技术词权重高）与
      **长度归一**（长切片不再天然占优）。实测初排的朴素词面分会让"包含大量常见词的
      长切片"挤掉"短但精准命中技术词"的切片 —— 这正是引用命中率只有 33–37% 的原因之一。
    - `rerank_weight`：BM25 分在最终分里的权重（其余给原混合分）。
      需要先把 BM25 归一到 0–1（同一批候选内 min-max），否则两者尺度不可比。
    - `mmr_lambda`：>0 时启用 MMR 多样性去重（0 = 关闭）。MMR 惩罚"与已选结果相似"的候选，
      用来解决**同一文档多个切片霸榜**——实测扩库后同一篇文档常占 top-5 的 2–3 席。
    """
    stmt = select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
    if scope:
        stmt = stmt.where(KnowledgeDoc.scope == scope)
    if role_family:
        # ⚠️ 只匹配 role_family==目标岗位族 是**错的**：知识库里 compliance / industry /
        # resume_template 这三类文档的 role_family 是 "general"，它们对任何岗位族都适用。
        # 实测（eval_rag）：只匹配精确值会让"表达结构 / ATS 机制 / 公平性红线"永远检索不到，
        # Agent 会如实报"该方向未命中检索结果"，看起来像模型不努力，实际是检索器过滤掉了。
        stmt = stmt.where(
            or_(
                KnowledgeDoc.role_family == role_family,
                KnowledgeDoc.role_family == "general",
                KnowledgeDoc.role_family.is_(None),
            )
        )

    rows = db.execute(stmt).all()
    if not rows:
        return []

    query_vec = embeddings.embed(query)

    # 一致性守卫（两类都会让 cosine 恒为 0 或失去意义）：
    # ① 维度不一致：换了 embedding 模型但没重建索引；
    # ② **模型不一致**：查询向量由 A 模型产生、库内向量由 B 模型产生 ——
    #    维度可能相同，但向量空间不同，余弦相似度没有意义。
    # 两种情况都退化为**纯词面检索**，保证 RAG 不整体失效，并在日志里明确报出来。
    stored_models = {
        (c.doc_metadata or {}).get("embedding_model")
        for c, _d in rows
        if (c.doc_metadata or {}).get("embedding_model")
    }
    served = embeddings.last_served_model()
    vector_usable = True
    for chunk, _doc in rows:
        if chunk.embedding and len(chunk.embedding) != len(query_vec):
            vector_usable = False
            break
    if vector_usable and stored_models and served and served not in stored_models:
        vector_usable = False
        logger.warning(
            "查询向量来自 %s，但库里是 %s —— 不是同一个模型，纯向量比较无效，"
            "本次退化为纯词面检索。换模型后必须重建索引：python -m scripts.seed_knowledge --force",
            served, "、".join(sorted(stored_models)),
        )
    if not vector_usable and not stored_models:
        logger.warning(
            "查询向量 %d 维与库内向量维度不一致，本次退化为纯词面检索。"
            "请检查 EMBEDDING_* 配置与 EMBEDDING_DIM，并重建索引："
            "python -m scripts.seed_knowledge --force",
            len(query_vec),
        )

    hits: list[dict] = []
    for chunk, doc in rows:
        text = chunk.chunk_text or ""
        lex_score = embeddings.lexical_overlap(query, text)
        if vector_usable:
            score = 0.6 * max(0.0, embeddings.cosine(query_vec, chunk.embedding)) + 0.4 * lex_score
        else:
            score = lex_score
        if score < min_score:
            continue
        hits.append(
            {
                "chunk_id": chunk.id,
                "doc_id": doc.id,
                "title": doc.title or "",
                "scope": doc.scope or "",
                "role_family": doc.role_family or "",
                "score": round(min(score, 1.0), 4),
                "text": text,
            }
        )

    hits.sort(key=lambda h: h["score"], reverse=True)

    if not rerank and mmr_lambda <= 0:
        return hits[:top_k]

    # ---- 重排：先取更大的候选池，再按 BM25 / MMR 重排 ----
    pool = hits[: max(top_k * RERANK_POOL, top_k)]
    if not pool:
        return []

    if rerank:
        # BM25 的 IDF 必须按**全库**统计（rows），不能只按候选池 —— 否则稀有词被低估。
        all_texts = [(c.chunk_text or "") for c, _d in rows]
        all_bm25 = embeddings.bm25_scores(query, all_texts)
        bm25_by_id = {c.id: s for (c, _d), s in zip(rows, all_bm25)}

        # 归一到 0–1（同一批候选内 min-max）：BM25 与混合分尺度不同，不归一无法加权
        raw = [bm25_by_id.get(h["chunk_id"], 0.0) for h in pool]
        lo, hi = min(raw), max(raw)
        span = (hi - lo) or 1.0
        for h, b in zip(pool, raw):
            b_norm = (b - lo) / span
            h["bm25"] = round(b, 4)
            # 最终分 = 原混合分 × (1-w) + BM25 归一 × w
            h["score"] = round(h["score"] * (1 - rerank_weight) + b_norm * rerank_weight, 4)

    if mmr_lambda > 0:
        pool = _mmr_select(pool, top_k=top_k, lam=mmr_lambda, query=query)
    else:
        pool.sort(key=lambda h: h["score"], reverse=True)

    return pool[:top_k]


# --------------------------------------------------------------------------- #
# 重排辅助
# --------------------------------------------------------------------------- #
# 候选池倍数：先取 top_k×3 个再重排。太小（如 ×1）重排无从改变顺序；
# 太大则 BM25 统计与 MMR 计算的成本上升，而收益递减。
RERANK_POOL = 3


def _jaccard(a: str, b: str) -> float:
    """两段文本的 token 级 Jaccard 相似度（用于 MMR 去冗余）。"""
    ta = set(embeddings.tokenize(a))
    tb = set(embeddings.tokenize(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _mmr_select(hits: list[dict], *, top_k: int, lam: float, query: str) -> list[dict]:
    """Maximal Marginal Relevance 选择：相关度高 **且** 与已选结果不重复。

    `lam` 是相关性权重（1.0 = 纯相关性，0 = 纯多样性）：
        score = lam * relevance - (1 - lam) * max_similarity_to_selected

    ## 为什么需要它（实测依据）

    扩库后（43 切片）同一篇文档常占 top-5 的 2–3 席：
    O*NET 的"软件开发者"一篇就有 3 个切片，它们的用词高度重叠。
    结果是一个维度的依据挤掉了其他维度 —— 而 gold 标注的恰恰是"多维度各一条"。
    MMR 是解决这个问题的标准做法，且**不需要任何模型**。
    """
    remaining = list(hits)
    selected: list[dict] = []
    while remaining and len(selected) < top_k:
        best = None
        best_score = float("-inf")
        for cand in remaining:
            rel = float(cand.get("score") or 0.0)
            if selected:
                sim = max(_jaccard(cand.get("text") or "", s.get("text") or "") for s in selected)
            else:
                sim = 0.0
            score = lam * rel - (1 - lam) * sim
            if score > best_score:
                best_score = score
                best = cand
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)
    return selected


# --------------------------------------------------------------------------- #
# 多视角 query 融合（单 query 的结构性上限的正解）
# --------------------------------------------------------------------------- #
# 实测（2026-10-21，43 切片库，10 例）：
#
#   单 query（JD 宽 query）         recall@5 = 89.2%
#   BM25 重排（权重 0.3/0.5/0.7）   recall@5 = 89.2%   ← 一点没动
#   MMR 去重                        recall@5 = 58.3%   ← 严重劣化
#
# 为什么重排无效？把"没被召回"的 gold 切片打出来看：
#   - 3bf866ba（经历要点的黄金表达结构 第 2 片）内容 = "禁用词清单：负责、参与、协助…"
#   - abd7488a（ATS 关键词匹配 第 1 片）内容 = "主流 ATS 处理简历的流程：格式解析→字段抽取…"
# 这两片**根本不含 JD 的任何技能词** —— 它们回答的是"怎么表达""机器怎么读"，
# 不是"这个岗位要什么"。**单 query 无论怎么重排都排不上来，因为信号不存在。**
#
# 而 MMR 更糟：gold 的定义本身就是"同一篇文档的多个切片"（能力模型 2 片 +
# 表达结构 1–2 片），去重等于**按定义惩罚 gold**。这不是参数问题，是目标冲突。
#
# 所以真正的杠杆是**多视角 query**：一个 query 问"岗位要什么"，另外几个分别问
# "怎么量化""机器怎么读""合规红线是什么"。这正是 Agent 多轮在做的事，
# 这里把它做成**确定性版本**（不花 LLM 钱），用于 fixed 线与评测对照。

# 四个视角各一条 query 模板。用 {title} 代入岗位名，其余是领域无关的固定问法 ——
# 之所以不用"后端量化标准"这种带岗位族的具体词，是因为知识库的这类文档
# role_family 就是 general，问"量化"比问"后端量化"召回更稳。
PERSPECTIVE_QUERIES: tuple[str, ...] = (
    "{title} 岗位能力模型 核心能力 分层 技能要求",
    "经历要点 量化指标 表达结构 强动词 结果 规模 性能 稳定性",
    "ATS 关键词匹配 简历解析 格式 章节 字段抽取 机器筛选",
    "招聘公平性 受保护特征 合规 敏感信息 歧视 与岗位无关",
)


def perspective_queries(job_title: str) -> list[str]:
    """按四个视角生成 query（用于多视角融合检索）。"""
    title = (job_title or "目标岗位").strip()
    return [q.format(title=title) for q in PERSPECTIVE_QUERIES]


def search_multi(
    db: Session,
    queries: list[str],
    *,
    top_k: int = 5,
    role_family: str | None = None,
    per_query: int | None = None,
) -> list[dict]:
    """**多视角检索**：每条 query 各检索一次，用 RRF 融合。

    ## 与 `search_scoped` 的区别（两者都是 RRF，但目的相反）

    - `search_scoped` 是"**同一个** query 按 scope 分组排序再融合" —— 想在不换 query 的
      前提下增加覆盖面。实测失败（30.8%），因为 scope 分配与 gold 分布不匹配。
    - `search_multi` 是"**不同的** query 各自全局排序再融合" —— 每条 query 问不同的问题，
      各自能命中自己那类文档。这是多轮 Agent 的确定性替代品。

    ## 为什么用 RRF

    不同 query 的分数尺度不同（问"合规"的命中分天然低），直接相加会让"岗位能力"类
    永远占优。RRF 只看排名，每条 query 的第一名都同等重要 —— 这正是"每个视角都要有代表"
    想要的效果。
    """
    if not queries:
        return []
    per_query = per_query or max(top_k, 3)

    fused: dict[str, float] = {}
    payload: dict[str, dict] = {}
    for q in queries:
        if not (q or "").strip():
            continue
        hits = search(db, q, top_k=per_query, role_family=role_family)
        for rank, h in enumerate(hits, start=1):
            cid = h["chunk_id"]
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)
            payload.setdefault(cid, h)

    # 主排序 RRF + 次级排序原始分（RRF 各 query 第 1 名同分，需要次级键区分）
    ordered = sorted(fused.items(), key=lambda kv: (kv[1], payload[kv[0]]["score"]), reverse=True)
    out: list[dict] = []
    for cid, rrf in ordered[:top_k]:
        item = dict(payload[cid])
        item["score"] = round(rrf, 6)
        out.append(item)
    return out


def build_query(parsed_jd: dict | None, parsed_resume: dict | None) -> str:
    """用 JD 的岗位名 + 技能 + 关键词拼检索 query。"""
    jd = parsed_jd or {}
    parts: list[str] = []
    for key in ("title", "seniority"):
        if jd.get(key):
            parts.append(str(jd[key]))
    for key in ("skills_core", "keywords", "responsibilities"):
        values = jd.get(key) or []
        if isinstance(values, list):
            parts.extend(str(v) for v in values[:6])
    skills = (parsed_resume or {}).get("skills") or []
    parts.extend(str(s) for s in skills[:8])
    return " ".join(parts)[:600]


# --------------------------------------------------------------------------- #
# Scope-aware 融合检索（query 改写/扩展的落地形式）
# --------------------------------------------------------------------------- #
# 知识库的 4 个 scope 对应 4 类**互不重叠**的依据。实测（eval_rag 对齐口径）：
# Agent 自拟的窄 query 第 1 轮 gold 覆盖只有 55%，而 fixed 的 JD 宽 query 有 89.2%
# —— 差距的来源不是"query 写得好不好"，而是**窄 query 只覆盖 1–2 个 scope**。
#
# 所以这里不做 LLM 改写（贵且不确定），而是把"补查其他维度"这件事**下沉到检索器**：
# 同一个 query 在每个 scope 内各排一次序，再用 RRF 融合。
# 好处：
# 1. **零额外 LLM 成本**；embedding 只算一次（复用同一 query 向量），SQL 也只查一次；
# 2. **结构性保证覆盖面** —— 每个 scope 都有代表进入结果，不再靠提示词求模型"记得查"；
# 3. RRF 只用排名不用分数，跨 scope 的分数尺度差异不影响融合（这点很关键：
#    不同 scope 的 chunk 长度/用词密度不同，直接比分数会系统性偏袒某一类）。
DEFAULT_SCOPES: tuple[str, ...] = ("role", "resume_template", "industry", "compliance")

# RRF 常数：标准值 60（Cormack et al.）。作用是把排名压成平缓的分数，
# 避免第 1 名独大、后面几名拿不到分。
RRF_K = 60


def _rrf_rank(ids: list[str]) -> dict[str, float]:
    """把有序 id 列表转成 RRF 分数：1/(k + rank)，rank 从 1 开始。"""
    return {cid: 1.0 / (RRF_K + i) for i, cid in enumerate(ids, start=1)}


def search_scoped(
    db: Session,
    query: str,
    *,
    top_k: int = 5,
    role_family: str | None = None,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
    per_scope: int | None = None,
    max_per_doc: int = 2,
    extra_queries: list[str] | None = None,
    scope_quota: int = 0,
) -> list[dict]:
    """**多维度融合检索**：每个 scope 内各排一次序 → RRF 融合 → 截断。

    ## 与 `search()` 的关系

    `search()` 是"单 query 全局排序"，行为一字不改（大量既有调用点依赖它）。
    本函数是它的**扩展**：多一次按 scope 分组排序 + RRF 融合，用于 agent 的工具层。

    ## 参数

    - `per_scope`：每个 scope 取前几名参与融合。默认 `ceil(top_k / len(scopes)) + 1`，
      保证融合后总候选数 > top_k（否则截断时没有选择余地）。
    - `max_per_doc`：同一文档最多占几条。**防止单篇长文档霸榜** ——
      实测出现过同一篇文档的 2 个切片占满前 2 名，挤掉其他维度的依据。
    - `extra_queries`：额外查询，各自跑一次全局检索后也参与 RRF 融合。
      用于把"JD 宽 query"（高覆盖）与"Agent 窄 query"（高精度）合起来 ——
      这是 query 扩展的最简形式，且**不需要 LLM**。
    - `scope_quota`：**每个 scope 保底占几席**。默认 **0 = 不保底**。

    ## ⚠️ 为什么 `scope_quota` 默认是 0（实测否决了"保底席位"）

    最初的设计是"每个 scope 保底 1 席"，理由是 gold 通常横跨 2–3 类依据。
    但 `scripts/eval_retrieval.py` 实测（10 例，top_k=5）：

        单 query 全局排序      recall 89.2%   scope 覆盖 3.00
        scope 融合 + 保底 1 席 recall 58.3%   scope 覆盖 4.00   ← recall 掉 30.8pp
        scope 融合（无保底）   recall 89.2%   scope 覆盖 3.00

    **根因**：gold 切片集中在 2 个文档（能力模型 2 个切片 + 表达结构 1 个），
    而保底席位把 2 个名额送给了 industry/compliance（它们不在 gold 里）。
    在 top_k=5 的预算下，"强制 4 类各一席"必然挤掉同文档的第 2 个 gold 切片。

    结论：**覆盖面不是越多越好，而是"不要漏掉相关的那几类"**。
    所以默认不保底；需要广度的场景（例如合规审查）可以显式传 `scope_quota=1`。

    ## 为什么用 RRF 而不是加权分数和

    4 个 scope 的切片在长度、术语密度上差异很大，余弦分与词面分不在同一尺度。
    直接相加会让某一类系统性占优。RRF 只看**排名**，对尺度不敏感。
    """
    if per_scope is None:
        per_scope = max(2, (top_k + len(scopes) - 1) // len(scopes) + 1)

    # ① 一次性取回候选（role_family 过滤与 search() 同口径）
    stmt = select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
    if role_family:
        stmt = stmt.where(
            or_(
                KnowledgeDoc.role_family == role_family,
                KnowledgeDoc.role_family == "general",
                KnowledgeDoc.role_family.is_(None),
            )
        )
    rows = db.execute(stmt).all()
    if not rows:
        return []

    # ② embedding 只算一次（所有 scope 复用同一 query 向量，这是本方案便宜的关键）
    query_vec = embeddings.embed(query)
    vector_usable = True
    for chunk, _doc in rows:
        if chunk.embedding and len(chunk.embedding) != len(query_vec):
            vector_usable = False
            break
    stored_models = {
        (c.doc_metadata or {}).get("embedding_model")
        for c, _d in rows
        if (c.doc_metadata or {}).get("embedding_model")
    }
    served = embeddings.last_served_model()
    if vector_usable and stored_models and served and served not in stored_models:
        vector_usable = False

    # ③ 预计算每条 chunk 的分数（只算一次，供所有分组复用）
    scored: list[tuple[float, KnowledgeChunk, KnowledgeDoc]] = []
    for chunk, doc in rows:
        text = chunk.chunk_text or ""
        lex = embeddings.lexical_overlap(query, text)
        vec = max(0.0, embeddings.cosine(query_vec, chunk.embedding)) if vector_usable else 0.0
        score = 0.6 * vec + 0.4 * lex if vector_usable else lex
        scored.append((score, chunk, doc))

    # ④ 每个 scope 内排序取前 per_scope，喂给 RRF
    fused: dict[str, float] = {}
    payload: dict[str, tuple[KnowledgeChunk, KnowledgeDoc, float]] = {}
    for scope in scopes:
        group = sorted(
            ((s, c, d) for s, c, d in scored if (d.scope or "") == scope),
            key=lambda t: t[0],
            reverse=True,
        )[:per_scope]
        for rank, (s, c, d) in enumerate(group, start=1):
            fused[c.id] = fused.get(c.id, 0.0) + 1.0 / (RRF_K + rank)
            payload.setdefault(c.id, (c, d, s))

    # ⑤ 额外 query 的全局排序也参与融合（把宽 query 的覆盖率与窄 query 的精度合起来）
    for extra in extra_queries or []:
        if not extra.strip() or extra.strip() == query.strip():
            continue
        extra_vec = embeddings.embed(extra)
        extra_scores: list[tuple[float, KnowledgeChunk, KnowledgeDoc]] = []
        for chunk, doc in rows:
            text = chunk.chunk_text or ""
            lex = embeddings.lexical_overlap(extra, text)
            vec = max(0.0, embeddings.cosine(extra_vec, chunk.embedding)) if vector_usable else 0.0
            extra_scores.append((0.6 * vec + 0.4 * lex if vector_usable else lex, chunk, doc))
        top = sorted(extra_scores, key=lambda t: t[0], reverse=True)[: max(top_k, per_scope * len(scopes))]
        for rank, (s, c, d) in enumerate(top, start=1):
            fused[c.id] = fused.get(c.id, 0.0) + 1.0 / (RRF_K + rank)
            payload.setdefault(c.id, (c, d, s))

    # ⑥ 排序 + 选取。三条规则：
    #
    # (a) 保底席位由 `scope_quota` 控制，**默认 0（关闭）** —— 实测"每 scope 保底 1 席"
    #     会让 recall 从 89.2% 掉到 58.3%，因为 gold 集中在 2 个文档，
    #     强制 4 类各占一席必然挤掉同文档的第二个 gold 切片。详见 docstring。
    # (b) 主排序键 RRF + 次级键**原始混合分**：RRF 的固有特性是各 scope 第 1 名同分
    #     （都是 1/(60+1)），只按 RRF 排会退化成 dict 插入顺序（= scope 声明顺序）。
    # (c) 同文档最多 `max_per_doc` 条：防止单篇长文档的多个切片霸榜。
    ordered = sorted(
        fused.items(),
        key=lambda kv: (kv[1], payload[kv[0]][2]),
        reverse=True,
    )

    hits: list[dict] = []
    taken: set[str] = set()
    per_doc: dict[str, int] = {}

    def _emit(cid: str) -> bool:
        """尝试收录一条；返回是否真的收进去了。"""
        if cid in taken:
            return False
        chunk, doc, raw = payload[cid]
        doc_key = str(doc.id)
        if per_doc.get(doc_key, 0) >= max_per_doc:
            return False
        per_doc[doc_key] = per_doc.get(doc_key, 0) + 1
        taken.add(cid)
        hits.append(
            {
                "chunk_id": chunk.id,
                "doc_id": doc.id,
                "title": doc.title or "",
                "scope": doc.scope or "",
                "role_family": doc.role_family or "",
                # score 用 RRF 分（范围小，不适合当"相关度百分比"展示）；
                # 原始混合分单独给出，便于调试与阈值判断
                "score": round(fused[cid], 6),
                "raw_score": round(min(raw, 1.0), 4),
                "text": chunk.chunk_text or "",
            }
        )
        return True

    # (a) 保底席位（`scope_quota`，默认 0 = 关闭，见 docstring 的实测依据）
    if scope_quota > 0:
        for scope in scopes:
            group = sorted(
                ((s, c) for s, c, d in scored if (d.scope or "") == scope),
                key=lambda t: t[0],
                reverse=True,
            )
            for _s, chunk in group[:scope_quota]:
                if len(hits) >= top_k:
                    break
                _emit(chunk.id)
            if len(hits) >= top_k:
                break

    # (b)(c) 剩余名额按 RRF 排序补足
    for cid, _rrf in ordered:
        if len(hits) >= top_k:
            break
        _emit(cid)

    return hits
