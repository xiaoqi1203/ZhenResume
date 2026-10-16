"""批量去重（方案 v2 §1）：L1 文件级 + L2 内容级。

**为什么需要**：HR 一次导入 100 份，重复很常见（同一个人被两个渠道推了两遍、同一份简历
导成 PDF 和 DOCX 各传一次、改个文件名重传）。代价不只是白花钱：

1. 每份重复都重跑一次分析（¥0.0213/份）；
2. **更严重的是排名被污染** —— 同一个人占两个名次，HR 看到的排序是假的；
3. 而"这个人投了 3 次"本身就是风险信号，现在完全看不到。

**关键设计：去重 ≠ 静默丢弃**
HR 场景下"重复投递"要被看见，所以命中时保留新记录、只标 `duplicate_of` 与级别，
并在排序表上显式标注。**绝不删除、绝不隐藏。**

**唯一性范围**：只在"同一 user + 同一 hash"内判定。同一文件被两个用户各传一份是两份
独立数据（多租户下互相不可见），所以**不加全局唯一约束**。

L3 语义级去重（向量相似度 > 0.98）等 pgvector 上线后再加，见 `find_duplicate` 的 TODO。
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Resume

# 归一化时要去掉的排版噪声：空白、标点、项目符号、常见页眉页脚
_PUNCT_RE = re.compile(r"[\s\u3000·・•▪◦●○■□◆◇\-—_=~*|/\\<>\[\]{}()（）【】「」『』《》〈〉:：;；,，.。!！?？'\"“”‘’]+")
# 页眉页脚常见干扰（页码、日期、简历/个人简历标题）
_NOISE_RE = re.compile(r"(第\s*\d+\s*页|共\s*\d+\s*页|page\s*\d+(?:\s*of\s*\d+)?|个人简历|求职简历|简\s*历|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)", re.IGNORECASE)


def normalize_text(text: str | None) -> str:
    """归一化文本，用于 L2 比对。

    目标：同一份简历换了排版/导出格式后，归一化结果一致。
    - 去空白与标点（排版差异）
    - 去页眉页脚噪声（页码、日期、"个人简历"标题）
    - 全角转半角（字母数字）
    - 统一小写
    """
    if not text:
        return ""
    s = str(text)
    # 全角字母数字 → 半角
    s = "".join(
        chr(ord(ch) - 0xFEE0) if 0xFF01 <= ord(ch) <= 0xFF5E else ch
        for ch in s
    )
    s = _NOISE_RE.sub("", s)
    s = _PUNCT_RE.sub("", s)
    return s.lower()


def compute_hashes(text: str | None, raw_bytes: bytes | None = None) -> tuple[str, str]:
    """返回 `(file_sha256, content_sha256)`。

    - `file_sha256`：原始文件字节；没有字节（编辑器创建的简历）时用文本 UTF-8 兜底，
      这样同一份编辑器简历重复提交也能被 L1 拦住。
    - `content_sha256`：归一化文本；空文本返回空串（**不要用空串去匹配**，否则所有空简历
      都会互相命中，见 `find_duplicate` 的守卫）。
    """
    payload = raw_bytes if raw_bytes is not None else (text or "").encode("utf-8")
    file_sha = hashlib.sha256(payload).hexdigest()
    normalized = normalize_text(text)
    content_sha = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""
    return file_sha, content_sha


def find_duplicate(
    db: Session,
    user_id: str | None,
    file_sha256: str | None,
    content_sha256: str | None,
) -> tuple[Resume | None, str]:
    """在同一 user 范围内查重，返回 `(命中的原简历 | None, 级别 'L1'|'L2'|'')`。

    先按 L1（文件字节）再按 L2（归一化内容）判定 —— L1 更便宜也更精确。

    ⚠️ 两个守卫：
    1. 空 hash 直接返回未命中（否则所有空简历会互相命中）；
    2. 只在自己不是"被复用的副本"的原简历里匹配，避免 A→B→C 的链式指向，
      让所有副本都指向同一个源头。
    """
    if user_id is None:
        return None, ""

    base = select(Resume).where(Resume.user_id == user_id, Resume.duplicate_of.is_(None))

    if file_sha256:
        hit = db.execute(base.where(Resume.file_sha256 == file_sha256)).scalars().first()
        if hit is not None:
            return hit, "L1"
    if content_sha256:
        hit = db.execute(base.where(Resume.content_sha256 == content_sha256)).scalars().first()
        if hit is not None:
            return hit, "L2"

    # TODO(L3)：pgvector 上线后，这里再加一层"向量相似度 > 0.98"的语义判定
    return None, ""


def recent_report_id(
    db: Session,
    resume_id: str,
    jd_id: str | None = None,
    within_days: int = 7,
) -> str | None:
    """取该简历在 `within_days` 天内最新的一份报告 id；没有则 None。

    用途：命中重复时**直接复用报告**，省一次 LLM 开销。

    ⚠️ **必须同时匹配 jd_id**：报告是"这份简历 vs 那个 JD"的结论，
    换一个 JD 复用就是错的（会给出不相关的匹配度与缺口）。
    传 `jd_id=None` 时只按简历 + 时间筛选（仅用于"最近有没有诊断过"这类判断）。
    """
    from datetime import datetime, timedelta, timezone

    from ..models import AnalysisReport, AnalysisTask

    cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)
    stmt = (
        select(AnalysisReport.id)
        .join(AnalysisTask, AnalysisTask.id == AnalysisReport.task_id)
        .where(AnalysisTask.resume_id == resume_id, AnalysisTask.created_at >= cutoff)
    )
    if jd_id is not None:
        stmt = stmt.where(AnalysisTask.jd_id == jd_id)
    return db.execute(stmt.order_by(AnalysisTask.created_at.desc()).limit(1)).scalar_one_or_none()
