# syntax=docker/dockerfile:1
#
# ZhenResume 前端镜像（Next.js 14 App Router，多阶段：deps → builder → runner）
#
# ⚠️ NEXT_PUBLIC_* 是**构建期**内联的（见 frontend/lib/api.ts:53），不是运行期读取。
#    所以后端地址必须在 `docker build` 时用 --build-arg NEXT_PUBLIC_API_BASE 指定，
#    改了它必须重新 build（本项目 docs/vm-deploy.md 第 175 行已说明这条限制）。
#
# 构建：docker build -t zhenresume-frontend --build-arg NEXT_PUBLIC_API_BASE=http://localhost:8000 ./frontend
#       （上下文 = frontend/）

# --------------------------------------------------------------------------- #
# 1) 依赖层：只装依赖，改业务代码不会触发重装
# --------------------------------------------------------------------------- #
FROM node:20-alpine AS deps

# 内网/镜像源场景可在构建时覆盖：
#   --build-arg NPM_REGISTRY=https://registry.npmmirror.com
# 同时作用于 corepack 拉取 pnpm 与 pnpm 拉取依赖两步，只传一个参数就够。
ARG NPM_REGISTRY="https://registry.npmjs.org"
ENV NPM_CONFIG_REGISTRY=$NPM_REGISTRY

# Node 20 自带 corepack，可直接激活 pnpm，无需全局安装。
# pnpm-lock.yaml 是 lockfileVersion 9.0，对应 pnpm 9（本仓库按 pnpm 9 锁定）。
RUN corepack enable && corepack prepare pnpm@9 --activate

WORKDIR /app

# pnpm-lock.yaml 已在仓库中，用 --frozen-lockfile 保证镜像可复现
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir /pnpm/store

# --------------------------------------------------------------------------- #
# 2) 构建层：产出 .next/standalone 自包含产物
# --------------------------------------------------------------------------- #
FROM node:20-alpine AS builder

RUN corepack enable && corepack prepare pnpm@9 --activate

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

# 后端地址在构建期内联进产物；默认值与 lib/api.ts 的兜底保持一致
ARG NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE

COPY --from=deps /app/node_modules ./node_modules
# 本仓库目前没有 public/ 目录（图标等都走内联），但 runner 阶段的
# `COPY --from=builder /app/public` 要求它必须存在，否则构建直接失败。
# 这里先建空目录，将来加了 public/ 会被下面的 COPY . . 正常覆盖填充。
RUN mkdir -p public
COPY . .

# next.config.mjs 里 output: 'standalone' 是下面 runner 阶段的前提
RUN pnpm build

# --------------------------------------------------------------------------- #
# 3) 运行层：只带 standalone 产物的精简镜像，非 root 运行
# --------------------------------------------------------------------------- #
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 10001 nodejs \
    && adduser --system --uid 10001 --ingroup nodejs nextjs

# standalone 产物自带最小化的 node_modules 与 server.js
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
# 静态资源不在 standalone 里，必须单独拷（否则页面无 CSS/JS）
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
# public/ 同样需要单独拷；builder 阶段已保证它存在
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

# Next 根路由返回 200 即视为就绪
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
