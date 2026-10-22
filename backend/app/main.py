"""FastAPI 应用入口。

启动时做两件事：建表 + 知识库为空则灌种子数据（保证 RAG 一上来就有依据可用）。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import api_router
from .config import settings
from .db import init_db, session_scope
from .errors import AppError
from .services.seed import seed_if_empty

logger = logging.getLogger("zhenresume")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with session_scope() as db:
        created = seed_if_empty(db)
    if created:
        logger.info("知识库已灌入 %s 篇种子文档", created)

    if settings.llm_mode == "mock":
        logger.warning(
            "未配置任何 LLM Key（ALIYUN_API_KEY / DEEPSEEK_API_KEY），当前为 mock 模式："
            "报告由规则引擎生成。配置 Key 后重启即可切换到真实模型。"
        )
    else:
        logger.info(
            "LLM 降级链（按序尝试）：%s", " → ".join(settings.llm_chain_summary)
        )
    if not (settings.auth_secret or "").strip():
        logger.warning("AUTH_SECRET 未配置：账号 JWT 使用开发默认密钥，禁止用于生产。")
    logger.info(
        "鉴权：%s（短信开发模式=%s）",
        "必须登录" if settings.auth_required else "Bearer 优先，未登录回落 X-Device-Id",
        settings.sms_dev_mode,
    )
    logger.info(
        "工作流引擎：%s（并行=%s，checkpoint=%s）",
        settings.workflow_engine,
        settings.workflow_parallel,
        settings.langgraph_checkpoint,
    )
    if settings.workflow_engine == "langgraph" and settings.langgraph_checkpoint != "off":
        # 服务重启后把卡在 running 的任务从断点续跑（同一 thread_id，已完成节点不重跑）
        asyncio.create_task(_resume_zombies_safe())
    yield


async def _resume_zombies_safe() -> None:
    try:
        from .workflow.graph import resume_zombies

        recovered = await resume_zombies()
        if recovered:
            logger.warning("已回收 %s 个僵尸任务", recovered)
    except Exception:  # noqa: BLE001
        logger.exception("僵尸任务回收失败（不影响服务启动）")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="AI 简历诊断与优化工作流 —— RAG 知识库 + 多 Agent 工作流 + 可追溯证据链",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 前端导出 PDF 需要用 fetch 拿 blob 自行触发下载，此时 JS 默认**读不到**
    # Content-Disposition（非简单响应头），不暴露就取不到文件名；
    # X-Page-Count / X-Fit-Scale 是导出接口的附加信息（实际页数、fit=one 的缩放比例）。
    expose_headers=["Content-Disposition", "X-Page-Count", "X-Fit-Scale"],
)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    """业务异常 → 契约 §8 的统一错误结构。

    401 额外带 `WWW-Authenticate: Bearer`：前端据此区分「没登录」与其它业务错误，
    并清理本地登录态、跳回 /login。
    """
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload(), headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """入参校验失败也走统一结构，避免前端要处理两种错误形状。"""
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    message = first.get("msg", "入参不合法")
    return JSONResponse(
        status_code=400,
        content={"detail": {"code": "VALIDATION_ERROR", "message": f"{location}: {message}" if location else message}},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": {"code": "INTERNAL_ERROR", "message": "服务内部错误，请稍后重试"}},
    )


app.include_router(api_router)

# 简历原文件静态访问（生产环境应换成 S3 预签名 URL）
app.mount("/static", StaticFiles(directory=str(settings.upload_dir)), name="static")


@app.get("/", tags=["health"])
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": settings.version,
        "docs": "/docs",
        "api_base": "/api/v1",
        "llm_mode": settings.llm_mode,
    }
