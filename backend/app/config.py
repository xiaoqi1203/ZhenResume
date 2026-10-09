"""全局配置。

设计原则（MVP-lite）：所有外部依赖都通过环境变量暴露，升级到云上只改 .env，
不改代码。未配置 DEEPSEEK_API_KEY 时自动进入 mock 模式，保证零 Key 也能跑通全链路。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"


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

    # ---- LLM ----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2
    llm_price_input_per_m: float = 2.0
    llm_price_output_per_m: float = 8.0

    # ---- RAG ----
    embedding_dim: int = 1536
    rag_top_k: int = 5

    # ---- PDF 导出 ----
    # 刻意不硬编码任何 LibreOffice 路径：产品不应该依赖运行环境的具体安装位置。
    # 留空时 `pdf_enabled` 为 False，导出 PDF 会返回 503 PDF_UNAVAILABLE，
    # 前端引导用户改用 DOCX 或浏览器打印。见 .env.example。
    libreoffice_node: str = ""
    libreoffice_cli: str = ""
    pdf_timeout_s: float = 120.0

    # ---- 业务 ----
    free_quota: int = 3
    frontend_base_url: str = "http://127.0.0.1:3000"
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"

    @property
    def llm_mode(self) -> str:
        """live = 真实调用 DeepSeek；mock = 规则引擎离线生成。"""
        return "live" if self.deepseek_api_key.strip() else "mock"

    @property
    def llm_provider(self) -> str:
        return "deepseek" if self.llm_mode == "live" else "mock"

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
