"""全局配置。

设计原则（MVP-lite）：所有外部依赖都通过环境变量暴露，升级到云上只改 .env，
不改代码。未配置 DEEPSEEK_API_KEY 时自动进入 mock 模式，保证零 Key 也能跑通全链路。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"


@dataclass(frozen=True)
class EmbeddingSpec:
    """一个 embedding 供应商（OpenAI 兼容 /embeddings）。"""

    name: str
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class LlmProviderSpec:
    """一个 LLM 供应商（OpenAI 兼容协议）。models 是**同层模型阶梯**，按序尝试。"""

    name: str
    base_url: str
    api_key: str
    models: tuple[str, ...]
    price_in: float
    price_out: float
    # 额外请求体字段（原样并入 payload）。典型用途：阿里云 qwen3 系列用
    # {"enable_thinking": false} 关掉思考，否则部分型号会在简单结构化任务上也耗时 60s+。
    extra_body: dict = field(default_factory=dict)



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ZhenResume 简甄"
    version: str = "1.0.0"

    # ---- 存储 ----
    # SQLite 起步；换成 postgresql+psycopg://... 即可迁移到 PostgreSQL + pgvector
    database_url: str = f"sqlite+pysqlite:///{(DATA_DIR / 'zhenresume.db').as_posix()}"
    upload_dir: Path = DATA_DIR / "uploads"
    max_upload_mb: int = 10

    # ---- 文件抽取与本地 OCR ----
    # 图片简历（PNG/JPG）与扫描版 PDF 走**本机** OCR 引擎，不需要任何外部 API/Key，
    # 也没有任何 Key/base_url 配置项 —— 装了什么引擎就用什么（见 services/ocr.py）。
    #   auto      = 按 rapidocr → tesseract 顺序取第一个可用的（默认）
    #   rapidocr  = 只用 rapidocr（pip install rapidocr-onnxruntime，纯 pip、推荐）
    #   tesseract = 只用 tesseract（系统二进制 + pip install pytesseract）
    #   off       = 关闭 OCR；图片/扫描件一律明确降级（空文本 + note，不静默）
    ocr_enabled: bool = True
    ocr_backend: str = "auto"
    ocr_lang: str = "chi_sim+eng"        # tesseract 语言；缺哪个自动降级到已安装的子集
    ocr_min_score: float = 0.5           # rapidocr 置信度阈值，低于它的文本块丢弃
    ocr_max_side: int = 2200             # 识别前等比缩放，最长边不超过该值（0 = 不缩放）
    ocr_max_pages: int = 10              # 扫描版 PDF 最多 OCR 页数（挡住 200 页扫描件）
    ocr_render_dpi: int = 200            # 无内嵌位图时整页渲染的 DPI（需 pypdfium2）
    ocr_text_layer_min_chars: int = 24   # PDF 文本层短于此值即视为扫描件，触发 OCR 回退
    tesseract_cmd: str = ""              # tesseract 可执行文件路径；留空则从 PATH 找

    # ---- LLM（多供应商链）----
    # 按 `llm_providers` 的顺序逐层尝试；同一层内再按「模型阶梯」降级。
    # 全部失败才回落到该 Agent 的规则引擎实现（步骤标记 degraded，原因写进 notes，不静默）。
    # 所有供应商都走 OpenAI 兼容协议：POST {base_url}/chat/completions + response_format=json_object。
    # 没有任何 Key 时自动进入 mock 模式，全链路仍可跑通。
    llm_providers: str = "aliyun,deepseek"
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2
    # 单次生成上限。8 个 Agent 的输出都是中小 JSON（几百 token 量级），
    # 给太大只会在截断/慢生成时浪费钱；可调。
    llm_max_tokens: int = 4096
    llm_max_retries: int = 2
    llm_timeout_s: float = 60.0
    # 节点级模型路由：把这些**结构化输出**节点提出到更快的模型上跑。
    # 实测 deepseek-v4-pro-0813 比 qwen3.8-max 快约 1.8×（parse 5.3s vs 9.5s）。
    # 为什么只路由这几个：ats/impact/risk/qc 字段固定、推理浅，差异只在措辞；
    # parse/jd_match/suggestion 需要更强的理解与判断，保留主模型。
    # 留空 = 不路由（当前行为不变）。
    llm_fast_model: str = ""
    llm_fast_nodes: str = "ats,impact,risk,qc"

    # 第一层：阿里云百炼（DashScope）OpenAI 兼容端点
    aliyun_api_key: str = ""
    aliyun_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    aliyun_model: str = "qwen3.8-max"
    aliyun_model_fallbacks: str = "deepseek-v4-pro-0813,qwen3.7-flash,qwen3.7-flash-2026-07-15"
    aliyun_price_input_per_m: float = 0.0      # 0 = 用下面的通用单价
    aliyun_price_output_per_m: float = 0.0
    # 额外请求体（JSON 对象字符串）。
    # ⚠️ 默认 `{"enable_thinking": false}` 是**实测结论**，不是随手写的：
    #    真实 parse 节点上，qwen3.8-max 开思考时 60.1s / 2483 输出 token，
    #    关思考后 9.5s / 404 输出 token —— 快 6.3×、成本降 77%，而解析结果完全一致
    #    （工作2/项目1/技能6）。8 个节点串行时这就是"8 分钟一份"和"1 分钟一份"的差别。
    aliyun_extra_body: str = '{"enable_thinking": false}'

    # 第二层：DeepSeek（兜底）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    deepseek_model_fallbacks: str = ""
    deepseek_price_input_per_m: float = 0.0
    deepseek_price_output_per_m: float = 0.0
    deepseek_extra_body: str = ""

    # 计价（元 / 百万 token）——未单独配置单价的供应商用这两个值。
    # ⚠️ 成本看板与「单份 ≤ 0.5 元」验收都依赖它，换模型后必须同步改。
    llm_price_input_per_m: float = 2.0
    llm_price_output_per_m: float = 8.0

    # ---- RAG ----
    # embedding 提供方：
    #   hash = 本地确定性哈希向量（默认）。零依赖、可离线、结果可复现，但不是语义向量。
    #   http = 调用 OpenAI 兼容的 /embeddings（如阿里云百炼 text-embedding-v4），是真实语义向量。
    # ⚠️ 切到 http 后必须：① 把 EMBEDDING_DIM 改成模型实际维度；② 重建索引
    #    （python -m scripts.seed_knowledge --force），否则新旧向量维度不一致 → 检索恒为 0 分。
    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-v4"
    embedding_base_url: str = ""      # 留空则复用 aliyun 层的 base_url
    embedding_api_key: str = ""       # 留空则复用 aliyun 层的 api_key
    # 回退层（例：阿里云不可用时走本地 Ollama）。
    # ⚠️ 必须与主层**输出同一维度**，否则维度校验会直接报错（不会静默混库）。
    embedding_fallback_base_url: str = ""
    embedding_fallback_api_key: str = ""
    embedding_fallback_model: str = ""
    # 建索引是"一批一次请求"，冷启动或大 batch 会明显更慢；20s 实测不够（会 ReadTimeout）
    embedding_timeout_s: float = 60.0
    embedding_batch_size: int = 10
    embedding_dim: int = 1536         # 主层与回退层必须一致；qwen3.7-text-embedding-flash/v4/mxbai 都是 1024
    rag_top_k: int = 5

    # ---- PDF 导出 ----
    # 刻意不硬编码任何 LibreOffice 路径：产品不应该依赖运行环境的具体安装位置。
    # 留空时 `pdf_enabled` 为 False，导出 PDF 会返回 503 PDF_UNAVAILABLE，
    # 前端引导用户改用 DOCX 或浏览器打印。见 .env.example。
    libreoffice_node: str = ""
    libreoffice_cli: str = ""
    pdf_timeout_s: float = 120.0

    # ---- 工作流编排 ----
    # legacy    = 手写状态机（workflow/engine.py，行为基线）
    # langgraph = LangGraph 1.2 图（workflow/graph.py）；两者共用同一批 services/* 与 agents/*
    workflow_engine: str = "legacy"
    workflow_parallel: bool = False        # ats / impact / risk 三路并行
    workflow_rework_max: int = 0           # 质检回环次数，0 = 关闭（保持现状语义）
    langgraph_checkpoint: str = "off"      # off | sqlite | postgres
    langgraph_checkpoint_url: str = ""     # 留空则按 database_url / data 目录推导
    langgraph_durability: str = "async"
    # checkpoint 保留天数：超过这个天数的任务其 checkpoint 会被 scripts/prune_checkpoints.py 清掉
    # （checkpoint 只服务于崩溃续跑，任务归档后留着只会拖慢表）
    langgraph_checkpoint_ttl_days: int = 7    # exit | async | sync（空字符串 = 不传该参数）
    zombie_task_timeout_s: int = 300       # 僵尸任务回收阈值（心跳超时）
    llm_max_concurrency: int = 4           # 同时在飞的 LLM 调用数（并行/批量时生效）
    # HR 批量初筛走**轻量排序表图**（parse + jd_match + risk，砍掉 impact/suggestion/report）。
    # 实测每份省约 2/3 时间与成本；详细分析那条完整流水线仍然保留给求职者单份诊断。
    hr_screening: bool = True
    # HR 批量同时在跑的份数。每份是网络等待型任务，并发才不浪费；
    # 在飞的 LLM 请求数 ≈ 本值 × 每份并行度（排序表图每份最多 2 个），别超过供应商配额。
    hr_batch_concurrency: int = 50

    # ---- 语义判断与评分档位（V2.0）----
    # scoring_version：v1 = 等权子串覆盖率（行为基线，永不改动）；v2 = 分层加权 + 语义校验。
    # 两档并存，保证历史报告可比、可回滚。
    scoring_version: str = "v2"
    # JD 要求的分层权重（§10.3）：核心项 / 加分项 / 概念词。
    # 只作用于 JD 要求侧，绝不用于简历侧内容。1.0/0.6/0.3 表达"重要性差 3 倍"，
    # 比 0.6/0.4 更有效——两个权重越接近，分层越不起作用。
    jd_tier_weight_core: float = 1.0
    jd_tier_weight_bonus: float = 0.6
    jd_tier_weight_keyword: float = 0.3
    # 语义判定 Agent 的预算（详见改动说明书 §5.3）
    # ⚠️ 实测（P2-c，LangChain Agent，deepseek-v4-pro-0813）：一次判定自然收敛的轨迹是
    #   第1轮 4 个工具、第2轮 18 个（批量核实关键词）、第3轮 4 个 kb_search、
    #   第4轮 4 个 locate_quote（自检）、第5轮 finish → **5 次模型调用、31 次工具调用**。
    #   所以工具上限必须 > 31，否则中间件会在 finish 之前直接把循环截断
    #   （实测 run_limit=16 时被截断成 'Tool call limit reached'，判定全丢）。
    #   注意：这里限的是**模型往返次数**（真正的成本驱动）与**工具调用总数**，
    #   不是"允许的工具种类"——18 个并发 resume_search 是本地读，几乎零成本。
    semantic_judge_enabled: bool = True
    semantic_judge_cache: bool = True
    semantic_judge_max_steps: int = 16       # 模型往返上限（实测自然收敛 5–6 次；给足余量避免强制收尾）
    semantic_judge_max_tool_calls: int = 60  # 工具调用总数上限（自然收敛约 31 次）

    # ---- 执行模型（谁把工作流跑起来）----
    # inline = FastAPI BackgroundTasks（现状；`scripts/verify.py` 依赖"请求返回前跑完"）
    # celery = 投递到 Redis 队列，由独立 worker 执行（生产；web 进程不再被长任务占住）
    workflow_executor: str = "inline"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"
    # 超时上限：单份报告按 8 节点 live 实测约 130s，给足余量；HR 排序表更快
    celery_task_time_limit_s: int = 900
    celery_task_soft_time_limit_s: int = 840

    # ---- 业务 ----
    free_quota: int = 3
    frontend_base_url: str = "http://127.0.0.1:3000"
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"

    @staticmethod
    def _split_models(raw: str) -> list[str]:
        return [m.strip() for m in (raw or "").split(",") if m.strip()]

    @staticmethod
    def _parse_extra_body(raw: str) -> dict:
        """解析额外请求体；写错就当空（启动时不该因为一个笔误崩掉）。"""
        raw = (raw or "").strip()
        if not raw:
            return {}
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return obj if isinstance(obj, dict) else {}

    @property
    def llm_fast_node_set(self) -> set[str]:
        return {n.strip() for n in (self.llm_fast_nodes or "").split(",") if n.strip()}

    @property
    def embedding_chain(self) -> list[EmbeddingSpec]:
        """embedding 供应商链：主层 → 回退层。没有 Key 的层直接跳过。"""
        specs: list[EmbeddingSpec] = []
        base = self.embedding_base_url.strip()
        key = self.embedding_api_key.strip()
        if not base or not key:
            aliyun = next((s for s in self.llm_chain if s.name == "aliyun"), None)
            if aliyun is not None:
                base = base or aliyun.base_url
                key = key or aliyun.api_key
        if base and key:
            specs.append(EmbeddingSpec(name="primary", base_url=base, api_key=key, model=self.embedding_model))
        fb_base = self.embedding_fallback_base_url.strip()
        if fb_base:
            # 本地 Ollama 不校验 Key，但字段留空会让 httpx 少一个头，给个占位值更稳
            specs.append(
                EmbeddingSpec(
                    name="fallback",
                    base_url=fb_base,
                    api_key=self.embedding_fallback_api_key.strip() or "ollama",
                    model=self.embedding_fallback_model or self.embedding_model,
                )
            )
        return specs

    @property
    def embedding_chain_summary(self) -> list[str]:
        return [f"{s.name}:{s.model}" for s in self.embedding_chain]

    @property
    def llm_chain(self) -> list[LlmProviderSpec]:
        """按配置顺序返回**可用**的供应商（没有 Key 的直接跳过）。"""
        specs: list[LlmProviderSpec] = []
        for name in self._split_models(self.llm_providers):
            if name == "aliyun" and self.aliyun_api_key.strip():
                specs.append(
                    LlmProviderSpec(
                        name="aliyun",
                        base_url=self.aliyun_base_url,
                        api_key=self.aliyun_api_key.strip(),
                        models=tuple(
                            [self.aliyun_model, *self._split_models(self.aliyun_model_fallbacks)]
                        ),
                        price_in=self.aliyun_price_input_per_m or self.llm_price_input_per_m,
                        price_out=self.aliyun_price_output_per_m or self.llm_price_output_per_m,
                        extra_body=self._parse_extra_body(self.aliyun_extra_body),
                    )
                )
            elif name == "deepseek" and self.deepseek_api_key.strip():
                specs.append(
                    LlmProviderSpec(
                        name="deepseek",
                        base_url=self.deepseek_base_url,
                        api_key=self.deepseek_api_key.strip(),
                        models=tuple([self.llm_model, *self._split_models(self.deepseek_model_fallbacks)]),
                        price_in=self.deepseek_price_input_per_m or self.llm_price_input_per_m,
                        price_out=self.deepseek_price_output_per_m or self.llm_price_output_per_m,
                        extra_body=self._parse_extra_body(self.deepseek_extra_body),
                    )
                )
        return specs

    @property
    def llm_mode(self) -> str:
        """live = 至少有一层真实模型可用；mock = 全部无 Key，用规则引擎离线生成。"""
        return "live" if self.llm_chain else "mock"

    @property
    def llm_provider(self) -> str:
        """当前生效的第一层（health 契约 §1 的字段）。"""
        chain = self.llm_chain
        return chain[0].name if chain else "mock"

    @property
    def active_model(self) -> str:
        """当前生效的第一层首个模型。"""
        chain = self.llm_chain
        return chain[0].models[0] if chain else "mock-rule-engine"

    @property
    def llm_chain_summary(self) -> list[str]:
        """形如 ['aliyun:qwen3.8-max', 'aliyun:qwen3.7-flash', 'deepseek:deepseek-flash']。"""
        return [f"{spec.name}:{model}" for spec in self.llm_chain for model in spec.models]

    @property
    def pdf_enabled(self) -> bool:
        """PDF 转换是否可用（依赖外部 LibreOffice Kit）。"""
        if not self.libreoffice_node or not self.libreoffice_cli:
            return False
        return Path(self.libreoffice_node).exists() and Path(self.libreoffice_cli).exists()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()
