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
#   5. OCR 引擎由 build arg `INSTALL_OCR` 控制（rapidocr / tesseract / none）：
#      图片简历与扫描版 PDF 是可选能力，none 时 extract_method=ocr-unavailable +
#      明确 note，前端会拦下来提示换格式。默认 none，compose 里默认 rapidocr。
#
# 构建：docker build -t zhenresume-backend ./backend   （上下文 = backend/）

FROM python:3.12-slim

# 内网/镜像源场景可在构建时覆盖：--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=""
ARG PIP_TRUSTED_HOST=""
# 离线 wheel 兜底：把 `pip download` 下好的 wheel 放进构建上下文（backend/wheels/），
# 传 PIP_FIND_LINKS=wheels 即可完全不走网络 —— 国内构建最稳的一条路。
ARG PIP_FIND_LINKS=""
# 国内构建时可传 APT_MIRROR=https://mirrors.aliyun.com，把 deb.debian.org 换掉。
# 默认空 = 保持基础镜像官方源（不改本机/海外部署的行为）。
ARG APT_MIRROR=""

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
    if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
        [ -f "$f" ] && sed -i "s|https\?://deb.debian.org|$APT_MIRROR|g" "$f"; \
      done; \
    fi; \
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

# ---- 本地 OCR 引擎（图片简历 / 扫描版 PDF；**不需要任何外部 API/Key**）----
# 由 compose 用 build args 传（见 deploy/infra/docker-compose.yml）：
#   rapidocr  = 默认。纯 pip，PP-OCR 中英文模型随包自带，中文效果好。
#               代价：pip 约 70MB + onnxruntime/opencv 在 slim 上必需的三个系统库。
#   tesseract = 更轻（apt 约 15MB + pip 薄封装），中文精度略逊。
#   none      = 不装；图片/扫描件返回 ocr-unavailable + 明确 note（前端会拦下并提示换格式）。
# 放在依赖层之后：改业务代码不会连带重装这 70MB。
# ⚠️ 装完务必在镜像里自检：docker run --rm <镜像> python -m scripts.check_ocr
ARG INSTALL_OCR=none
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
        [ -f "$f" ] && sed -i "s|https\?://deb.debian.org|$APT_MIRROR|g" "$f"; \
      done; \
    fi; \
    case "$INSTALL_OCR" in \
      rapidocr) \
        apt-get update; \
        apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0; \
        rm -rf /var/lib/apt/lists/*; \
        pip install --no-cache-dir rapidocr-onnxruntime==1.4.4; \
        ;; \
      tesseract) \
        apt-get update; \
        apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim; \
        rm -rf /var/lib/apt/lists/*; \
        pip install --no-cache-dir pytesseract==0.3.13; \
        ;; \
      none|"") \
        echo "跳过 OCR 引擎：图片简历与扫描件会明确降级（见 .env.example 的 OCR_* 段）"; \
        ;; \
      *) \
        echo "未知 INSTALL_OCR=$INSTALL_OCR（可选 rapidocr / tesseract / none）" >&2; \
        exit 1; \
        ;; \
    esac

# ---- PDF 导出所需的 LibreOffice（可选能力，但本项目已默认启用）----
# 路线：DOCX → LibreOffice CLI → PDF，能最大程度保留 Word 版式。
# 中文字体不是可选项：缺字体时导出的 PDF 中文会变成方框。
# 用 fonts-wqy-zenhei 而不是 fonts-noto-cjk：前者约 15MB、后者约 57MB，
# 在部分网络环境下 57MB 的单个 deb 会长时间卡住；文泉驿对中文简历足够。
# poppler-utils 仅用于运维自检（pdftotext 验证中文没丢），不参与业务。
ARG INSTALL_PDF=1
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
        [ -f "$f" ] && sed -i "s|https\?://deb.debian.org|$APT_MIRROR|g" "$f"; \
      done; \
    fi; \
    if [ "$INSTALL_PDF" = "1" ]; then \
      apt-get update; \
      apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=3 install -y --no-install-recommends libreoffice-writer fonts-wqy-zenhei poppler-utils; \
      rm -rf /var/lib/apt/lists/*; \
      ls -l /usr/lib/libreoffice/program/soffice /usr/lib/libreoffice/program/soffice.bin; \
    else \
      echo "跳过 LibreOffice：PDF 导出会返回 503 PDF_UNAVAILABLE"; \
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

# PDF 导出：config.pdf_enabled 要求两个路径都存在（安装层已校验过）
ENV LIBREOFFICE_NODE=/usr/lib/libreoffice/program/soffice.bin \
    LIBREOFFICE_CLI=/usr/lib/libreoffice/program/soffice

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

# --------------------------------------------------------------------------- #
# 可选：让容器内也能识别图片简历 / 扫描版 PDF（**不需要任何外部 API/Key**）
#
# 已经在上面用 `ARG INSTALL_OCR` 实现好了，这里只留"怎么传参 + 怎么验证"：
#
#   docker build --build-arg INSTALL_OCR=rapidocr -t zhenresume-backend ./backend
#   docker run --rm zhenresume-backend python -m scripts.check_ocr
#
# 走 compose 时不用手敲 build args（deploy/infra/docker-compose.yml 已把
# INSTALL_OCR 默认设成 rapidocr）：
#   docker compose -f deploy/infra/docker-compose.yml --profile app up -d --build
#   INSTALL_OCR=tesseract docker compose ... --profile app up -d --build   # 换轻量方案
#   INSTALL_OCR=none       docker compose ... --profile app up -d --build   # 关掉 OCR
#
# 注意：镜像内没有中文字体时，check_ocr 会跳过「中文识别正确性」断言 ——
# 那只是合成样本画不出中文，真实图片的识别不受影响。
# --------------------------------------------------------------------------- #
