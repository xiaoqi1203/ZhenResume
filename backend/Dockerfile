# syntax=docker/dockerfile:1
#
# ZhenResume 后端镜像（FastAPI + SQLite）
#
# 设计要点（与本仓库的既有约定保持一致）：
#   1. 容器内目录布局与本地一致：backend/ → /app，因此 config.py 用
#      `Path(__file__).resolve().parent.parent` 推导出的 DATA_DIR 就是 /app/data，
#      与进程工作目录无关（见 backend/.env.example 第 13-24 行的说明）。
#   2. 数据库与上传文件落在 /app/data，声明为 VOLUME，compose 里挂具名卷持久化。
#      不把 data/ 拷进镜像：里面是运行期用户简历原文（.gitignore 已排除）。
#   3. 以非 root 用户运行；/app/data 在镜像内就归该用户所有，具名卷首次创建时
#      会继承这层权限，所以不需要 entrypoint 里 chown 提权。
#   4. 默认不装 LibreOffice：PDF 导出在本项目中本就是可选能力，缺失时
#      pdf_enabled=False → 返回 503 PDF_UNAVAILABLE → 前端引导改用 DOCX。
#      需要容器内出 PDF 见文件末尾注释。
#
# 构建：docker build -t zhenresume-backend ./backend   （上下文 = backend/）

FROM python:3.12-slim

# 内网/镜像源场景可在构建时覆盖：--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=""
ARG PIP_TRUSTED_HOST=""
# 离线 wheel 兜底：把 `pip download` 下好的 wheel 放进构建上下文（backend/wheels/），
# 传 PIP_FIND_LINKS=wheels 即可完全不走网络 —— 国内构建最稳的一条路。
ARG PIP_FIND_LINKS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app

# ---- 依赖层（单独一层，改代码不会触发重装）----
# curl 供 HEALTHCHECK 使用：python:*-slim 基础镜像本身不带 curl。
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# wheels/ 恒存在（带 .gitkeep），里面可放 `pip download` 下好的离线包：
# 传 PIP_FIND_LINKS=wheels 就完全不走网络，国内构建最稳。
COPY wheels ./wheels/
RUN set -eux; \
    if [ -n "$PIP_FIND_LINKS" ]; then \
        pip install --no-index --find-links "$PIP_FIND_LINKS" -r requirements.txt; \
    elif [ -n "$PIP_INDEX_URL" ]; then \
        pip install --index-url "$PIP_INDEX_URL" ${PIP_TRUSTED_HOST:+--trusted-host "$PIP_TRUSTED_HOST"} -r requirements.txt; \
    else \
        pip install -r requirements.txt; \
    fi

# ---- 运行期用户与数据目录（必须在 COPY 代码之前：见文件头第 3 条）----
RUN set -eux; \
    groupadd --gid 10001 zhenresume; \
    useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin zhenresume; \
    install -d -o zhenresume -g zhenresume -m 0755 /app/data

# ---- 应用代码 ----
# 排除项由 backend/.dockerignore 控制（.venv / data / .env / __pycache__ 都不会进镜像）。
COPY --chown=zhenresume:zhenresume . .

USER zhenresume

# 声明数据卷：compose 用具名卷挂载，容器重建不丢库
VOLUME ["/app/data"]

EXPOSE 8000

# 启动钩子（app/main.py::lifespan）会自动建表 + 知识库为空则灌 16 篇种子文档，
# 所以不需要额外的 entrypoint/迁移步骤。
# 必须绑 0.0.0.0，否则只在容器内监听、宿主访问不到。
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --------------------------------------------------------------------------- #
# 可选：让容器内也能导出 PDF
#
# 需要 LibreOffice Kit（本项目 docx_to_pdf 调的是它的 CLI）。加下面两段即可，
# 代价是镜像大约多 500MB~1GB：
#
#   RUN set -eux; \
#       apt-get update; \
#       apt-get install -y --no-install-recommends libreoffice-writer fonts-noto-cjk; \
#       rm -rf /var/lib/apt/lists/*
#
#   ENV LIBREOFFICE_NODE=/usr/lib/libreoffice/program/soffice.bin \
#       LIBREOFFICE_CLI=/usr/lib/libreoffice/program/soffice
#
# 装完先验证路径真实存在（不同发行版/版本可能不同）：
#   docker run --rm zhenresume-backend ls -l /usr/lib/libreoffice/program/soffice*
#
# 注意：PDF 路线是 DOCX → LibreOffice → PDF，缺少中文字体（fonts-noto-cjk）
# 时中文会变方框，所以字体包不是可选项。
# --------------------------------------------------------------------------- #
