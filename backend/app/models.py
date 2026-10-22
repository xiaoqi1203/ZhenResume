"""ORM 模型 —— 对齐 aaa.md《三、数据库设计》的 DDL 草案。

约定：
1. 表名、列名与 DDL 完全一致，便于直接迁移到 PostgreSQL + pgvector。
2. 主键在 Python 侧生成 uuid4（DDL 用 gen_random_uuid()，换 PG 时可改回服务端默认值）。
3. 少量列是契约需要的扩展，均在注释里标了 `[扩展]`，迁移时需一并补上。
4. SQLite 没有 VECTOR 类型，`knowledge_chunks.embedding` 先用 JSON 存向量；
   换 PG 时改为 `Vector(1536)` 并建 HNSW 索引即可，检索层代码不用动。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 用户与组织
# --------------------------------------------------------------------------- #
class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str | None] = mapped_column(String(255))
    plan: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # jobseeker / hr / admin
    role: Mapped[str] = mapped_column(String(20), default="jobseeker")
    org_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("organizations.id"))
    # 匿名设备标识：账号体系上线前用它做隔离；账号绑定后仍然保留，
    # 用于「同一个浏览器登录前后」的数据延续（见 api/deps.py 的鉴权双通道）。
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    free_quota_used: Mapped[int] = mapped_column(Integer, default=0)
    # [扩展] 账号体系：邮箱/密码是否由「手机验证码首次登录」自动生成（占位乱码）
    auto_created: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # [扩展] 会话版本号：改密 / 强制下线时 +1，旧 JWT 立即失效
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


# --------------------------------------------------------------------------- #
# 简历 / JD
# --------------------------------------------------------------------------- #
class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(255))
    file_url: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    parsed_json: Mapped[dict | None] = mapped_column(JSON)
    # [扩展] 原始文件名 / MIME / 大小，便于前端展示与错误排查
    file_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    # ---- 去重（方案 v2 §1）----
    # L1：原始文件字节 sha256；L2：归一化文本 sha256（解决"同一简历导成 PDF 和 DOCX 各传一次"）。
    # ⚠️ 不加全局唯一约束：同一文件被不同用户各传一份是两份数据，多租户下不能互相可见；
    #    唯一性只在"同一 user + 同一 hash"范围内判定（见 services/dedup.py）。
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    # 命中去重时指向被复用的那份简历
    duplicate_of: Mapped[str | None] = mapped_column(String(36), ForeignKey("resumes.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tasks: Mapped[list["AnalysisTask"]] = relationship(back_populates="resume", cascade="all, delete-orphan")


class RoleStats(Base):
    """岗位族聚合统计（方案 v2 §2）：**只存聚合量，永不存简历原文**。

    这是 HR 侧数据唯一被允许落库的形态。为什么这样合法：
    - 存进去的是"该岗位族所有高分简历的平均要点字数"这类**聚合统计量**，个体在聚合中消失；
    - 配合 `sample_size >= 10` 闸门（见 scripts/aggregate_stats.py），
      避免"某格只有 1 条 → 反推出那个人"的可识别性。

    两个消费者：
    1. 内部 —— 生成/校准知识库里的「岗位能力模型」（替换手写文档）；
    2. 外部 —— 诊断报告的"同岗位对标"栏（"你 68 字，同岗位高分 42 字"）。
    """

    __tablename__ = "role_stats"
    __table_args__ = (UniqueConstraint("role_family", "period", name="uq_role_stats_family_period"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    role_family: Mapped[str] = mapped_column(String(64))       # 后端 / 算法 / 前端 …
    period: Mapped[str] = mapped_column(String(8))             # '2026-Q1'
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    score_p75: Mapped[int | None] = mapped_column(Integer)      # 该格的"高分线"
    metrics: Mapped[dict | None] = mapped_column(JSON)          # 见 aggregate_stats.py 的 METRIC 说明
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JobDescription(Base):
    __tablename__ = "job_descriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(255))
    company: Mapped[str | None] = mapped_column(String(255))
    raw_text: Mapped[str | None] = mapped_column(Text)
    parsed_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tasks: Mapped[list["AnalysisTask"]] = relationship(back_populates="jd", cascade="all, delete-orphan")


# --------------------------------------------------------------------------- #
# 分析任务与报告
# --------------------------------------------------------------------------- #
class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    resume_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("resumes.id", ondelete="CASCADE"))
    jd_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("job_descriptions.id", ondelete="CASCADE"))
    # single / batch
    type: Mapped[str] = mapped_column(String(20), default="single")
    # fixed / agentic —— 单份诊断的分析模式（用户可在提交时切换）
    mode: Mapped[str] = mapped_column(String(20), default="fixed")
    # pending / running / done / failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # [扩展] 工作流进度与失败原因，供 4.2 轮询接口使用
    current_step: Mapped[str | None] = mapped_column(String(50))
    progress: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    resume: Mapped[Resume | None] = relationship(back_populates="tasks")
    jd: Mapped[JobDescription | None] = relationship(back_populates="tasks")
    steps: Mapped[list["WorkflowStep"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="WorkflowStep.created_at"
    )
    report: Mapped["AnalysisReport | None"] = relationship(back_populates="task", uselist=False, cascade="all, delete-orphan")


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("analysis_tasks.id", ondelete="CASCADE"))
    overall_score: Mapped[int | None] = mapped_column(Integer)
    ats_score: Mapped[int | None] = mapped_column(Integer)
    impact_score: Mapped[int | None] = mapped_column(Integer)
    clarity_score: Mapped[int | None] = mapped_column(Integer)
    match_score: Mapped[int | None] = mapped_column(Integer)
    # [扩展] DDL 只有 risk_level，契约还需要 risk 分值（越高越安全）
    risk_score: Mapped[int | None] = mapped_column(Integer)
    # red / yellow / green
    risk_level: Mapped[str | None] = mapped_column(String(10))
    summary: Mapped[str | None] = mapped_column(Text)
    report_url: Mapped[str | None] = mapped_column(Text)
    # [扩展] 契约中的 missing_keywords / action_items / knowledge_refs 落在这里
    extra_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    task: Mapped[AnalysisTask | None] = relationship(back_populates="report")
    findings: Mapped[list["ReportFinding"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class ReportFinding(Base):
    __tablename__ = "report_findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("analysis_reports.id", ondelete="CASCADE"))
    # ats / impact / clarity / risk / gap
    category: Mapped[str | None] = mapped_column(String(50))
    # red / yellow / green
    severity: Mapped[str | None] = mapped_column(String(10))
    # [扩展] 契约中每条 finding 有短标题，便于前端列表展示
    title: Mapped[str | None] = mapped_column(String(255))
    original_text: Mapped[str | None] = mapped_column(Text)
    issue: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    rewritten_text: Mapped[str | None] = mapped_column(Text)
    # {source, quote, span:[start,end], field}
    evidence_ref: Mapped[dict | None] = mapped_column(JSON)
    # [扩展] 排序权重，保证前端拿到的顺序稳定（red → yellow → green）
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    report: Mapped[AnalysisReport | None] = relationship(back_populates="findings")


class ReportShare(Base):
    """报告的分享令牌（只存 token 的 SHA-256，不存明文）。

    为什么要单独一张表而不是把 token 写进报告：
    - 可撤销（`revoked_at`）与可过期（`expires_at`）；
    - 一个报告可以有多条历史分享记录，便于审计访问次数；
    - 泄露库文件也拿不到可用的 token（只存 hash）。
    """

    __tablename__ = "report_shares"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("analysis_reports.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # 是否包含简历原文引用（默认不包含；用户勾选后才写入）
    include_raw: Mapped[bool] = mapped_column(Boolean, default=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    # 一个任务的一个步骤只能有一行：LangGraph 重放/续跑时靠它保证 upsert 语义，
    # 否则重复行会让 SUM(tokens)/SUM(cost) 虚高、成本看板失真。
    __table_args__ = (
        UniqueConstraint("task_id", "step_name", name="uq_workflow_step_task_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("analysis_tasks.id", ondelete="CASCADE"))
    step_name: Mapped[str | None] = mapped_column(String(100))
    # pending / running / done / failed / degraded
    status: Mapped[str | None] = mapped_column(String(20))
    input_json: Mapped[dict | None] = mapped_column(JSON)
    output_json: Mapped[dict | None] = mapped_column(JSON)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # [扩展] 降级原因 / 异常摘要，便于可观测
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    task: Mapped[AnalysisTask | None] = relationship(back_populates="steps")


# --------------------------------------------------------------------------- #
# 知识库 RAG
# --------------------------------------------------------------------------- #
class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # role / industry / resume_template / compliance
    scope: Mapped[str | None] = mapped_column(String(50), index=True)
    role_family: Mapped[str | None] = mapped_column(String(100), index=True)
    doc_type: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str | None] = mapped_column(Text)
    # 注意：ORM 属性不能叫 metadata，映射到列名 "metadata" 以对齐 DDL
    doc_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="doc", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    doc_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_docs.id", ondelete="CASCADE"))
    chunk_text: Mapped[str | None] = mapped_column(Text)
    # SQLite 下为 JSON 数组；换 PG 时改为 Vector(1536)
    embedding: Mapped[list | None] = mapped_column(JSON)
    doc_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    doc: Mapped[KnowledgeDoc | None] = relationship(back_populates="chunks")


# --------------------------------------------------------------------------- #
# 支付与订阅
# --------------------------------------------------------------------------- #
class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    order_no: Mapped[str] = mapped_column(String(64), unique=True)
    amount: Mapped[float | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str | None] = mapped_column(String(10), default="CNY")
    # pending / paid / failed / refunded
    status: Mapped[str | None] = mapped_column(String(20), default="pending")
    # stripe / wechat / alipay
    provider: Mapped[str | None] = mapped_column(String(20))
    # [扩展] 下单的套餐 code 与回调原文
    plan_code: Mapped[str | None] = mapped_column(String(50))
    raw_callback: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    plan: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str | None] = mapped_column(String(20))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------------------- #
# 反馈与审计
# --------------------------------------------------------------------------- #
class Feedback(Base):
    __tablename__ = "feedbacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("analysis_reports.id", ondelete="CASCADE"))
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    rating: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    action: Mapped[str | None] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --------------------------------------------------------------------------- #
# [扩展] HR 批量初筛（aaa.md §3.9 功能需要，DDL 草案中未列出）
# --------------------------------------------------------------------------- #
class HrBatch(Base):
    __tablename__ = "hr_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    jd_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("job_descriptions.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    total: Mapped[int] = mapped_column(Integer, default=0)
    finished: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["HrBatchItem"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class HrBatchItem(Base):
    __tablename__ = "hr_batch_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hr_batches.id", ondelete="CASCADE"))
    resume_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("resumes.id", ondelete="CASCADE"))
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("analysis_tasks.id"))
    report_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("analysis_reports.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    overall_score: Mapped[int | None] = mapped_column(Integer)
    match_score: Mapped[int | None] = mapped_column(Integer)
    risk_level: Mapped[str | None] = mapped_column(String(10))
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    batch: Mapped[HrBatch | None] = relationship(back_populates="items")


# --------------------------------------------------------------------------- #
# [扩展] Prompt 模板版本管理（aaa.md §3.10 管理后台需要）
# --------------------------------------------------------------------------- #
class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())

# --------------------------------------------------------------------------- #
# [扩展] 账号体系：短信验证码（登录 / 注册 / 改密）
# --------------------------------------------------------------------------- #
class SmsCode(Base):
    """只存验证码的 sha256 摘要，绑定「手机号 + 用途」。

    为什么落库而不是放内存：多 worker / 重启后仍有效，且能直接查最近一条做
    60 秒重发限制与每日上限，不需要额外引入 Redis。
    """

    __tablename__ = "sms_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    # login / register / reset
    purpose: Mapped[str] = mapped_column(String(20), default="login", index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


# --------------------------------------------------------------------------- #
# [扩展] 会话黑名单：登出 / 改密后按 jti 失效单个 token
# --------------------------------------------------------------------------- #
class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
