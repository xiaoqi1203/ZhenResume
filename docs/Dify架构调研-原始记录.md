# Dify 现状调研（用于「FastAPI + 8-Agent → Dify + LangGraph 混合架构」技术依据）

> 调研方法：全部通过 `web_fetch` 抓取**官方文档**（`docs.dify.ai`，Mintlify 托管，可在任意页面 URL 后加 `.md` 拿到纯 Markdown，无导航噪音）、**官方仓库源码**（`raw.githubusercontent.com/langgenius/dify/main/...`）、**GitHub REST API**（releases / issues / commits / contents）。所有结论标注来源 URL。
>
> 标注约定：
> - **【官方确认】** = 官方文档原文或官方仓库源码直接支持
> - **【源码确认】** = 官方仓库源码/提交直接支持（比文档更硬）
> - **【推断】** = 我基于确认事实的合理推论，官方未明说
> - **【未查证】** = 我没有找到依据，不要当作事实使用
>
> ⚠️ 时点提醒：抓取 `releases` API 时最新稳定版为 **v1.17.1（published 2026-09-10）**。但抓到的 issue 元数据最晚到 **2026-10-14**（#44059），说明调研时点之后很可能已有更新的 tag。**落地前请重新确认一次最新版本号。**

---

## 1. 版本号、发布节奏、License 与商用/多租户限制

### 结论

1. **当前稳定版：v1.17.1**（`published_at: 2026-09-10T10:04:06Z`），非 prerelease。【官方确认】
2. **License = 修改版 Apache-2.0 + 附加条款**，不是纯 Apache-2.0。核心两条附加限制：**(a) 未经书面授权不得用 Dify 源码运营多租户环境**（Dify 语境下 1 tenant = 1 workspace）；**(b) 使用 Dify 前端时不得移除/修改 LOGO 与版权信息**。【官方确认】
3. **作为"被调用的后端能力层"商用是明确允许的**：许可证原文允许 "as a backend service for other applications"。【官方确认】——这对本方案是好消息。
4. **多租户 SaaS 是硬红线**：如果你们打算把 Dify 包装成"一个实例服务多个客户/多 workspace"，必须取得 Dify 书面商业授权。【官方确认】
5. LOGO 条款**仅在前端（`web/` 目录或 `web` 镜像）场景下生效**；纯 API 调用、不涉及 Dify 前端的用法不受该条款约束。【官方确认】
6. 许可证允许生产方（LangGenius）**单方面调整开源协议（可更严也可更宽）**，贡献者代码可被用于商业目的。【官方确认】

### 关键细节与限制

**1.1 版本与发布节奏**

- 最新 tag `1.17.1`，release 名称 "v1.17.1 - Bug Fixes and Improvements"；发布产物包含 `difyctl` 二进制（darwin/linux/windows），说明 CLI 已作为一等发布物。
  来源：<https://api.github.com/repos/langgenius/dify/releases?per_page=6>
- `docker/docker-compose.yaml`（main 分支）中所有核心镜像硬编码为 `1.17.1`：`langgenius/dify-api:1.17.1`、`langgenius/dify-web:1.17.1`、`langgenius/dify-agent-backend:1.17.1`、`langgenius/dify-agent-local-sandbox:1.17.1`。
  来源：<https://raw.githubusercontent.com/langgenius/dify/main/docker/docker-compose.yaml>
- **发布节奏：快。** 从 release body / issue 标签可见大量 minor 连续迭代：0.15.x → 1.2.0 → 1.10.0 → 1.11.1 → 1.13.3 → 1.14.0/1.14.1 → 1.15 → 1.16.0/1.16.1 → 1.17.0/1.17.1，且 1.17.0 与 1.17.1 之间存在需要**人工介入的破坏性升级步骤**（见 §2）。具体"每版间隔多少天"我未逐版核对 → **【未查证】**。
- **上线前必须做的一步**：官方 upgrade 文档要求升级后逐个比对 `docker/envs/*.env.example` 与本地 `*.env`，因为新增/变更的环境变量不会自动迁移。【官方确认】

**1.2 License 原文（`LICENSE`，main 分支）逐条**

```
Dify is licensed under a modified version of the Apache License 2.0, with the following additional conditions:

1. Dify may be utilized commercially, including as a backend service for other applications
   or as an application development platform for enterprises. Should the conditions below be
   met, a commercial license must be obtained from the producer:

a. Multi-tenant service: Unless explicitly authorized by Dify in writing, you may not use the
   Dify source code to operate a multi-tenant environment.
   - Tenant Definition: Within the context of Dify, one tenant corresponds to one workspace.

b. LOGO and copyright information: In the process of using Dify's frontend, you may not remove
   or modify the LOGO or copyright information in the Dify console or applications. This
   restriction is inapplicable to uses of Dify that do not involve its frontend.
   - Frontend Definition: the "frontend" of Dify includes all components located in the `web/`
     directory when running Dify from the raw source code, or the "web" image when running Dify
     with Docker.

2. As a contributor, you should agree that:
   a. The producer can adjust the open-source agreement to be more strict or relaxed as deemed necessary.
   b. Your contributed code may be used for commercial purposes, including but not limited to its
      cloud business operations.

Apart from the specific conditions mentioned above, all other rights and restrictions follow the
Apache License 2.0. ... The interactive design of this product is protected by appearance patent.
© 2025 LangGenius, Inc.
```

来源：<https://raw.githubusercontent.com/langgenius/dify/main/LICENSE>

**1.3 对方案设计的直接约束（重要）**

- 如果 8-Agent 简历诊断服务是**单租户**（一个部署服务你们自己的业务/单个客户），把 Dify 当内部后端能力层使用 → **合规**。
- 如果是**多租户 SaaS**（每个客户一个 Dify workspace，同一套实例对外售卖）→ **需要商业授权**。规避路径：**一个客户一套独立 Dify 部署**（物理/实例级隔离，而非 workspace 级隔离）——这属于【推断】，落地前建议法务确认。
- 若你们不使用 Dify 的 web 控制台对外呈现（只用 API），可自建前端并去掉 Dify LOGO；若使用 `web` 镜像/`web/` 源码，则必须保留 LOGO。

**1.4 版本相关的其他事实**

- `DEPLOYMENT_EDITION`（`COMMUNITY` / `ENTERPRISE` / `CLOUD`，默认 `COMMUNITY`）**在 1.17.0 起取代**了 `EDITION`、`ENTERPRISE_ENABLED`、`BILLING_ENABLED`。【官方确认】
  来源：<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>
- 社区版默认 `RBAC_ENABLED=false`，但仓库里有 RBAC/ACL 相关开发（`feat(rbac): agent RBAC/ACL scenes`、`feat: add agent resource permissions`），说明企业特性正在下沉。【源码确认，来自 release body 的 PR 列表】
- 存在 "Dify Premium on AWS" 官方平台指南，即官方也有商业分发形态。【官方确认，见文档导航】

参考链接：
- <https://github.com/langgenius/dify/releases>
- <https://api.github.com/repos/langgenius/dify/releases?per_page=6>
- <https://github.com/langgenius/dify/blob/main/LICENSE>
- <https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>

---

## 2. 自托管部署：组件构成、最低资源、外部化依赖

### 结论

1. **最低资源：CPU ≥ 2 核，RAM ≥ 4 GiB**（官方文档）。macOS 用 Docker Desktop 时官方建议给 VM 至少 2 vCPU + 8 GiB。【官方确认】
2. **组件远比"api/worker/web/postgres/redis/vector/sandbox/plugin_daemon/nginx"多**。1.17.x 的 compose 新增了 **`api_websocket`（协作专用 WebSocket 服务）、`worker_beat`、`init_permissions`、`agent_backend`、`local_sandbox`、`agent_ssrf_proxy`** 六个服务。【源码确认】
3. **外部化 PostgreSQL / Redis / 向量库 / 对象存储全部可行且是官方支持路径**，只需改 `DB_HOST` / `REDIS_HOST` / `VECTOR_STORE` + 对应 provider 变量 / `STORAGE_TYPE`。【官方确认】
4. **有一个极易踩的坑：SSRF 代理默认拒绝私网 IP**。Dify 的所有出站 HTTP（HTTP Request 节点、工具、插件）都强制走 squid 代理，默认 deny-by-default 私网/回环/链路本地地址。**要调用你们内部的 FastAPI / LangGraph 服务，必须显式配置 `SSRF_PROXY_ALLOW_PRIVATE_IPS`**。【官方确认】
5. **1.17.1 有一个必须人工分阶段执行的升级步骤**：内置 Weaviate 从 `1.27.0` 跳到 `1.39.2`（跨 12 个 minor），跳过 minor **不受支持**，"拉取镜像并重启可能静默且永久地破坏向量检索"。**用外部 Weaviate 或其他向量库不受影响。**【官方确认】
6. `docker/.env` 的优先级**高于** `docker/envs/*.env`；且 compose 会覆盖 `RESPECT_XFORWARD_HEADERS_ENABLED`。【官方确认 + 源码注释】

### 2.1 组件清单（`docker/docker-compose.yaml`，main 分支，镜像 tag = 1.17.1）

| 服务 | 镜像 | profile | 作用 |
|---|---|---|---|
| `init_permissions` | `busybox:latest` | 默认 | 修正 `./volumes/app/storage` 权限（chown 1001:1001），一次性 |
| `api` | `langgenius/dify-api:1.17.1` | 默认 | `MODE=api`，主 API（Gunicorn + gevent） |
| `api_websocket` | 同上 | `collaboration` | 工作流画布实时协作专用 WebSocket 服务（GeventWebSocketWorker） |
| `worker` | 同上 | 默认 | `MODE=worker`，Celery 消费者（dataset / workflow / mail 等全部队列） |
| `worker_beat` | 同上 | 默认 | `MODE=beat`，Celery 定时任务调度器 |
| `web` | `langgenius/dify-web:1.17.1` | 默认 | Next.js 前端 |
| `db_postgres` | `postgres:15-alpine` | `postgresql` | 主库 |
| `db_mysql` | `mysql:8.0` | `mysql` | 备选主库 |
| `oceanbase` / `seekdb` | `oceanbase/oceanbase-ce:4.3.5-lts` / `oceanbase/seekdb` | 同名 | 备选主库（国产化） |
| `redis` | `redis:6-alpine` | 默认 | 缓存 + Celery broker + 事件总线 |
| `sandbox` | `langgenius/dify-sandbox:0.2.15` | 默认 | **Code 节点 / Template Transform 的代码执行沙箱** |
| `local_sandbox` | `langgenius/dify-agent-local-sandbox:1.17.1` | 默认 | 新 Agent 的 shell workspace 沙箱（独立网络隔离） |
| `plugin_daemon` | `langgenius/dify-plugin-daemon:0.6.10-local` | 默认 | 插件运行时（有自己的库 `dify_plugin`） |
| `agent_backend` | `langgenius/dify-agent-backend:1.17.1` | 默认 | 新 Agent 后端（run 状态存 Redis db 2） |
| `ssrf_proxy` | `ubuntu/squid:latest` | 默认 | 全局出站 SSRF 代理 |
| `agent_ssrf_proxy` | `ubuntu/squid:latest` | 默认 | agent 沙箱专用出口代理（更严的白名单） |
| `nginx` | `nginx:latest` | 默认 | 反向代理 |
| `weaviate` | `cr.weaviate.io/semitechnologies/weaviate:1.39.2` | `weaviate` | **默认向量库** |
| `qdrant` | `langgenius/qdrant:v1.8.3` | `qdrant` | 备选 |
| `pgvector` | `pgvector/pgvector:pg16` | `pgvector` | 备选 |
| `chroma` | `ghcr.io/chroma-core/chroma:0.5.20` | `chroma` | 备选 |
| `milvus`(etcd+minio+milvus-standalone) | `milvusdb/milvus:v2.6.3` | `milvus` | 备选（3 容器） |
| `elasticsearch` + `kibana` | `docker.elastic.co/...:8.14.3` | `elasticsearch` | 备选 |
| `opensearch` + `opensearch-dashboards` | latest | `opensearch` | 备选 |
| `couchbase-server` / `vastbase` / `pgvecto-rs` / `iris` / `oracle` / `opengauss` / `myscale` / `matrixone` | 各自官方镜像 | 同名 | 备选（企业/国产化） |
| `unstructured` | `downloads.unstructured.io/...` | `unstructured` | 可选 ETL（需 `ETL_TYPE=Unstructured`） |
| `certbot` | `certbot/certbot` | `certbot` | 可选证书签发 |

`COMPOSE_PROFILES=${VECTOR_STORE:-weaviate},${DB_TYPE:-postgresql},collaboration`

存储类型支持极广（`STORAGE_TYPE=opendal` 默认 fs；可选 S3 / Azure Blob / Google Storage / 阿里云 OSS / 腾讯 COS / 华为 OBS / 火山 TOS / 百度 OBS / OCI 等）。

来源：<https://raw.githubusercontent.com/langgenius/dify/main/docker/docker-compose.yaml>

### 2.2 最低资源要求（官方原文）

- **CPU ≥ 2 Core**
- **RAM ≥ 4 GiB**
- macOS 10.14+：Docker Desktop + Docker Compose **2.24.0+**，Docker VM 至少 **2 vCPU + 8 GiB**
- Linux：Docker 19.03+ ，Docker Compose 2.24.0+
- Windows：WSL 2 + Docker Desktop，源码/数据必须放 Linux 文件系统

来源：<https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose.md>

> ⚠️ 注意：4 GiB 是"能跑起来"的下限，**不是生产可用下限**。仅 `agent_backend` + `local_sandbox` + `plugin_daemon` + `sandbox` + 向量库 + Postgres + Redis 就有 9 个常驻容器；若启用 milvus profile 还要再加 etcd/minio。

### 2.3 外部化依赖的可行性与配置项（全部【官方确认】）

**外部 PostgreSQL**

```env
DB_TYPE=postgresql            # 可选 mysql / oceanbase / seekdb
DB_HOST=<external-host>       # 默认 db_postgres
DB_PORT=5432
DB_USERNAME=postgres
DB_PASSWORD=difyai123456
DB_DATABASE=dify
# 连接池
SQLALCHEMY_POOL_SIZE=30
SQLALCHEMY_MAX_OVERFLOW=10            # 默认最多 30+10=40 连接
SQLALCHEMY_POOL_RECYCLE=3600
SQLALCHEMY_POOL_TIMEOUT=30
SQLALCHEMY_POOL_RESET_ON_RETURN=rollback
SQLALCHEMY_POOL_PRE_PING=false        # 官方建议生产/网络不稳时开启
SQLALCHEMY_POOL_USE_LIFO=false
```
- `POSTGRES_*`（`MAX_CONNECTIONS=200`、`SHARED_BUFFERS` 等）是**传给 Postgres 容器启动参数**的，用外部库时不生效，需在外部库侧自行配置。【官方确认】
- 官方连接数经验公式：`POSTGRES_MAX_CONNECTIONS ≥ SERVER_WORKER_AMOUNT × SERVER_WORKER_CONNECTIONS + CELERY_WORKER_AMOUNT + headroom`。【官方确认】

**外部 Redis**

```env
REDIS_HOST / REDIS_PORT / REDIS_USERNAME / REDIS_PASSWORD
REDIS_USE_SSL / REDIS_SSL_CERT_REQS / REDIS_SSL_CA_CERTS / REDIS_SSL_CERTFILE / REDIS_SSL_KEYFILE
REDIS_DB=0
REDIS_KEY_PREFIX=
REDIS_MAX_CONNECTIONS=
REDIS_RETRY_RETRIES=3 / REDIS_RETRY_BACKOFF_BASE=1.0 / REDIS_RETRY_BACKOFF_CAP=10.0
REDIS_SOCKET_TIMEOUT=5.0 / REDIS_SOCKET_CONNECT_TIMEOUT=5.0 / REDIS_HEALTH_CHECK_INTERVAL=30
REDIS_KEEPALIVE=true / ..._IDLE / ..._INTERVAL / ..._COUNT
CELERY_BROKER_URL=redis://:pw@host:6379/1     # 注意默认走 db 1
CELERY_BACKEND=redis
CELERY_USE_SENTINEL / CELERY_SENTINEL_MASTER_NAME / CELERY_SENTINEL_PASSWORD / CELERY_SENTINEL_SOCKET_TIMEOUT
EVENT_BUS_REDIS_URL / EVENT_BUS_REDIS_CHANNEL_TYPE=pubsub / EVENT_BUS_REDIS_USE_CLUSTERS=false
DIFY_AGENT_REDIS_URL=<独立 db，默认 db 2>
```
- 官方文档明确支持 **standalone / Sentinel / Cluster** 三种模式。【官方确认】
- ⚠️ `REDIS_SOCKET_TIMEOUT` 曾被错误地传给 Socket.IO 的 pub/sub 阻塞监听，导致协作 WebSocket 每 5 秒重连一次（issue #39423，1.16.0，已修复）。**Redis 侧超时参数与阻塞式 pub/sub 的兼容性值得在压测中重点验证。**【GitHub issue 证据】

**外部向量库**

```env
VECTOR_STORE=weaviate          # 也可 qdrant / pgvector / milvus / elasticsearch / opensearch / chroma / ...
VECTOR_INDEX_NAME_PREFIX=Vector_index

# Weaviate
WEAVIATE_ENDPOINT=http://weaviate:8080
WEAVIATE_GRPC_ENDPOINT=grpc://weaviate:50051
WEAVIATE_API_KEY=...
WEAVIATE_TOKENIZATION=word

# Qdrant
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=...
QDRANT_CLIENT_TIMEOUT=20
QDRANT_GRPC_ENABLED=false
QDRANT_GRPC_PORT=6334
QDRANT_REPLICATION_FACTOR=1

# Milvus（默认 URI 就指向 host.docker.internal，说明外部化是预期用法）
MILVUS_URI=http://host.docker.internal:19530
MILVUS_TOKEN / MILVUS_USER / MILVUS_PASSWORD / MILVUS_DATABASE / MILVUS_ENABLE_HYBRID_SEARCH
```
- compose 里有 `./envs/vectorstores/*.env.example` 模板，官方推荐做法是 `cp envs/vectorstores/milvus.env.example envs/vectorstores/milvus.env` 后编辑。【官方确认】
- ⚠️ 混合检索能力**依向量库而异**：`OCEANBASE_ENABLE_HYBRID_SEARCH=false`、`MILVUS_ENABLE_HYBRID_SEARCH=False`、`TENCENT_VECTOR_DB_ENABLE_HYBRID_SEARCH=false` 都是独立开关，部分库默认关闭混合检索。【源码确认】

**外部对象存储**

```env
STORAGE_TYPE=opendal           # 默认 fs
OPENDAL_SCHEME=fs / OPENDAL_FS_ROOT=storage
# 或 S3_ENDPOINT/S3_REGION/S3_BUCKET_NAME/S3_ADDRESS_STYLE/S3_USE_AWS_MANAGED_IAM ...
```
- `plugin_daemon` **有自己独立的一套存储配置**（`PLUGIN_STORAGE_TYPE`、`PLUGIN_S3_*`、`PLUGIN_AZURE_*`、`PLUGIN_ALIYUN_OSS_*`、`PLUGIN_TENCENT_COS_*`、`PLUGIN_VOLCENGINE_TOS_*`），外部化时要**配两遍**。【源码确认】

来源（除标注外）：<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>、<https://raw.githubusercontent.com/langgenius/dify/main/docker/.env.example>、<https://raw.githubusercontent.com/langgenius/dify/main/docker/envs/core-services/shared.env.example>

### 2.4 外部化时最需要注意的三个坑

**坑 1：SSRF 代理默认拒绝私网（对本方案影响最大）**

`docker/.env.example` 中的官方注释原文：
```
# Comma-separated CIDR ranges that the SSRF proxy should allow even when they
# resolve to private, loopback, link-local, or otherwise non-public addresses.
# Leave empty (the default) to keep the deny-by-default policy. Required when
# Dify needs to reach internal HTTP endpoints (e.g. an internal API on
# http://172.21.x.x) from an HTTP Request node, a tool, or a plugin.
# Example: SSRF_PROXY_ALLOW_PRIVATE_IPS=172.21.0.0/16,10.0.0.0/8
SSRF_PROXY_ALLOW_PRIVATE_IPS=
```
另有一个 `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS` 变量存在（见 compose）。**如果 LangGraph 服务部署在同一内网，一定要配这个变量，否则 HTTP Request 节点/Tool 直接连不通。**【官方确认】
> 注意：这是"全 Dify 出站"的白名单，放行 `10.0.0.0/8` 会削弱 SSRF 防护面，建议只放行 LangGraph 服务所在的最小子网。

**坑 2：`agent_ssrf_proxy` 是独立的、更严的代理**
`local_sandbox` 无 `api` 直连路由，出口只有 `agent_ssrf_proxy`，而后者"只允许 agent_backend `/agent-stub/` 和 Dify API `/files/*`"。compose 注释还明确写道："The agent runtime respects HTTP(S)_PROXY, but arbitrary code execution is still possible through the shellctl channel."（**已知局限**）。【源码确认】

**坑 3：`RESPECT_XFORWARD_HEADERS_ENABLED` 的双重默认值**
- `docker/envs/core-services/shared.env.example` 默认 `false`，注释："Bare API default. Compose enables this for its API services behind Nginx. Override Compose through docker/.env, the shell, or a Compose override, not this env_file."
- `.env.example` 默认 `true`，注释："The bundled Nginx supplies a verified client IP; keep direct API callers trusted."
**即：用自建反向代理暴露 API 时，客户端 IP 归属/限流会有偏差。**【源码确认】

### 2.5 升级风险（与 1.17.1 直接相关）

1. **Weaviate 分阶段升级**：内置 Weaviate `1.27.0 → 1.39.2`（12 个 minor），跳过 minor 不受支持，官方明说可能"静默且永久地破坏向量检索"。Fresh install / 外部 Weaviate / 其他向量库不受影响。文档有专门页面：`/en/self-host/deploy/troubleshooting/weaviate-server-migration-path`。
2. **模型类型数据迁移**：1.15 之前的安装遗留 `text-generation` / `embeddings` / `reranking` 旧枚举值会导致模型 API 请求失败。1.16.1 与 1.17.0 需要手动执行 `flask data-migrate legacy-model-types`；1.17.1 起已并入 `flask db upgrade`。
3. **知识库抽取 bug 修复后需要重新导入文档**：1.17.1 修复了一批"改变文档内容但不报错"的抽取 bug（CSV 前导零被当成浮点、Notion 表格丢列、`.xls` 双引号破坏行、内置网页抓取返回 Python dict 字面量、URL 加载 PDF 直接 AssertionError 崩溃等），官方明确："**Re-import affected documents after upgrading; indexed text is not retroactively repaired.**"

来源：<https://api.github.com/repos/langgenius/dify/releases?per_page=6>

参考链接：
- <https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose.md>
- <https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>
- <https://raw.githubusercontent.com/langgenius/dify/main/docker/docker-compose.yaml>
- <https://raw.githubusercontent.com/langgenius/dify/main/docker/.env.example>
- <https://raw.githubusercontent.com/langgenius/dify/main/docker/envs/core-services/shared.env.example>

---

## 3. 工作流 / 编排能力：Workflow vs Chatflow、节点清单、循环迭代并行、HITL

### 结论

1. **Workflow 与 Chatflow 共用同一套画布与节点系统**，唯一本质差异是"用户如何交互"：Workflow 跑一次返回结果；Chatflow 加了会话层，每条消息触发一次流程。【官方确认】
2. **节点类型远多于问题里列的 14 个**。除列出的全部节点外，还有 **List Operator、Variable Assigner、Doc Extractor、Output、以及 3 类 Trigger 节点（Webhook / Schedule / Plugin）**；另有新增的 **Agent V2 节点（beta，仅 Workflow）**。【官方确认】
3. **循环、迭代、并行、子流程全部支持，但都是有硬上限的"有界"实现**：Loop ≤ 100 次、Iteration ≤ 99、Agent 迭代 ≤ 99、并行分支 ≤ 10、单执行路径节点数 ≤ 50、子工作流调用深度 ≤ 5、单次运行节点执行数 ≤ 500。**不支持无界状态机循环。**【官方确认】
4. **Dify 1.17 已原生支持 human-in-the-loop（Human Input 节点）**，而且**暂停/恢复可以完全由外部系统通过 Service API 驱动**（拿到 `form_token` → GET 表单 → POST 提交 → 重连流）。这对本方案是**重大利好**：HITL 不必强制放在 LangGraph。【官方确认】
5. **没有独立的"并行分支"节点**——并行是靠画布扇出隐式表达的；汇聚节点的下游可以同时读取所有并行分支的变量，但并行分支之间**不能互相读取变量**。【官方确认】
6. **子流程 = Workflow-as-Tool**：任何以 User Input 节点开头的 Workflow 都能变成一个 Tool 被复用；**Chatflow 不能当 Tool**。【官方确认】

### 3.1 Workflow vs Chatflow 对比（官方表格）

| | Workflow | Chatflow |
|---|---|---|
| 起点 | User Input **或 Trigger** | User Input |
| 终点 | Output 节点（**可选**） | Answer 节点（**必需**） |
| 是否会话态 | 无，每次调用独立 | 有，每条消息触发一次流程 |
| 支持 Trigger | ✅ | ❌（Chatflow 永远从用户消息开始） |
| 附加能力 | — | 内容审核、TTS 等（见 App Toolkit） |

官方原文要点："A **Workflow** runs once from start to finish." / "A **Chatflow** adds a conversation layer."
来源：<https://docs.dify.ai/en/cloud/use-dify/build/workflow-chatflow.md>

补充（来自 Service API 文档）：Workflow app 的 API 调用"**There's no conversation state between calls, so each run is independent of any previous one.**"
来源：<https://docs.dify.ai/en/api-reference/guides/workflow.md>

### 3.2 节点类型清单（官方文档导航 + 源码两处交叉验证）

**官方文档 `nodes/` 目录下有独立页面的节点：**
`agent`、`answer`、`code`、`doc-extractor`、`http-request`、`human-input`、`ifelse`、`iteration`、`knowledge-retrieval`、`list-operator`、`llm`、`loop`、`output`、`parameter-extractor`、`question-classifier`、`start`(=User Input)、`template`、`tools`、`user-input`、`variable-aggregator`、`variable-assigner`
**Trigger 子类：** `trigger/overview`、`trigger/webhook-trigger`、`trigger/schedule-trigger`、`trigger/plugin-trigger`
来源：<https://docs.dify.ai/sitemap.xml>、<https://docs.dify.ai/en/cloud/use-dify/build/workflow-chatflow.md>

**源码中 `api/core/workflow/nodes/` 目录（部分节点已迁移到独立引擎包）：**
`agent`、`agent_v2`、`datasource`、`human_input`、`knowledge_index`、`knowledge_retrieval`、`trigger_plugin`、`trigger_schedule`、`trigger_webhook`
来源：<https://api.github.com/repos/langgenius/dify/contents/api/core/workflow/nodes>

> 【推断】`start` 节点源码在提交 `83e76817` 中路径为 `api/core/workflow/nodes/start/start_node.py`（确实存在），但 main 分支的目录列表里未再出现 —— 说明 1.17 起**工作流引擎正在被提取为独立的 `graphon` 包**（`api/controllers/service_api/app/workflow.py` 中有 `from graphon.enums import WorkflowExecutionStatus`、`from graphon.graph_engine.manager import GraphEngineManager`）。仓库根目录没有 `graphon/`（`/contents/graphon` 返回 404），其分发形式 **【未查证】**。对使用方无影响，但意味着**引擎内部 API 不稳定，不要依赖内部模块路径**。

与问题清单的对照：LLM ✅ / Knowledge Retrieval ✅ / Code ✅ / HTTP Request ✅ / Template ✅ / If-Else ✅ / Iteration ✅ / Loop ✅ / Variable Aggregator ✅ / Question Classifier ✅ / Parameter Extractor ✅ / Agent ✅ / Answer ✅ / Tool ✅。**额外还有**：List Operator、Variable Assigner、Doc Extractor、Output、User Input、3 类 Trigger、Agent V2（beta）、以及 Knowledge Pipeline 用的 Datasource / Knowledge Index。

### 3.3 循环 / 迭代 / 并行 / 子流程 —— 与硬上限

**官方原文（编排逻辑页）：**
> "**In series**, nodes run one after another. Each node can read variables from any node earlier in the chain."
> "**In parallel**, nodes run at the same time. They can't read each other's variables, but where parallel branches converge, the downstream node can read from all of them."
> "A single execution path supports up to **50 nodes**."
来源：<https://docs.dify.ai/en/cloud/use-dify/build/orchestrate-node.md>

**全部相关上限（来自环境变量参考，均为默认值）：**

| 变量 | 默认值 | 含义 |
|---|---|---|
| `WORKFLOW_MAX_EXECUTION_STEPS` | `500` | 单次工作流的**节点执行次数**上限，超出终止工作流 |
| `WORKFLOW_MAX_EXECUTION_TIME` | `3600` | 单次工作流**墙钟时间**上限（秒），超出终止 |
| `WORKFLOW_CALL_MAX_DEPTH` | `5` | 工作流嵌套调用工作流的最大深度（防无限递归） |
| `MAX_VARIABLE_SIZE` | `204800` | 单个工作流变量最大字节数（200 KB） |
| `LOOP_NODE_MAX_COUNT` | `100` | Loop 节点最大迭代次数 |
| `MAX_PARALLEL_LIMIT` | `10` | 工作流最大并行分支数 |
| `MAX_ITERATIONS_NUM` | `99` | Agent 单次执行的最大推理迭代次数 |
| `MAX_TOOLS_NUM` | `10` | Agent 可同时使用的最大工具数 |
| `MAX_TREE_DEPTH` | `50` | 单条执行路径上的最大节点数（**超长的路径无法发布**；改动只对新发布的工作流生效） |
| `WORKFLOW_FILE_UPLOAD_LIMIT` | `10` | 单个文件上传字段的 max-files 上限 |
| `GRAPH_ENGINE_MIN_WORKERS` / `MAX_WORKERS` | `3` / `10` | GraphEngine 实例的工作池上下限 |
| `GRAPH_ENGINE_SCALE_UP_THRESHOLD` | `0` | 有任务等待即扩容 |
| `GRAPH_ENGINE_SCALE_DOWN_IDLE_TIME` | `5.0` | 空闲多少秒后回收 worker |

来源：<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>

**需要留意的两个 1.17.1 相关修复（说明这些能力此前不稳）：**
- `Loop` 布尔型 break 条件此前"永远不匹配"（`"true"`/`"false"` 被存成字符串），1.17.1 修复（#41186）。
- **Human Input 此前无法放进 Iteration / Loop 节点内**，1.17.0 有运行时支持但编辑器校验阻止了选择/连接/粘贴，1.17.1 修复（#40061）。
来源：<https://api.github.com/repos/langgenius/dify/releases?per_page=6>

**子流程（Workflow-as-Tool）官方原文要点：**
> "Turn any Workflow that starts with a User Input node into a tool, so you can reuse multi-step logic across apps. **Chatflows cannot be used as tools.**"
> "Creating the tool publishes the workflow first, so the tool matches the workflow as it stands."
来源：<https://docs.dify.ai/en/cloud/use-dify/workspace/tools.md>

1.17.1 修复了一个相关 bug：Workflow-as-Tool 此前"只暴露第一个 End 节点的输出"，现在收集所有 End 节点的输出声明（#41517）。【官方确认】

**错误处理与重试（节点级）：**
- 最多 **10 次自动重试**，重试间隔**最大 5000 ms**。
- 可定义 fallback 分支（Code 节点出错时工作流仍可继续）。
- 有专门的 "Predefined error handling logic" 文档页。
来源：<https://docs.dify.ai/en/cloud/use-dify/nodes/code.md>、<https://docs.dify.ai/en/cloud/use-dify/build/predefined-error-handling-logic>

### 3.4 Human-in-the-Loop（Human Input 节点）—— 重点章节

**配置项（官方）：**
- **Delivery method**：`Web app`（不适用于 Trigger 启动的工作流）或 `Email`（发给 workspace 成员/外部邮箱，任何人有点击链接即可回复，无需 Dify 账号）。
- **Form content**：Markdown 渲染 + 可引用上游工作流变量（**web app 投递时表单直接展示给终端用户，所以 Human Input 之前不需要 Answer/Output 节点**）。响应后完整内容作为 `__rendered_content` 变量可用；文件字段渲染为纯文本占位符 `[file]` / `[N files]`。
- **Form 字段类型与约束**：

  | 字段类型 | 说明 |
  |---|---|
  | Paragraph | 文本输入，**无长度上限**（但过长会超出下游 LLM 上下文）。**唯一可选字段** |
  | Select | 单选，可手工定义选项，也可引用 `array[string]` 变量 |
  | Single File / File List | 单文件/多文件。**单文件上限：图片 10 MB、文档与其他 15 MB、音频 50 MB、视频 100 MB。File List 最多 10 个文件。** 强制必填 |
  | （Select / Single File / File List 均为必填，未填完则动作按钮禁用） |

- **User Action**：每个按钮有 title + action id，点击后 id 暴露为 `__action_id`，title 暴露为 `__action_value`。
- **Timeout**：**默认 3 天**；超时走 timeout 分支；**若没有连 timeout 分支，工作流直接结束**。
- **全局暂停上限**：`HUMAN_INPUT_GLOBAL_TIMEOUT_SECONDS`，默认 `7 天`（源码 `default=int(timedelta(days=7).total_seconds())`）。【源码确认】
- **表单"首次响应即关闭"，无论投递方式**。
- 超时检测由 Celery 任务 `ENABLE_HUMAN_INPUT_TIMEOUT_TASK=true` / `HUMAN_INPUT_TIMEOUT_TASK_INTERVAL=1`（分钟）驱动。

来源：<https://docs.dify.ai/en/cloud/use-dify/nodes/human-input.md>、<https://raw.githubusercontent.com/langgenius/dify/main/api/configs/feature/__init__.py>

**API 驱动的完整 HITL 闭环（对"Dify 被外部后端调用"场景至关重要）：**

1. 以 **streaming** 模式启动（`POST /v1/workflows/run` 或 chat-messages）。
2. 监听 SSE，捕获 `human_input_required` 事件 → 取 `form_token` 与 `workflow_run_id`；随后流以 `workflow_paused` 结束。
   - ⚠️ **若 `form_token` 为 `null`，说明表单走的是 Email 投递，无法通过 API 驱动。**
3. `GET /form/human_input/{form_token}` 拿表单定义（`form_content`、`inputs[]`、`user_actions[]`、`expiration_time`）。
4. （仅本地文件输入）先 `POST /files/upload` 拿 `id`，作为 `upload_file_id` 提交。**整个 run/upload/submit 必须使用同一个 `user`。**
5. `POST /form/human_input/{form_token}` 提交：`{ inputs: {...}, action: "<action-id>", user: "..." }`。**提交成功是终态，表单关闭，同一 token 不能重复提交**；失败（非法 action / 缺必填项 / 远程文件拉取失败）**表单状态不变，可修复后用同一 token 重试**。
6. 用 `GET /v1/workflows/run/{workflow_run_id}/events` 重连流：
   - 必须带**与启动时相同的 `user`**，否则 **404**。
   - `include_state_snapshot=true` → 先重放已执行节点的状态。
   - `continue_on_pause=true` → 让一条流跨越**多个** Human Input 暂停点（默认每遇到一个暂停就关流）。
   - 重连后的事件序列含 `human_input_form_filled`（或超时时的 `human_input_form_timeout`）。

来源：<https://docs.dify.ai/en/api-reference/guides/human-input-flow.md>、<https://docs.dify.ai/en/api-reference/guides/workflow.md>、<https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow.md>

**已知缺口【GitHub issue 证据】**
- 独立 Agent app（mode=agent，beta）**目前不会发出 `human_input_required` 事件**，其唯一 HITL 机制 `dify.ask_human` 会退化成"纯文本问答"，**没有表单控件、没有强制的 Approve/Reject 按钮**，对"写操作前结构化确认"的语义模型是回退。issue #41996（2026-09-08，open）——**对 Chatflow/Workflow 无影响，但若你们打算用 Agent app 形态则需注意。**
  来源：<https://github.com/langgenius/dify/issues/41996>

参考链接：
- <https://docs.dify.ai/en/cloud/use-dify/build/workflow-chatflow.md>
- <https://docs.dify.ai/en/cloud/use-dify/build/orchestrate-node.md>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/human-input.md>
- <https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>

---

## 4. API 能力（含 `inputs` 类型限制 —— 重点查证）

### 结论

1. **⚠️ 最重要的一条：问题里"`inputs` 只接受字符串与文件对象"的说法在 1.17.x 上已经过时。** 当前 OpenAPI 明确 `inputs` 的值类型是 `oneOf: [string, number, boolean, object, array<InputFileObject>]`，源码是 `inputs: dict[str, Any]`。【官方确认 + 源码确认】
2. **但"能传任意 JSON"≠"能被工作流消费"**。`inputs` 的键必须对应 **User Input 节点已声明的变量**；变量的可用类型是固定枚举，**没有顶层数组类型**。结构化对象必须声明为 **JSON Code（`json-object`）** 类型变量。【官方确认 + 源码确认】
3. **JSON Code 类型是 2025-12-03 才落地的**（commit `83e76817`，PR #29053 "feat: start node support json schema"），且**支持可选 JSON Schema 校验**（Draft 7）。在此之前的官方立场是"不支持，在路线图上"（issue #22010 于 2025-09-14 以 not planned 关闭；issue #29846 请求 object/array 类型于 2026-02-02 以 not planned 关闭）。【源码确认 + issue 证据】
4. **`/v1/workflow/run` 的关键路径是 `/workflows/run`**（另有 `POST /workflows/{workflow_id}/run` 按 version 执行）。blocking vs streaming 由 `response_mode` 决定，**省略时默认 blocking**。【官方确认】
5. **一个响应里有两个 ID**：`task_id`（控制用，仅执行期间有效）+ `workflow_run_id`（持久记录标识）。两者都要存。【官方确认】
6. **流式断线可以恢复（这是 1.x 后期的重要改进）**，但 **API Key 粒度粗、且自托管默认没有 per-app 并发上限**。【官方确认】

### 4.1 Service API 全貌

**认证**：`Authorization: Bearer {API_KEY}`，**app key 与 knowledge key 是两套**。缺失/非法 → HTTP 401 `unauthorized`。官方明确："Call the API from your backend only. A key embedded in frontend code or a client app can be extracted and abused."【官方确认】

**App API Key 粒度**：
- "create an API key from inside the app itself. **The key is scoped to that one app, and one key serves all your end users.**"
- 终端用户区分靠请求体里的 `user` 字段（"End User Identity"），`user` 本身也是数据隔离维度：**"a workflow run and its files are only visible to later requests that carry the same `user`"**。
来源：<https://docs.dify.ai/en/api-reference/guides/get-started.md>、<https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow.md>

**Knowledge API Key 粒度（1.17.1 新特性）**：
- 此前 knowledge key 是**整个 workspace 级**的（一个 key 可读写所有知识库）。1.17.1 起可**绑定到特定知识库**，创建时提供 workspace / this-knowledge-base 作用域选择器。绑定后的 key 访问其他 dataset（文档与检索）以及无 dataset ID 的端点（如 list-all）**返回 403**。**存量 key 保持 workspace 级行为不变。**（#37569）
来源：<https://api.github.com/repos/langgenius/dify/releases?per_page=6>

**Workflow 相关端点（全部【官方确认】）**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/workflows/run` | 执行**当前已发布**的工作流；需有已发布版本 |
| POST | `/v1/workflows/{workflow_id}/run` | 执行**指定已发布版本**（`workflow_id` 来自先前 run 响应或 run detail） |
| POST | `/v1/workflows/tasks/{task_id}/stop` | 取消运行中的任务。**仅 streaming 模式有效**；blocking 调用返回时已结束 |
| GET | `/v1/workflows/run/{workflow_run_id}` | 取运行状态与输出（"Get Workflow Run Detail"） |
| GET | `/v1/workflows/logs` | **run 级汇总**日志（status / token 用量 / 步数 / 耗时），**不是逐节点执行日志**；支持关键词、状态、时间范围、触发者过滤 |
| GET | `/v1/workflows/run/{workflow_run_id}/events` | **重连/恢复事件流**（"Stream Workflow Events"） |

其他 App API：chat-messages（含 stop / suggested questions）、completion-messages、conversations、files（upload / download）、audio（ASR/TTS）、applications（get-app-info / meta / parameters / webapp-settings）、feedback、annotations、end-users、human-input。

来源：<https://docs.dify.ai/en/api-reference/guides/workflow.md>、<https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow.md>、<https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow-by-id.md>

**`response_mode` 语义（官方原文，来自 OpenAPI description）**
> "Response mode. Use `blocking` for synchronous responses (**Cloudflare timeout is `100 s`**), or `streaming` for Server-Sent Events. **When omitted, defaults to blocking behavior.**"

**`/v1/workflow/{id}/run`（by ID）的额外限制**
- 只能跑**已发布**版本；draft → 400 `bad_request`；ID 格式错 → 400。
- **Cloud 版 Sandbox 套餐 → 403 `workflow_version_execution_not_allowed`**（"Upgrade to a paid plan"）。源码里这个检查被 `if dify_config.DEPLOYMENT_EDITION == DeploymentEdition.CLOUD` 包住，**自托管 CE 不受此限制**。【源码确认】
- Trigger 入口的工作流**不能**通过 Web App / Service API / OpenAPI / MCP 调用 → 403 `trigger_workflow_service_mode_unavailable`。**只有以 User Input 节点开头的工作流才能走 API。**（1.17.1 新增此拒绝逻辑，#41519）

### 4.2 `inputs` 类型限制 —— 逐层查证（本节最关键）

**第 1 层：HTTP/Schema 层 —— 接受任意 JSON 值**

当前 OpenAPI（`/en/api-reference/openapi_service.json`，由真实代码生成）：

```yaml
WorkflowExecutionRequest:
  properties:
    inputs:
      additionalProperties:
        oneOf:
          - type: string
          - type: number
          - type: boolean
          - type: object
          - type: array
            items: { $ref: '#/components/schemas/InputFileObject' }
      type: object
    files:                       # 顶层还有独立的 files 数组
      items: { $ref: '#/components/schemas/InputFileObject' }
      type: array
      nullable: true
  required: [inputs, user]
```

对应源码（`api/controllers/common/controller_schemas.py`）：

```python
class WorkflowRunPayload(BaseModel):
    inputs: dict[str, Any] = Field(description="Key-value pairs for workflow input variables. ...")
    files: WorkflowInputFileList = Field(default=None, description="File list for workflow system file inputs. ...")
```

**结论：API 层不限制 JSON 结构。`inputs` 是 `dict[str, Any]`，任意嵌套对象/数字/布尔都能传进去。**"只接受字符串与文件对象"是**旧版文档的说法，已不适用于 1.17.x**。
来源：<https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow.md>、<https://raw.githubusercontent.com/langgenius/dify/main/api/controllers/common/controller_schemas.py>

**第 2 层：变量绑定层 —— 必须是已声明的变量，且类型受枚举约束**

`inputs` 的键必须匹配 User Input 节点里声明的变量名。**User Input 节点的可用字段类型（官方文档原文）：**

| 组 | 类型 | 约束 |
|---|---|---|
| 预置 | `userinput.files` | Workflow 中已被标记为 **legacy**（仅向后兼容），官方建议改用自定义 file 字段 |
| 预置 | `userinput.query` | 仅 Chatflow，自动捕获最新一轮用户消息 |
| 文本 | **Short Text** | **≤ 256 字符** |
| 文本 | **Paragraph** | 无长度限制 |
| 结构化 | **Select** | 下拉，预定义选项 |
| 结构化 | **Number** | 仅数值 |
| 结构化 | **Checkbox** | `true` / `false` |
| 结构化 | **JSON Code** | **接受 JSON 对象格式，可定义 JSON Schema 校验输入，并可引用对象的单个属性** |
| 文件 | **Single File** | 一个文件 |
| 文件 | **File List** | 多个文件 |

来源：<https://docs.dify.ai/en/cloud/use-dify/nodes/user-input.md>

**关键点：枚举里没有"顶层数组"类型。** 数组只能作为 JSON Code 对象内部的字段，或者以字符串形式传入后在 Code 节点解析。

**第 3 层：JSON Code 的运行时实现 —— 源码级确认**

commit `83e76817b60c0489abddedb5844f36675cdc9aaf`（author: fatelei，**2025-12-03**），message: `feat: start node support json schema`，改动 127 行：

```python
# api/core/app/app_config/entities.py
class VariableEntityType(StrEnum):
    ...
    CHECKBOX = "checkbox"
    JSON_OBJECT = "json-object"          # 新增

class VariableEntity(BaseModel):
    ...
    json_schema: dict[str, Any] | None = Field(default=None)   # 新增
```

```python
# api/core/workflow/nodes/start/start_node.py
from jsonschema import Draft7Validator, ValidationError

def _run(self) -> NodeRunResult:
    node_inputs = dict(self.graph_runtime_state.variable_pool.user_inputs)
    self._validate_and_normalize_json_object_inputs(node_inputs)   # 新增调用
    ...

def _validate_and_normalize_json_object_inputs(self, node_inputs):
    for variable in self.node_data.variables:
        if variable.type != VariableEntityType.JSON_OBJECT:
            continue
        key = variable.variable
        value = node_inputs.get(key)
        if value is None:
            if variable.required:
                raise ValueError(f"{key} is required in input form")
            continue
        # 支持前端传字符串或已经是对象
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                raise ValueError(f"{key} must be a valid JSON object")
            value = parsed
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a JSON object")
        schema = getattr(variable, "json_schema", None)
        if schema:
            ...  # 若为 str 则 json.loads，若为 dict 则直接用
            if schema_obj:
                try:
                    Draft7Validator(schema_obj).validate(value)
                except ValidationError as e:
                    raise ValueError(f"JSON object for '{key}' does not match schema: {e.message}")
        node_inputs[key] = value
```

**由此可确定的运行时行为：**
- ✅ JSON Code 变量**既接受 JSON 字符串，也接受已经是对象的值**（字符串会被 `json.loads`）。
- ✅ 值**必须是 `dict`（JSON 对象）**；传数组/标量 → `ValueError: "{key} must be a JSON object"`。
- ✅ 字符串不是合法 JSON → `"{key} must be a valid JSON object"`。
- ✅ required 且缺值 → `"{key} is required in input form"`。
- ✅ 配了 `json_schema` 则用 **jsonschema Draft7Validator** 强校验，不符 → `"JSON object for '{key}' does not match schema: {message}"`。
- 依赖：`api/pyproject.toml` 新增 `jsonschema>=4.25.1`；`api/uv.lock` 中同时可见 `langfuse`、`langsmith`、`litellm`、`weave` 等（说明 LLMOps 依赖内建）。

来源：<https://api.github.com/repos/langgenius/dify/commits/83e76817b60c0489abddedb5844f36675cdc9aaf>、<https://github.com/langgenius/dify/pull/29053>、<https://github.com/langgenius/dify/commit/83e76817b60c0489abddedb5844f36675cdc9aaf>

**第 4 层：历史证据（说明"不能传任意 JSON"这个印象的来源）**

- issue **#22010** "🔧 Support nested JSON object input in 'Start' node of workflows"（2025-07-07 创建，**2025-09-14 以 not planned 关闭**）。原文：
  > "the 'Start' node in the workflow does not currently allow configuring an input field that accepts this kind of structured object directly. ... **The only workaround is to pass the entire JSON as a string (via a 'Paragraph' input type) and then manually parse it in a Code block.** This adds complexity, introduces fragility, and increases the risk of parsing errors."
  来源：<https://github.com/langgenius/dify/issues/22010>
- issue **#29846** "Support Object and Array Types in Start Node"（2025-12-18 创建，**2026-02-02 以 not planned 关闭**）。原文关键句：
  > "While these data types are **already supported by the Dify backend**, the frontend currently lacks the functionality to configure such variables in the start node and input these values directly."
  来源：<https://github.com/langgenius/dify/issues/29846>
  > 【推断】结合 commit 时间线（json-object 于 2025-12-03 落地），#29846 的诉求在提交时**部分**已被满足（object 支持了），但**顶层 array 类型仍未支持**，这可能是它被 not planned 关闭的原因。**数组类型是否已支持：文档与源码均未见顶层 array 变量类型 → 视为不支持。**

- issue **#35032** "Object of type File is not JSON serializable"（1.13.3）：**把 workflow 发布成 tool 后，用 files 类型入参在 Chatflow 里调用会抛此错**。说明**文件对象在跨工作流/工具调用时存在序列化边界问题**。
  来源：<https://github.com/langgenius/dify/issues/35032>

**⚠️ 对本方案的实操建议（事实层面，不含实现方案）：**
- 若 8-Agent 诊断服务需要向 Dify 传结构化诊断上下文（如 `{resume: {...}, jd: {...}, scores: [...]}`），**必须先在 User Input 节点声明一个 JSON Code 变量（建议同时配 JSON Schema）**，然后把整个对象放进该变量。
- 顶层数组不可声明；数组要么放进对象，要么走字符串 + Code 节点解析。
- **未声明键或类型不匹配时究竟是"静默忽略"还是"报错"，我未在源码中逐行确认 → 【未查证】。** 稳妥假设是"不绑定、下游读不到"，但**落地前建议用一次真实调用验证**（这是本调研中我唯一建议实测的 API 语义点）。

### 4.3 流式 SSE 契约

**官方原文要点：**
- 除 `ping` 外，**每个事件都是一行 `data: ` 加一个 JSON 对象，空行分隔**。
- **Chatflow 与 Workflow 的流都以一个 `ping` 作为第一帧**，之后大约**每 10 秒**再来一个。keep-alive 的 `ping` 是裸的 `event: ping` 行，**没有 `data:` 负载**。
- **不要把第一帧当"已受理"信号，要以第一个 `data:` 事件（`workflow_started`）为准。**
- 客户端 read timeout 要**明显大于 10 秒**，否则空闲间隔会杀掉连接。

**Workflow 流的事件类型**：`workflow_started`、`node_started`、`node_finished`、`reasoning_chunk`、`text_chunk`、`human_input_required`、`human_input_form_filled`、`human_input_form_timeout`、`workflow_paused`、`workflow_finished`、`error`、`ping`。

**流生命周期（官方原文）**：
> "The stream closes when a `workflow_finished`, `workflow_paused`, or `error` event is received. **Errors are delivered in-stream with HTTP status `200`; inspect the event payload for details rather than relying on the status code.**"

**推理事件**：`reasoning_chunk` 来自 `reasoning_format: separated` 的 LLM 节点；连续拼接可重建完整推理；`is_final: true` 标记该节点思考结束（可能带空 reasoning）。与 chat app 不同，其 payload 里 `message_id: null` 且**没有 `conversation_id`**；并行的 `text_chunk` 里**不含 `<think>` 标签**。

**重连与恢复（官方）**：
- `task_id` 控制**进行中**的生成（stop 端点用）；`workflow_run_id` 是持久记录名。
- 除 `error` 外每个事件都带 `task_id`；workflow 与 node 事件带 `workflow_run_id`。**"Save `workflow_run_id` as soon as it arrives"** —— 断线后它是唯一的续接凭据。
- Workflow / Chatflow 的流断线**不是致命的**：用 `Stream Workflow Events` + `workflow_run_id` + 同一个 `user` 重开；`user` 不匹配 → 404。
- **重连到仍在运行的 workflow 后，官方建议用 `Get Workflow Run Detail` 确认完成，而不是只信重连流的最后一个事件。**
- **其他 reply（Chatbot / Legacy Agent / Agent / Completion）没有 resume 端点**，断线只能重新发请求。

来源：<https://docs.dify.ai/en/api-reference/guides/streaming.md>、<https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow.md>

### 4.4 文件上传

- `POST /v1/files/upload`，`Content-Type: multipart/form-data`，字段 `file`（binary，必填）+ `user`（可选，省略则归到共享的 `DEFAULT-USER`）。
- **返回 `id`，后续作为 `upload_file_id` 配合 `transfer_method: local_file` 引用。只有携带相同 `user` 的后续请求能引用该文件。**
- **大小上限（默认可被 `UPLOAD_*_FILE_SIZE_LIMIT` 覆盖）**：图片 **10 MB**、音频 **50 MB**、视频 **100 MB**、其他文件 **15 MB**。
- 扩展名黑名单：`UPLOAD_FILE_EXTENSION_BLACKLIST`（默认空）。文件名不得含 `/` 或 `\`。
- 错误码：400 `no_file_uploaded` / `too_many_files` / `filename_not_exists_error` / `invalid_param` / `file_extension_blocked`；**413 `file_too_large`（官方文档明确标注 `message` 当前返回空字符串，是已知后端 quirk，要靠 code + status 判断）**；415 `unsupported_file_type`。
- 文件类型枚举（`InputFileObject.type`）：`image` / `document` / `audio` / `video` / `custom`。`transfer_method`：`remote_url` / `local_file`。
  - `remote_url` 有**兼容别名**：源码里 `remote_url` 字段是 `url` 的 legacy alias；且 `upload_file_id` 在 `remote_url` 下也被接受（"for compatibility with persisted file references"）。
- 签名 URL 有效期：`FILES_ACCESS_TIMEOUT=300`（5 分钟）。

来源：<https://docs.dify.ai/en/api-reference/files/upload-file.md>、<https://raw.githubusercontent.com/langgenius/dify/main/api/controllers/common/controller_schemas.py>、<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>

### 4.5 错误码与限流

**统一错误信封**：`{ "code": ..., "message": ..., "status": ... }`。`code` 是稳定标识，`message` 是人类可读细节，`status` 与 HTTP 状态一致。【官方确认】

| 状态 | 含义 | 典型 code |
|---|---|---|
| 400 | 请求或应用配置无效 | `invalid_param`、`bad_request`、`app_unavailable`、provider 错误 |
| 401 | API key 缺失/无效 | `unauthorized` |
| 403 | key 在此无权操作：访问限制或套餐限制 | `forbidden`、`trigger_workflow_service_mode_unavailable`、`workflow_version_execution_not_allowed` |
| 404 | 资源不存在或对该 key/`user` 不可见 | `not_found` |
| 413 / 415 | 文件过大 / 类型不支持 | `file_too_large`、`unsupported_file_type` |
| 429 | 请求过多或配额耗尽 | `too_many_requests`、`rate_limit_error` |
| 500 | Dify 侧故障 | `internal_server_error` |

**两个 429 语义完全不同（官方原文）**
> "`too_many_requests` is a **concurrency ceiling** — too many simultaneous requests for the app right now. Back off and retry."
> "`rate_limit_error` is a **plan quota on Dify Cloud**, such as workflow executions. Retrying won't clear it."

**无法靠重试解决的 400（provider 配置错误）**：`provider_not_initialize`、`provider_quota_exceeded`、`model_currently_not_support`、`completion_request_error`。官方："retrying won't help. Fix the app's model configuration in Dify."

**403 的坑（官方原文警告）**
> "On Dify Cloud, knowledge write endpoints also enforce plan limits as `403` responses. These carry the same `forbidden` code as access restrictions — the `message` is what tells you it's a plan limit, **so don't build a switch on `code` alone for 403s.**"

**官方重试建议**
- 应带 backoff 重试：`too_many_requests`、`500`、网络故障。
- 不要原样重试：校验错误、授权失败、配额错误。
- 重连的 404 表示 `user` 不对或 run 不存在 → 改标识符，不要重试。

来源：<https://docs.dify.ai/en/api-reference/guides/errors.md>

**自托管的并发限制（重要且反直觉）**
```env
APP_DEFAULT_ACTIVE_REQUESTS=0    # 每个 app 的默认并发上限，0 = 不限
APP_MAX_ACTIVE_REQUESTS=0        # 全局每 app 并发天花板，0 = 不限
```
**默认两者都是 0（不限）。** 那么自托管下 `too_many_requests` 从何处产生，我**未查证**。生产上**必须自己设这两个值**，否则单 app 可能被打满。
相关容量参数：`SERVER_WORKER_AMOUNT=1`（gevent，通常够）、`SERVER_WORKER_CONNECTIONS=10`、`CELERY_WORKER_AMOUNT=4`、`GRAPH_ENGINE_MIN/MAX_WORKERS=3/10`、`TENANT_ISOLATED_TASK_CONCURRENCY=1`、`QUEUE_MONITOR_THRESHOLD=200`（仅监控告警，不拒绝）。

**另一套 API 表面（与 app key 无关）**：`/openapi/v1/*`，供 `difyctl` 等程序化客户端使用；`OPENAPI_ENABLED` **默认 false**，`OPENAPI_RATE_LIMIT_PER_TOKEN=60`（每 token 每分钟，按 token 的 sha256 做桶、经 Redis 跨副本共享，设 0 关闭），`ENABLE_OAUTH_BEARER`（设备流 + Service API `/v1/*` bearer 中间件）。【源码确认】

来源：<https://raw.githubusercontent.com/langgenius/dify/main/api/configs/feature/__init__.py>

参考链接：
- <https://docs.dify.ai/en/api-reference/guides/get-started.md>
- <https://docs.dify.ai/en/api-reference/guides/workflow.md>
- <https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow.md>
- <https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow-by-id.md>
- <https://docs.dify.ai/en/api-reference/guides/streaming.md>
- <https://docs.dify.ai/en/api-reference/guides/errors.md>
- <https://docs.dify.ai/en/api-reference/files/upload-file.md>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/user-input.md>
- <https://github.com/langgenius/dify/commit/83e76817b60c0489abddedb5844f36675cdc9aaf>

---

## 5. 知识库 / RAG

### 结论

1. **索引方式二选一，且不可逆**：**High Quality**（用 embedding 模型向量化，支持 Vector / Full-Text / Hybrid 三种检索）与 **Economical**（每 chunk 取 10 个关键词倒排索引，**不消耗 token**，只支持倒排 + TopK）。**HQ 建好后不能切回 Economical**；Economical 可以升级到 HQ。【官方确认】
2. **一个反直觉的关键限制：TopK 与 Score Threshold 只在 Rerank 阶段生效。** 官方原文："The TopK and Score configurations are only effective during the Rerank phase. Therefore, to apply either of these settings, it is necessary to add and enable a Rerank model." **不启用 Rerank 模型，TopK/阈值形同虚设。**【官方确认】
3. **External Knowledge API 契约完全公开且字段明确**，请求体 `{knowledge_id, query, retrieval_setting{top_k, score_threshold}, metadata_condition?}`，响应 `{records:[{content, score, title, metadata}]}`。**`metadata_condition` 是"可用但无 UI"的编程接口。**【官方确认】
4. **Knowledge Pipeline 已存在**（把文档处理做成可视工作流：数据源 → 抽取 → 处理 → 入库），并已有 4 个 Service API 端点。【官方确认】
5. **Metadata 过滤能力完整**：字段类型 String / Number / Time，三段式过滤模式（Disabled / Automatic / Manual），运算符齐全，AND/OR，**大小写敏感且要求精确匹配**。【官方确认】
6. **没有找到"按 external_id upsert"的增量同步语义**；文档更新走 `update-document-by-text` / `update-document-by-file` + 索引状态轮询。**【未查证 = 不存在 upsert-by-external-id】**

### 5.1 索引与检索参数

**High Quality 的三种检索方式（官方）**

| 方式 | 说明 | 可配参数 |
|---|---|---|
| **Vector Search** | 把用户问题向量化，与库中文本向量比对找最近 chunk | Rerank 模型（默认关）、TopK（默认 **3**）、Score Threshold（默认 **0.5**） |
| **Full-Text Search** | 对文档所有词建索引，返回含该词的片段 | 同上 |
| **Hybrid Search** | 同时做全文与向量检索，再重排 | **Weight Settings**（semantic / keyword 权重，**不需要 Rerank 模型 API**）或 **Rerank 模型**；TopK；Score Threshold |

- **Weight 语义**：`Semantic = 1` → 只走语义检索（跨语言也能匹配意义）；`Keyword = 1` → 只走关键词精确匹配（资源消耗低）；也可自定义权重比。
- **TopK 会自动按所选模型上下文窗口调整**。
- Rerank 模型官方示例：**Cohere、Jina AI** 等，需在 Model Provider 页填 API Key。**启用 Rerank 会消耗该模型的 token。**
- **Economical**：只有倒排索引，只支持 TopK。

**两层检索设置（官方明确）**
> "Retrieval settings exist at two layers: the knowledge base level and the knowledge retrieval node level. Think of them as two consecutive filters: the knowledge base settings determine the initial pool of results, and the node settings further rerank the results or narrow down the pool."

- **节点级**才有的 **Weighted Score**："available **only when all added knowledge bases are indexed with High Quality mode**"。
- 多知识库：**先从所有库同时检索，再按节点级设置合并处理**；如果**多个库的 embedding 模型不一致，Weight Score 不可调并会报错**（建议统一检索设置或启用 Rerank）。

**多模态（Vision）**
- 选**多模态 embedding 模型**（标 Vision 图标）→ 文档中抽取的图片也会被 embed 并索引，知识库卡片标 **Multimodal**；支持跨模态检索（同时返回文和图）。
- **Query Images 每张图 ≤ 2 MB**；该选项仅在至少有一个多模态库时出现。
- **如果选了多模态 embedding，必须同时选多模态 Rerank 模型，否则检索到的图片会在 rerank 阶段被排除。**

来源：<https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/setting-indexing-methods.md>、<https://docs.dify.ai/en/cloud/use-dify/nodes/knowledge-retrieval.md>

**`Knowledge Retrieval` 节点输出**：名为 `result` 的数组，元素是检索到的 chunk（含 content、metadata、title 等属性）；**若检索结果含图片附件，`result` 还会带 `files` 字段**。给 LLM 节点当 Context 用时，**启用 Vision 后 LLM 会自动访问检索到的图片**，无需显式指定。

**自托管相关上限**
```env
TOP_K_MAX_VALUE=10                                  # UI 里 TopK 滑块上限
INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH=4000        # 分段最大 token 长度
CHILD_CHUNKS_PREVIEW_NUMBER=50                      # 子 chunk 预览数上限
```
来源：<https://raw.githubusercontent.com/langgenius/dify/main/docker/.env.example>、<https://raw.githubusercontent.com/langgenius/dify/main/api/configs/feature/__init__.py>

### 5.2 External Knowledge API 完整契约（官方）

**认证**：`Authorization: Bearer {API_KEY}`。官方："Dify only passes the key—**it does not validate it**."（验证逻辑由你实现）

**请求**
```
POST {your-endpoint}/retrieval
Content-Type: application/json
Authorization: Bearer {API_KEY}
```
> "Dify appends `/retrieval` to the endpoint URL you configured. If you registered `https://your-service.com`, Dify sends requests to `https://your-service.com/retrieval`."

**Body**

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `knowledge_id` | ✅ | string | 外部系统中的知识源标识，即连接时填的 **External Knowledge ID**；用它路由到正确知识源 |
| `query` | ✅ | string | 用户检索词 |
| `retrieval_setting` | ✅ | object | 见下 |
| `metadata_condition` | ❌ | object | 元数据过滤条件，见下 |

**`retrieval_setting`**

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `top_k` | ✅ | int | 最多返回条数 |
| `score_threshold` | ✅ | float | 最小相似度（0–1）。**当 Dify 中关闭 score threshold 时，该值为 `0.0`** |

**`metadata_condition`**（官方 Info 卡片原文）
> "Dify passes metadata conditions to your API but **does not currently provide a UI for users to configure them. This parameter is available for programmatic use only.**"

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `logical_operator` | ❌ | string | `and` 或 `or`，默认 `and` |
| `conditions` | ✅ | array[object] | 条件列表 |

每个 condition：`name`（必填，字段名）、`comparison_operator`（必填）、`value`（可选；用 `empty` / `not empty` 时省略）。

**支持的运算符（官方折叠面板全量）**：
`contains`、`not contains`、`start with`、`end with`、`is`、`is not`、`in`、`not in`、`empty`、`not empty`、`=`、`≠`、`>`、`<`、`≥`、`≤`、`before`、`after`

**响应**
- 200 + `{ "records": [ ... ] }`；无命中返回 `{"records": []}`。
- 每条 record：

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | string | 检索到的文本 chunk，**Dify 用它作为传给 LLM 的上下文** |
| `score` | float | 相似度（0–1），用于阈值过滤与排序 |
| `title` | string | 来源文档标题 |
| `metadata` | object | 任意键值对，Dify 会保留 |

- 官方："Dify does not reject records with missing fields, but **omitting `content` or `score` will produce incomplete or unranked results**."
- **⚠️ Warning 原文**："If you include `metadata` in a record, it **must be an object (`{}`), not `null`**. A `null` metadata value **causes errors in Dify's retrieval pipeline**."

**错误处理**
- Dify 检查 HTTP 状态码，**非 200 会抛错并暴露给用户**。
- 可返回 `{error_code: int, error_msg: string}` 结构化信息。
- **推荐错误码是约定、非强制**：`1001` 无效 Authorization 头格式 / `1002` 授权失败 / `2001` 知识库不存在。

来源：<https://docs.dify.ai/en/cloud/use-dify/knowledge/external-knowledge-api.md>、连接方式见 <https://docs.dify.ai/en/cloud/use-dify/knowledge/connect-external-knowledge-base>

### 5.3 Knowledge Pipeline（存在，官方）

**四个阶段**：`Data Sources → Data Extraction → Data Processing → Knowledge Storage`。

**能力要点**
- 可**从内置模板 / 空白 / 导入已有 pipeline** 三种方式创建。
- 与工作流编排同构：可视组合处理节点与工具。
- 有 **Datasource 插件**（如 Google Drive、Notion 页面）作为外部内容源，需要先**授权数据源**。
- 流程：创建 → 编排 → **发布** → 上传文件 → 管理与使用。

**Service API 端点（官方文档有独立页）**
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/datasource-plugins` | 列出数据源插件 |
| POST | `run-datasource-node` | 执行数据源节点 |
| POST | `run-pipeline` | 执行 pipeline |
| POST | `upload-pipeline-file` | 上传 pipeline 文件 |

来源：<https://docs.dify.ai/en/cloud/use-dify/knowledge/knowledge-pipeline/readme.md>、<https://docs.dify.ai/sitemap.xml>

### 5.4 Chunk / 文档 / 元数据 API（增量同步相关）

**文档 API**
`create-document-by-text`、`create-document-by-file`、`update-document`、`update-document-by-text`、`update-document-by-file`、`delete-document`、`get-document`、`list-documents`、`get-document-indexing-status`、`update-document-status-in-batch`、`download-document`、`download-documents-as-zip`

**Chunk API**
`create-chunks`、`list-chunks`、`get-chunk`、`update-chunk`、`delete-chunk`，以及**子 chunk**：`create-child-chunk`、`list-child-chunks`、`update-child-chunk`、`delete-child-chunk`

**Metadata API**
`create-metadata-field`、`list-metadata-fields`、`get-built-in-metadata-fields`、`update-metadata-field`、`update-built-in-metadata-field`、`update-document-metadata-in-batch`、`delete-metadata-field`

**Tags API**
`create-knowledge-tag`、`list-knowledge-tags`、`update-knowledge-tag`、`delete-knowledge-tag`、`create-tag-binding`、`delete-tag-binding`、`get-knowledge-base-tags`

**Models API**：`get-available-models`

来源：<https://docs.dify.ai/sitemap.xml>（`/en/api-reference/{documents,chunks,metadata,tags,models}/*`）

**增量同步：官方支持的与不支持的**
- ✅ 支持：按文档 update-by-text / update-by-file、批量改状态、轮询 `get-document-indexing-status`、segments/chunks 级增删改、批量下载 zip（`DOCUMENT_BATCH_DOWNLOAD_ZIP_MAX_DOCS = 100`）。
- ❌ **没有查证到"按 external_id upsert"的语义** → 需要外部自己记录 document_id 映射。
- ⚠️ **1.17.1 起有一个重要行为**：一批抽取 bug 修复之前会**静默改变文档内容**。官方要求"Re-import affected documents after upgrading; **indexed text is not retroactively repaired**"。**即：修复只影响后续导入，已索引内容不会自动重建。**
- ⚠️ `DATASET_MAX_SEGMENTS_PER_REQUEST=0`（0 = 不限）、`ENABLE_CLEAN_EMBEDDING_CACHE_TASK=false`、`ENABLE_CLEAN_UNUSED_DATASETS_TASK=false`。

### 5.5 Metadata 字段与过滤

**字段类型**：`String` / `Number` / `Time`。命名规则：**仅小写字母、数字、下划线**。
**内置 Metadata（默认禁用，需手动激活；一旦生成，字段与值不可修改）**：`document_name`(string)、`uploader`(string)、`upload_date`(time)、`last_update_date`(time)、`source`(string)。作用域是"启用后对所有现存与新文档生效"。
**自定义 Metadata**：可编辑/删除；存储在 metadata 列表中，需手动赋给文档。

**Knowledge Retrieval 节点的过滤模式**：`Disabled`（默认）/ `Automatic`（依据节点中的 query 变量自动生成过滤；**需要为文档检索选择模型**）/ `Manual`。

**Manual 运算符全表（官方）**

| 字段类型 | 运算符与示例 |
|---|---|
| String | `is`（精确，如 `is "Published"`）、`is not`、`is empty`、`is not empty`、`contains`、`not contains`、`starts with`、`ends with` |
| Number | `=`、`≠`、`>`、`<`、`≥`、`≤`、`is empty`、`is not empty` |
| Date | `is`、`before`、`after`、`is empty`、`is not empty` |

- 逻辑组合：`AND`（全部匹配）/ `OR`（任一匹配）。
- 值来源：**Variable**（选 work flow 变量）或 **Constant**（填常量）。**时间类型字段只能用常量过滤。**
- 多知识库时：**下拉中只显示共有的 metadata 字段**。
- **⚠️ 官方 Info 原文**："Filter values are **case-sensitive** and require **exact matches**. Example: a filter `starts with "App"` or `contains "App"` will match `Apple` but **not** `apple` or `APPLE`."

**删除语义差异（官方表格摘要）**：在 Metadata Panel 删除 = 全局删除（所有文档的该字段与值永久消失）；在 Metadata Editor 删除 = 仅选定文档；在文档详情页删除 = 仅当前文档。

来源：<https://docs.dify.ai/en/cloud/use-dify/knowledge/metadata.md>、<https://docs.dify.ai/en/cloud/use-dify/knowledge/integrate-knowledge-within-application.md>

### 5.6 自定义 Embedding 模型

- **官方路径是"模型供应商插件"体系**：`Integrations > Model Provider` 从 Marketplace 安装 provider，或从 GitHub URL 安装，或上传本地 `.zip`（私有/内部插件）。【官方确认】
- **`Add Model`**：当需要的模型不在列表里（如全新模型或微调模型）时，可在 provider 卡片上 **Add Model** 手工添加模型名与凭据。**"Providers that serve only a fixed set of models don't offer this option."** 若添加的模型名与类型与已有模型相同，Dify 会把新 key 挂到已有模型上而非新建重复项。【官方确认】
- **默认模型设置**：`Default Models` 可分别设定 System Reasoning / **Embedding** / **Rerank** / Speech-to-Text / Text-to-Speech。【官方确认】
- **多 key 与负载均衡**：可给同一模型配多个 key；**Load Balancing 是 Professional/Team 套餐特性（Cloud）**，轮询使用并让触发限流的 key 休息 1 分钟。自托管是否可用受 `DEPLOYMENT_EDITION` 影响 → **【未查证】**。
- **OpenAI 兼容 / 本地 Ollama / Xinference**：Dify 长期通过 Marketplace 提供这些 provider 插件，但**我这次没有逐个抓取它们的文档页 → 【未查证具体页与当前插件名】。** 可确定的是：**"自定义/私有模型"的官方机制是模型供应商插件 + Add Model**，且 `api/uv.lock` 中确有 `litellm` 依赖。
- **⚠️ 换 embedding 模型后是否需要重建索引**：文档未明说 → **【未查证】**。但 §5.1 提到"多个知识库 embedding 模型不一致会导致 Weight Score 不可调"——说明 embedding 模型是库级固定属性，**保守假设"换模型必须重建/重新导入"**。

来源：<https://docs.dify.ai/en/cloud/use-dify/workspace/model-providers.md>、<https://docs.dify.ai/en/cloud/use-dify/workspace/plugins.md>

### 5.7 知识库请求限流（Cloud 套餐）

| 套餐 | 限制 |
|---|---|
| Sandbox | 10 / min |
| Professional | 100 / min |
| Team | 1,000 / min |

覆盖 18 类操作：创建/删除 dataset、改设置、上传/删除/更新/禁用/启用/归档/恢复文档、暂停/恢复文档处理、增删改 segment、批量导入 segment、**hit test**、以及**"在 app 或 workflow 中查询知识库"（Note: Multi-path recalls count as a single request）**。
> 官方举例："if a Sandbox user performs 10 hit tests within one minute, their workspace will be temporarily unable to perform the restricted actions during the following minute."

**自托管是否强制这套限流：文档未说明 → 【未查证】。** 我在 `docker/envs/core-services/shared.env.example` 中**没有**看到对应的知识库限流环境变量，倾向于认为**自托管不强制**（【推断】）。

来源：<https://docs.dify.ai/en/cloud/use-dify/knowledge/knowledge-request-rate-limit.md>

参考链接：
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/external-knowledge-api.md>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/setting-indexing-methods.md>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/knowledge-retrieval.md>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/metadata.md>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/integrate-knowledge-within-application.md>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/knowledge-pipeline/readme.md>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/knowledge-request-rate-limit.md>

---

## 6. Prompt 管理与 CI 化（DSL / difyctl）

### 结论

1. **🛑 核心答案：没有任何官方文档化的 API 可以读取/写入"线上应用的 prompt 或节点配置"。** Service API（app key）的 Applications 组只有 `get-app-info`、`get-app-meta`、`get-app-parameters`、`get-app-webapp-settings` 四个只读端点，**没有写配置的端点**。【官方确认】
2. **官方唯一的程序化配置路径是 DSL（YAML）+ `difyctl`**，且 `difyctl import studio-app --app-id <id>` **可以覆盖已有应用**。【官方确认】
3. **⚠️ 但 DSL 路径有两个关键语义陷阱**：(a) Workflow/Chatflow 的 `export` 导出的是 **Current Draft 而不是已发布版本**；(b) `import` **写到 Draft**，**必须在 Dify 里手动 Publish 才会生效**（`run app` 用的是已发布版本）。所以"API 覆盖应用"≠"线上立即生效"。【官方确认】
4. **`difyctl` 依赖 `/openapi/v1/*`，该组默认关闭**（`OPENAPI_ENABLED=false`），且用 OAuth 设备流认证（`ENABLE_OAUTH_BEARER`），默认 **60 请求/分钟/token**。【源码确认】
5. **1.17 的版本控制（Draft / Latest / Previous Versions）是纯 UI 能力**：可命名版本、写 release notes、把旧版本 restore 回 draft。**没有文档化的 API 来创建/publish 一个版本。**（发布动作是否可 API 化 → **文档未说明**）【官方确认 + 未查证】
6. **外部 prompt 版本管理走社区插件**：官方文档确实介绍了 **Langfuse Prompt Management Plugin**（Get / Search / Update prompt），但明确标注 **community maintained**（`gao-ai-com/dify-plugin-langfuse`）。【官方确认】

### 6.1 App 内 Prompt 编辑与调试（Prompt IDE 相关能力）

从官方文档可见的应用构建/调试能力：
- **Prompt 里用 Jinja2 引用上游变量**：`{{#user_input.topic#}}`、`{{#generate_draft.text#}}`、`{{#human_input.feedback#}}` 这类语法在 Human Input 示例中大量出现。【官方确认】
- **Debug 文档族**：`/en/cloud/use-dify/debug/step-run`（单步运行）、`/en/cloud/use-dify/debug/variable-inspect`（变量检查）、`/en/cloud/use-dify/debug/history-and-logs`、`/en/cloud/use-dify/debug/error-type`。【官方确认，来自 sitemap】
- **Run 级调试**：Logs 页点开一个 run，可看输入输出/状态/token，以及**逐节点 trace**，并能点 **Test With Params** 用该次 run 的输入在编辑器里重跑。【官方确认】
- **LLM 节点有 "Enable Reasoning Tag Separation"**（把思考过程与答案分离，影响 Human Input 表单展示与 `reasoning_chunk` 事件）。【官方确认】
- **Prompt 里的 Code Inspector / Markdown 表单字段**等细节存在 `MARKDOWN_FORM_FIELD_NAME_MAX_LENGTH=128`、`MARKDOWN_FORM_FIELD_NAME_EXTRA_CHARS` 等开关。【源码确认】
- **Agent node（classic）的 Memory** 使用 `TokenBufferMemory`，可选记忆窗口大小。【官方确认】

### 6.2 版本控制（Version Control）—— UI 能力

官方原文：
- **Current Draft**：工作版本，改动在此进行，**不对外生效**。
- **Latest Version**：线上版本，用户看到的。
- **Previous Versions**：更早的已发布版本。
- 操作：`Publish → Publish Update` 让 draft 成为新的 Latest，同时得到一个新的 draft。
- 可按"全部 / 仅我的 / 仅命名版本"过滤；可**命名版本**、**编辑版本信息（名称 + release notes）**、**删除旧版本**（**不能删除 Current Draft 与 Latest Version**）、**Restore a version**（**会完全替换当前 draft**）。
- **套餐门槛（Cloud，官方 Info 卡片原文）**：
  > "Restoring a version, exporting a specific version's DSL, and running a specific published version through the Service API are available on paid plans. **On the Sandbox plan, apps always run the latest published version.**"
- **适用范围**："Only available for Chatflow and Workflow apps right now."

来源：<https://docs.dify.ai/en/cloud/use-dify/build/version-control.md>

> ⚠️ 注意区分：`workflow_id`（版本标识，用于 `run-workflow-by-id` 与 `difyctl --workflow-id`）与 app_id（应用标识）不是一回事。DSL export 的 `--workflow-id` 可导出**指定的已发布版本**。

### 6.3 DSL（YAML）导入导出与 `difyctl`

**`difyctl` 是什么（官方）**
> "Run your Dify apps from terminals, scripts, CI pipelines, and AI agents with the official Dify command-line interface `difyctl`."
> "Dify CLI (difyctl) closes that gap: anything that can run a shell command can run your Dify apps."

能力：跑任意 app 并捕获输出；列 app 并查看其期望的 inputs；流式输出或结构化 JSON；**OAuth 登录一次后自动化（不必在脚本里存密码或 API key）**；让 AI agent 把 Dify app 当工具发现与调用。
来源：<https://docs.dify.ai/en/cli/overview.md>

**命令全集（官方 apps 参考）**

| 命令 | 作用 |
|---|---|
| `difyctl get app [app-id]` | 列 app（`--name` / `--mode chat\|advanced-chat\|agent-chat\|workflow\|completion` / `--page` / `--limit 1..200`） |
| `difyctl describe app <app-id>` | 看单个 app 详情与 **`input_schema`**；`--refresh` 绕过本地缓存 |
| `difyctl run app <app-id> [message]` | 跑 app。Workflow 用 `--inputs '<json>'` 或 `--inputs-file`；`--file key=@path` 传文件；`--conversation` 续会话；`--workflow-id` **钉住某个已发布版本**；`--stream`；`--think`；`--retry-on-limit` |
| `difyctl resume app <app-id> <form-token> --workflow-run-id <id>` | 提交暂停中的 Human Input 表单并接着跑；`--action`、`--inputs`、`--with-history` |
| `difyctl export studio-app <app-id>` | **导出 DSL YAML**；`-o/--output <path>`、`--include-secret`、`--workflow-id <id>` |
| `difyctl import studio-app (--from-file <path> \| --from-url <url>)` | **导入 DSL**；`--name`、`--description`、**`--app-id <id>`（覆盖已有 app）**、`--icon-type`、`--icon`、`--icon-background` |

**⚠️ 三个关键语义（官方原文，对 CI 设计至关重要）**

1. **导出的是 Draft，不是线上版本**
   > "For Workflow and Chatflow apps, **export returns the current draft**, not the published version that `run app` executes. Use `--workflow-id` to export a specific published version instead. Chatbot, Agent, and Text Generator apps **export the published version**."

2. **导入写到 Draft，必须手动 Publish**
   > "For Workflow and Chatflow apps, it **writes the definition to the app's draft**. `run app` uses the published version, **so publish the app in Dify after importing for the change to take effect**."

3. **导入的副作用与告警**
   - DSL 版本不一致 → CLI 会确认并提示两个版本号。
   - **缺少插件依赖** → stderr 列出 `Missing plugin dependencies`，需先安装。
   - `DSL_EXPORT_ENCRYPT_DATASET_ID` **默认 `true`**（导出时加密 dataset ID）；跨环境导入时官方建议**设为 `false`** 以导出明文 ID。【官方确认，环境变量页】

**幂等性与成本提醒（官方原文）**
> `--retry-on-limit`: "On a 429 rate limit, wait and retry the run instead of failing with exit `7`. **Off by default, since a run isn't idempotent.**"

**退出码**：`0` 成功（**包括因 HITL 暂停**）/ `1` 网络或服务器错误 / `2` 用法错误 / `4` 认证失败 / `7` 被限流（HTTP 429）。
> 官方贴心提示："**a paused run and a completed run both exit 0, so don't branch on the exit code.** Run workflows with `-o json` and check stdout for `"status": "paused"`."

**认证底层**：`difyctl` 走 `/openapi/v1/*`，需 `OPENAPI_ENABLED=true`（默认 false）；`OPENAPI_KNOWN_CLIENT_IDS` 默认 `difyctl`（未知 client_id 在 `POST /openapi/v1/oauth/device/code` 返回 400 `unsupported_client`）；`OPENAPI_RATE_LIMIT_PER_TOKEN=60`/分钟；`DEVICE_FLOW_APPROVE_RATE_LIMIT_PER_HOUR=10`；`DIFY_ENV_NACOS_*` 等远程配置源超时也在同一文件。**其余变量含义见官方 CLI reference 的 authentication / environments 页（我未逐页抓取 → 部分【未查证】）。**

来源：<https://docs.dify.ai/en/cli/reference/apps.md>、<https://raw.githubusercontent.com/langgenius/dify/main/api/configs/feature/__init__.py>

**DSL 作为 IaC 的成熟度信号**：仓库中有一个**开放的** enhancement proposal：
- issue **#42075** "Proposal: Extending `openapi` group to support IaC (Infrastructure as Code) integration"（作者 wylswz，2026-09-09 创建，**open**，assignee 是自己，标签 `review: high`）。
**说明"用 API 做应用配置的 IaC"在 1.17.x 时点仍是进行中的提案，不是成熟能力。**【GitHub issue 证据】
来源：<https://github.com/langgenius/dify/issues/42075>

### 6.4 外部 Prompt 管理（Langfuse Prompt Management Plugin）

官方文档明确介绍（原文摘要）：
> "The **Langfuse Prompt Management Plugin** (`https://github.com/gao-ai-com/dify-plugin-langfuse`) (**community maintained**) lets you use prompts that are managed and versioned in Langfuse in your Dify applications... Key features include: **Get Prompt** / **Search Prompts** / **Update Prompt**（在 Langfuse 创建新版本并设置 tags/labels）"

来源：<https://docs.dify.ai/en/cloud/use-dify/monitor/integrations/integrate-langfuse.md>

> 【推断】这实际上是"想要 prompt 版本化 + API 管理"的**当前最现实路径**：把 prompt 的单一事实来源放在 Langfuse（或你们的代码仓库），Dify 侧只做调用，避免 §10 提到的"两个事实来源"问题。**但该插件是社区维护，不是官方一等公民。**

参考链接：
- <https://docs.dify.ai/en/api-reference/applications/get-app-parameters>
- <https://docs.dify.ai/en/cloud/use-dify/build/version-control.md>
- <https://docs.dify.ai/en/cli/overview.md>
- <https://docs.dify.ai/en/cli/reference/apps.md>
- <https://github.com/langgenius/dify/issues/42075>
- <https://docs.dify.ai/en/cloud/use-dify/monitor/integrations/integrate-langfuse.md>

---

## 7. 可观测性

### 结论

1. **内置可观测性分为三块**：**Logs**（会话日志 + 工作流 run 日志 + 逐节点 trace）、**Annotations/Annotation Reply**（标注问答对，可直接影响线上回复）、**Dashboard/Analysis**（分析）。**没有内置的分布式追踪 UI**——追踪要外接 provider。【官方确认】
2. **原生支持多家追踪：Langfuse、LangSmith、Arize、Phoenix（Arize Phoenix）、Opik、Weave、Aliyun**，共 7 家官方文档页。**配置方式是"每个 app 在 Monitoring → Tracing app performance 里粘贴 provider 凭据"，而不是（当前文档里的）环境变量。**【官方确认】
3. **self-host 的追踪投递由后台任务完成**，有专门的韧性参数：`OPS_TRACE_UNIFIED_ENABLED=false`、`OPS_TRACE_RETRYABLE_DISPATCH_MAX_RETRIES=780`、`OPS_TRACE_RETRYABLE_DISPATCH_DELAY_SECONDS=5`、`OPS_TRACE_PARENT_CONTEXT_TTL_SECONDS=3900`。**重试窗口 ≈ 65 分钟，专门设计为"超过 1 小时的工作流上限 + 导出宽限期"。**【官方确认】
4. **OpenTelemetry 是原生支持但默认关闭**：`ENABLE_OTEL=false`，且**默认采样率只有 10%**（`OTEL_SAMPLING_RATE=0.1`）。**不显式打开就等于没有 trace。**【源码确认】
5. **⚠️ 自托管 Phoenix 有一个硬约束**：官方原文"Dify sends traces **server-side**, so a **self-hosted endpoint must be reachable from the internet**"。即内网自建 Phoenix 除非对 Dify 可达，否则收不到数据。【官方确认】
6. **日志保留策略在 Cloud 上有套餐差异**，自托管需要自己开清理任务（默认关闭）。【官方确认】

### 7.1 内置日志与标注

**Logs 覆盖范围**
- 哪些 app 记什么：**Chatbot / Chatflow / Agent / Text Generator → 记会话；Workflow → 记 run。**
- **所有渠道的用量都会被记录**：web app、API、Home 页 WEB APPS 列表打开的 app。Workflow 的 **trigger 启动的 run 也会被记录**。
- **编辑器里的测试会话被排除**（Chatflow 与 Workflow 的 **Preview** 与 **Test Run** 不计入）；其他 app 类型会列出，归在成员账号下。

**列含义**
- **Title**：由会话第一条消息生成。
- **End User or Account**：web app / API 用量显示 session ID；Dify 内部用量显示成员账号。
- **User Rate**：终端用户的 like/dislike 聚合；**Op. Rate**：你们团队的评分，两者**跨整个会话聚合**。
- **Trigger By**（仅 Workflow）：`WebApp`（已发布 web app **或走 API 的调用**）/ `Webhook` / `Schedule` / `Integration`。
- 搜索：会话可按消息内容或 session ID 搜；workflow run 可按输入/输出搜，**也可直接粘贴 run ID**。

**单个会话/run 的可观测深度**
- hover 一条回答：看到 **token 消耗与延迟**；打开背后的 log（**原始 prompt**、Agent app 的工具迭代、Chatflow 消息的**逐节点 trace**）；给团队评分（dislike 可带评论）；**添加 annotation**（把问题与改进后的回复配对）。
- Workflow run：结果 + 输入输出（含状态与 token 合计）+ **逐节点 trace**（定位失败或变慢的节点）；**Test With Params** 用该 run 的输入在编辑器重跑。
- 分享：复制浏览器地址栏链接即可。

**日志保留**
- **Cloud Sandbox：所有日志保留 30 天**，超期永久删除，**升级套餐也找不回**。
- **Cloud Professional / Team**：会话日志保留至订阅期结束；**workflow run 日志在 Logs 页保留最近约 3 个月**，更早的按月打包为 CSV 归档。
- **归档 CSV 构成（官方表格）**：`workflow_runs.csv`（每 run 一行：status/inputs/outputs/timing/token）、`workflow_app_logs.csv`（run↔app↔触发者）、`workflow_node_executions.csv`（逐节点）、`workflow_node_execution_offload.csv`（超大节点输入输出单独存）、`workflow_pauses.csv` + `workflow_pause_reasons.csv`（HITL 暂停）、`workflow_trigger_logs.csv`（trigger 调用）。
- **只有 owner 与 admin 能下载归档**；"Prepare download" 后台准备，准备好的下载**24 小时后失效**。

**自托管的日志保留**
```env
WORKFLOW_LOG_CLEANUP_ENABLED=false        # 默认关闭
WORKFLOW_LOG_RETENTION_DAYS=30
WORKFLOW_LOG_CLEANUP_BATCH_SIZE=100
WORKFLOW_LOG_CLEANUP_SPECIFIC_WORKFLOW_IDS=
WORKFLOW_NODE_EXECUTION_STORAGE=rdbms     # 或 hybrid：新数据存对象存储，读取时两者都读
```
**默认不清日志。** 若工作流量大，`workflow_node_executions` 会持续膨胀，**必须显式开启清理或改用 `hybrid`**。【官方确认】

来源：<https://docs.dify.ai/en/cloud/use-dify/monitor/logs.md>、<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>

### 7.2 追踪集成（7 家 provider）

| Provider | 官方文档路径 | 配置方式（官方） |
|---|---|---|
| **Langfuse** | `/en/cloud/use-dify/monitor/integrations/integrate-langfuse` | 在 Langfuse 建 project → `Create API Keys` → 复制 **Secret Key / Public Key / Host** → 在 Dify app 的 **Monitoring → Tracing app performance** 粘贴保存 |
| **LangSmith** | `.../integrate-langsmith` | 同上路径（细节未抓取） |
| **Arize（Arize AX）** | `.../integrate-arize` | 同上 |
| **Phoenix（Arize Phoenix）** | `.../integrate-phoenix` | **API Key + project name**；用 Phoenix Cloud 时还需 **Space Hostname** |
| **Opik** | `.../integrate-opik` | 同上路径 |
| **Weave** | `.../integrate-weave` | 同上路径 |
| **Aliyun** | `.../integrate-aliyun` | 同上路径 |

**Langfuse 的字段映射非常细（官方表格），包括：**
- **Workflow/Chatflow Trace**：`workflow_app_log_id`/`workflow_run_id` → `id`；`user_session_id` → `user_id`；`{"workflow_{id}"}` → `name`；inputs/outputs → `input`/`output`；token → `usage`；error → `level` 与 `status_message`；`[workflow]` → `tags`；`conversation_id` → `parent_observation_id`。
- **Workflow Trace metadata 字段（节选）**：`workflow_id`、`conversation_id`、`workflow_run_id`、`tenant_id`、`elapsed_time`、`status`、**`version`**、`total_tokens`、`file_list`、**`triggered_from`**、`workflow_run_inputs/outputs`、`error`、`query`、`message_id`、start/end time、**`workflow node executions`**。
- **Message Trace**（Generation/Trace）：`message_id` → `id`；`[ "message", conversation_mode ]` → `tags`；`conversation_id` → `session_id`；metadata 含 `ls_provider`、`ls_model_name`、`status`、`from_end_user_id`、`from_account_id`、`agent_based`、`workflow_run_id`、`from_source`。
- **Moderation Trace**：`user_id`、`moderation`、flagged、action、preset_response，tags `[moderation]`。
- **Suggested Question Trace**：`suggested_question`、tags `[suggested_question]`。
- **Dataset Retrieval Trace**：`dataset_retrieval`、inputs、**documents**、tags `[dataset_retrieval]`。
- **Tool Trace**：`tool_name`、tool_inputs/outputs、`tool_config`、`time_cost`、`tool_parameters`、`file_url`，tags `["tool", tool_name]`。
- **Generate Name Trace**：会话标题生成。

**Phoenix 的映射差异（官方）**：`user_session_id` 放在 **metadata** 里而不是 user_id；message 的 `name` 是 `"llm"`；retrieval 映射为 **Phoenix Retriever**，tool 映射为 **Phoenix Tool**；workflow 用 `conversation_id in metadata`。
**Phoenix 硬约束（官方原文）**：`Dify sends traces server-side, so a self-hosted endpoint must be reachable from the internet.`

来源：<https://docs.dify.ai/en/cloud/use-dify/monitor/integrations/integrate-langfuse.md>、<https://docs.dify.ai/en/cloud/use-dify/monitor/integrations/integrate-phoenix.md>

### 7.3 OpenTelemetry（自托管原生支持）

**⚠️ 默认全关 + 10% 采样。** 来自 `docker/envs/core-services/shared.env.example`：

```env
ENABLE_OTEL=false
OTLP_TRACE_ENDPOINT=
OTLP_METRIC_ENDPOINT=
OTLP_BASE_ENDPOINT=http://localhost:4318
OTLP_API_KEY=
OTEL_EXPORTER_OTLP_PROTOCOL=
OTEL_EXPORTER_TYPE=otlp
OTEL_SAMPLING_RATE=0.1                    # ← 默认 10%，生产追踪务必调
OTEL_BATCH_EXPORT_SCHEDULE_DELAY=5000
OTEL_MAX_QUEUE_SIZE=2048
OTEL_MAX_EXPORT_BATCH_SIZE=512
OTEL_METRIC_EXPORT_INTERVAL=60000
OTEL_BATCH_EXPORT_TIMEOUT=10000
OTEL_METRIC_EXPORT_TIMEOUT=30000
```

`api/configs/feature/__init__.py` 中确认存在 `ENABLE_OTEL`、`OTLP_TRACE_ENDPOINT`、`OTLP_METRIC_ENDPOINT`、`OTLP_BASE_ENDPOINT`。【源码确认】

**LLMOps 追踪投递（self-host）**
```env
OPS_TRACE_UNIFIED_ENABLED=false
OPS_TRACE_RETRYABLE_DISPATCH_MAX_RETRIES=780
OPS_TRACE_RETRYABLE_DISPATCH_DELAY_SECONDS=5
OPS_TRACE_PARENT_CONTEXT_TTL_SECONDS=3900
```
- 官方说明：应用的 trace **由后台任务投递**到配置的 LLMOps provider（Langfuse / LangSmith / Arize-Phoenix 等）。
- `OPS_TRACE_UNIFIED_ENABLED=true` 会给 **LangSmith 与 Arize/Phoenix** 的 trace 统一结构（workflow runs、nodes 及其父子关系）；`false` 用各家原生实现。**"Other providers are unaffected."**
- 默认重试窗口 = `780 × 5s ≈ 65 分钟`，"**sized to outlast the one-hour workflow limit plus the export grace period**"。
- 目前只有 **Arize/Phoenix 的嵌套工作流追踪**会走这条重试路径（子 trace 先于父 context 到达）。
- **约束**：`OPS_TRACE_PARENT_CONTEXT_TTL_SECONDS` 必须 ≥ 重试窗口，否则 **Dify 会拒绝加载配置**（源码 `model_validator` 会 `raise ValueError("OPS_TRACE_PARENT_CONTEXT_TTL_SECONDS must cover the retry window")`）。【源码确认】
- 官方补充："**Each app still needs LLMOps enabled and a provider configured.**"（即环境变量只是投递管道，app 级还要开）

来源：<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>、<https://raw.githubusercontent.com/langgenius/dify/main/api/configs/feature/__init__.py>

### 7.4 其他观测面

**Sentry**
```env
API_SENTRY_DSN= / API_SENTRY_TRACES_SAMPLE_RATE=1.0 / API_SENTRY_PROFILES_SAMPLE_RATE=1.0
WEB_SENTRY_DSN=
PLUGIN_SENTRY_ENABLED=false / PLUGIN_SENTRY_DSN=
DEPLOY_ENV=PRODUCTION        # 给 Sentry 与 OTel 数据打环境标签，同时作为 X-Env 响应头返回；不改变应用行为
```
**注意 `SENTRY_TRACES_SAMPLE_RATE` 与 `SENTRY_PROFILES_SAMPLE_RATE` 是 `API_SENTRY_*` 的"直接名"，Compose 栈里 `API_SENTRY_*` 优先**；profiling 只在已被 traces 采样的请求上再采，所以有效量 = traces 率 × profiles 率。【官方确认】

**结构化日志**
```env
LOG_OUTPUT_FORMAT=text        # 改 json 可直接喂 ELK / Datadog
LOG_FILE=/app/logs/server.log / LOG_FILE_MAX_SIZE=20 / LOG_FILE_BACKUP_COUNT=5
LOG_TZ=UTC                    # 仅 text 格式生效，JSON 恒为 UTC；同时设置 Celery 调度时区
ENABLE_REQUEST_LOGGING=false  # true 时记录 METHOD PATH STATUS DURATION TRACE_ID；配合 LOG_LEVEL=DEBUG 还会记录完整请求/响应体
DEBUG=false                   # true 会记录工作流节点输入输出、工具执行细节、完整 LLM prompt 与响应 —— 官方明确不建议用于生产
```
**注意 `DEBUG=true` 会打印完整 prompt 与响应**（可能泄露敏感数据），这是官方警告。【官方确认】

来源：<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>

参考链接：
- <https://docs.dify.ai/en/cloud/use-dify/monitor/logs.md>
- <https://docs.dify.ai/en/cloud/use-dify/monitor/integrations/integrate-langfuse.md>
- <https://docs.dify.ai/en/cloud/use-dify/monitor/integrations/integrate-phoenix.md>
- <https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>
- <https://raw.githubusercontent.com/langgenius/dify/main/docker/envs/core-services/shared.env.example>

---

## 8. 插件/工具生态、自定义工具、Code 节点沙箱限制

### 结论

1. **集成（Integration）分 7 类**：Model Provider、Tool、Data Source、Trigger、**Agent Strategy**、Custom Endpoint、Extension。三种安装来源：Marketplace（<https://marketplace.dify.ai>）、GitHub 公开仓库（URL + 版本）、本地 `.zip`（私有/内部）。【官方确认】
2. **自定义工具四条路**：**Swagger/OpenAPI schema 导入**、**MCP server（仅支持 HTTP transport）**、**Workflow-as-Tool**、**Tool 插件（用插件 SDK 自研）**。OpenAPI 支持**粘贴 schema、从 URL 导入、或用内置示例起步**。【官方确认】
3. **⚠️ 文档与默认配置存在矛盾**：Code 节点安全文档说沙箱"**blocks ... outbound network requests**"，但 `docker/.env.example` 里 **`SANDBOX_ENABLE_NETWORK=true`** 且配了 `HTTP_PROXY/HTTPS_PROXY=http://ssrf_proxy:3128`。**实际语义是"能出网，但走 SSRF 代理，私网默认被拒"。**【官方确认 + 源码确认】——**这个矛盾必须在设计时按"能出网"来假设。**
4. **Code 节点的真实执行超时是 15 秒**（`SANDBOX_WORKER_TIMEOUT=15`），而 API 侧读取超时是 60 秒（`CODE_EXECUTION_READ_TIMEOUT=60`）。**这是一个非常紧的限制。**【源码确认】
5. **Code 节点输出有严格的结构化上限**（字符串 40 万字符、嵌套 5 层、小数 20 位、数组元素数上限不同），**超限直接失败**，不是截断。【官方确认 + 源码确认】
6. **预装库清单官方文档没有穷举**，只说"a standard set of packages is pre-installed ... Imports outside that set will fail at execution time"，示例里提到 `json/math/datetime/re`、`numpy/pandas/requests`（Python）与 `lodash/moment`（JS）。**精确清单必须以沙箱镜像为准 → 【未查证】。**

### 8.1 插件体系

**插件类型（官方 "Integration Types"）**
| 类型 | 作用 |
|---|---|
| **Model Provider** | 驱动 app 的模型，从 LLM 到 embedding 与 rerank |
| **Tool** | agent 与 workflow 可调用的能力，含 tool 插件、MCP server、已发布的 workflow、OpenAPI 服务 |
| **Data Source** | pipeline 构建的知识库的外部内容（Google Drive、Notion 页面等） |
| **Trigger** | 外部事件触发工作流 |
| **Agent Strategy** | Agent 节点遵循的推理策略（决定如何决策、选工具、处理结果） |
| **Custom Endpoint** | 接你自己的 API，加内容审核或外部数据工具 |
| **Extension** | 暴露自定义 HTTP 端点，扩展其他类别覆盖不到的能力 |

**插件权限（workspace 级，owner/admin 可设，每项可选 Everyone / Admins / No one）**
- **Who can install and manage integrations** → 默认 **Everyone**
- **Who can debug integrations**（调试密钥访问权）→ 默认 **No one**
**自动更新策略**：按类别（tools / models / data sources …）各自设定（off / 仅 patch 版本 / always latest），可作用于整类或选定子集。

来源：<https://docs.dify.ai/en/cloud/use-dify/workspace/plugins.md>

**plugin_daemon 关键参数（self-host）**
```env
PLUGIN_DAEMON_URL=http://plugin_daemon:5002
PLUGIN_DAEMON_KEY=...
PLUGIN_DAEMON_TIMEOUT=600.0              # 官方文档；源码默认 600.0
PLUGIN_MAX_PACKAGE_SIZE=52428800         # 50 MB（源码 PluginConfig 默认 15 MB，Compose 覆盖为 50 MB）
PLUGIN_MAX_BUNDLE_SIZE=15728640*12       # ≈180 MB
PLUGIN_MAX_FILE_SIZE=52428800            # 50 MB —— 插件产出文件的单文件上限
PLUGIN_MAX_EXECUTION_TIMEOUT=600         # 插件执行超时上限（秒）
PLUGIN_PYTHON_ENV_INIT_TIMEOUT=120       # 插件 Python 环境初始化超时
PLUGIN_STDIO_BUFFER_SIZE=1024 / PLUGIN_STDIO_MAX_BUFFER_SIZE=5242880
PLUGIN_WORKING_PATH=/app/storage/cwd
PLUGIN_STORAGE_TYPE=local / PLUGIN_STORAGE_LOCAL_ROOT=/app/storage
PLUGIN_MODEL_SCHEMA_CACHE_TTL=3600
PLUGIN_MODEL_PROVIDERS_CACHE_ENABLED=true / PLUGIN_MODEL_PROVIDERS_CACHE_TTL=86400
FORCE_VERIFYING_SIGNATURE=true           # ← 默认强制校验签名
MARKETPLACE_ENABLED=true / MARKETPLACE_API_URL=https://marketplace.dify.ai
NEW_USER_DEFAULT_PLUGIN_IDS=             # 新用户自动安装的插件
PIP_MIRROR_URL=                          # 插件/沙箱装包镜像
```
**离线/内网部署路径**：`MARKETPLACE_ENABLED=false` + 本地 `.zip`/`.difypkg` 上传（官方 "Local upload: Custom `.zip` packages for private or internal integrations."）。**`FORCE_VERIFYING_SIGNATURE` 默认 true**，官方另有 "Third-Party Signature Verification" 标准文档页 → **内网自研插件若未签名，需要评估是否关闭该开关（关闭会削弱供应链安全）。**
**远程调试（开发自研插件）**：`INSTALL_METHOD=remote`、`REMOTE_INSTALL_URL`（如 `debug.dify.ai:5003`）、`REMOTE_INSTALL_KEY`，然后 `python -m main`；`PLUGIN_DEBUGGING_PORT=5003`、`EXPOSE_PLUGIN_DEBUGGING_PORT=5003`。【官方确认 + 源码确认】

**一个已知坑（源码注释里记录）**：issue **#39323** —— `PLUGIN_REMOTE_INSTALL_PORT` 不能是 `host:port` 形式（如 `127.0.0.1:5003`），否则 api 容器会以晦涩的 `int_parsing` traceback 崩溃。源码里专门加了 `_reject_host_port_shaped_plugin_remote_install_port` 校验并给出可操作提示。**要绑 loopback 请用 `docker-compose.override.yaml`。**【源码确认】

来源：<https://raw.githubusercontent.com/langgenius/dify/main/api/configs/feature/__init__.py>、<https://raw.githubusercontent.com/langgenius/dify/main/docker/.env.example>、<https://raw.githubusercontent.com/langgenius/dify/main/docker/docker-compose.yaml>

### 8.2 自定义工具（OpenAPI / MCP / Workflow / Tool 插件）

**A. Swagger（OpenAPI）导入**（官方 Tab "Swagger API"）
> "Integrate a service that isn't available as a tool plugin by **importing its OpenAPI (Swagger) specification**. Paste the schema, **import it from a URL**, or **start from the provided example**, and Dify generates the tool interface for you."

**B. MCP（作为客户端）**
- **⚠️ "Only MCP servers with HTTP transport are supported."**（不支持 stdio transport）
- 添加方式：URL + 名称 + **唯一 server identifier**。Dify 连接、必要时授权，然后导入该 server 的 tools。
- **认证**：**Dynamic Client Registration（默认开启）** —— 让 Dify 自动获取 OAuth 凭据；当 server 不支持自动注册，或必须用团队既有 OAuth 应用时关闭，填 **Client ID / Client Secret** 并注册 Dify 给出的 redirect URL。
- **Custom Headers**：给每个请求加额外 header（常用于静态 token/API key，如 `Authorization: Bearer <token>`）。
  **动态 header（很有用）**：`{{request.headers.X-Custom-Auth}}` 会在调用时被替换为**触发该次 run 的 HTTP 请求**的 `X-Custom-Auth` header（例如一次 Service API 调用）—— **可以把调用方的凭据透传到下游**。请求没有该 header 时解析为空；run 没有来源 HTTP 请求时，占位符原文发送。
- **⚠️ 三个变更风险（官方 Note）**：
  1. **app 通过 identifier 引用 server**；之后改 identifier 会让旧 identifier 的 tools 在 app 中失效，需在每个受影响的 app 里重新添加 tool 才能恢复。
  2. **导出的 app 也按 identifier 引用 server**，所以在另一个 workspace 运行需要**重建同名 identifier 的 server**。
  3. "You can update the tool list later to pull the server's latest tools, **though doing so can break an app if a tool it uses is removed or changed.**"
- **Timeouts**：可分别调 **request timeout** 与 **SSE read timeout**（长时/流式结果用后者）。官方："Change them only if you hit timeout errors."

**C. Workflow-as-Tool**
- 只能把**以 User Input 节点开头的 Workflow** 变成 tool；**Chatflow 不能**。
- **创建 tool 时会先把 workflow 发布一次**，使 tool 与 workflow 当前状态一致。（1.17.1 修复：之前 update 路径会先发布，create 路径会用旧的已发布版本（#41528）；另一修复：之前只暴露第一个 End 节点的输出（#41517））

**D. Tool 插件**：Marketplace 安装，部分（Google、GitHub 等）需要先认证 —— 可在 `Integrations > Tools > Tool Plugin` 设 workspace 级凭据，也可在 app 或节点内设置。

**E. Agent Strategy 插件**（这是"自带编排逻辑"的官方缝隙，详见 §10）
- 用 `dify plugin init` 选 `agent-strategy` 模板；核心是 `strategies/basic_agent.yaml`（声明参数）+ `strategies/basic_agent.py`（实现）。
- 推荐 4 个参数：`model`（`type: model-selector`, `scope: tool-call&llm`）、`tools`（`type: array[tools]`）、`query`（`type: string`）、`maximum_iterations`（`type: number`，示例 `default: 5`, `min: 1`, `max: 50`）。
- 用 `session.model.llm.invoke()` 调模型、`session.tool.invoke()` 调工具；用 `create_log_message` / `finish_log_message` 记录每步（支持 `parent` 嵌套）。
- 官方提供了完整 Function Calling 参考实现：`https://github.com/langgenius/dify-official-plugins/blob/main/agent-strategies/cot_agent/strategies/function_calling.py`
- 打包 `dify plugin package ./<dir>/` → `.difypkg`；发布到 `https://github.com/langgenius/dify-plugins`。

来源：<https://docs.dify.ai/en/cloud/use-dify/workspace/tools.md>、<https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/agent-strategy-plugin.md>

**F. API Tool 的默认超时（影响自定义工具调用外部服务）**
```env
API_TOOL_DEFAULT_CONNECT_TIMEOUT=10      # 建立 TCP 连接
API_TOOL_DEFAULT_READ_TIMEOUT=60         # 读取响应
```

### 8.3 Code 节点与沙箱限制（本节重点）

**语言与签名（官方）**
- **Python** 或 **JavaScript** 二选一。
- 必须定义 `main()`，**返回一个包含已声明输出变量的 dict**：
  ```python
  def main(input_variable: str) -> dict:
      return {'output_variable': result}
  ```

**沙箱隔离（官方原文）**
> "Code runs in an isolated sandbox that **blocks file system access, outbound network requests, and system commands**. ... Avoid reading system files, opening network connections, or running shell commands."

**⚠️ 但与默认配置矛盾：**
```env
SANDBOX_ENABLE_NETWORK=true                      # 默认 true
SANDBOX_HTTP_PROXY=http://ssrf_proxy:3128
SANDBOX_HTTPS_PROXY=http://ssrf_proxy:3128
```
**实际约束应理解为：能出网，但强制经 squid 代理；私网/回环/链路本地地址默认被拒（需 `SSRF_PROXY_ALLOW_PRIVATE_IPS`）。** 文档的"blocks outbound network requests"与默认配置不一致 —— **按"能出网但受限"来设计更安全。**【官方确认 + 源码确认】

**超时（关键数字，源码确认）**
| 变量 | 默认 | 位置/含义 |
|---|---|---|
| `SANDBOX_WORKER_TIMEOUT` | **15** | 沙箱服务侧 worker 超时（**秒**）→ **Code 节点实际执行超时** |
| `CODE_EXECUTION_READ_TIMEOUT` | 60 | API/Worker 侧读取沙箱响应的读超时 |
| `CODE_EXECUTION_CONNECT_TIMEOUT` | 10 | 连接沙箱的超时 |
| `CODE_EXECUTION_WRITE_TIMEOUT` | 10 | 写请求超时 |
| `SANDBOX_GIN_MODE` | `release` | 沙箱服务运行模式 |
| `SANDBOX_PORT` | 8194 | 沙箱监听端口 |
| `CODE_EXECUTION_ENDPOINT` | `http://sandbox:8194` | API/Worker 连沙箱的地址 |
| `CODE_EXECUTION_API_KEY` | `dify-sandbox` | 必须与 `SANDBOX_API_KEY` 一致 |
| `CODE_EXECUTION_POOL_MAX_CONNECTIONS` | 100 | 到沙箱的最大并发 HTTP 连接 |
| `CODE_EXECUTION_POOL_MAX_KEEPALIVE_CONNECTIONS` | 20 | 保活空闲连接数 |
| `CODE_EXECUTION_POOL_KEEPALIVE_EXPIRY` | 5.0 | 空闲连接关闭前秒数 |
| `PIP_MIRROR_URL` | （空） | 沙箱内装包的 pip 镜像 |

**输出上限（由 API/Worker 强制，沙箱不读这些；改后需重启 API 与 Worker，重启沙箱无效）**

| 变量 | 默认 | 含义 |
|---|---|---|
| `CODE_MAX_STRING_LENGTH` | **400000** | 输出字符串最大长度 |
| `CODE_MAX_DEPTH` | **5** | 输出数据结构的最大嵌套层数 |
| `CODE_MAX_PRECISION` | **20** | 浮点最大小数位 |
| `CODE_MAX_NUMBER` | `9223372036854775807` | 最大数值（64 位有符号整数上限） |
| `CODE_MIN_NUMBER` | `-9223372036854775808` | 最小数值 |
| `CODE_MAX_STRING_ARRAY_LENGTH` | **30** | 字符串数组最大元素数 |
| `CODE_MAX_OBJECT_ARRAY_LENGTH` | **30** | 对象数组最大元素数 |
| `CODE_MAX_NUMBER_ARRAY_LENGTH` | **1000** | 数字数组最大元素数 |
| `TEMPLATE_TRANSFORM_MAX_LENGTH` | **400000** | Template Transform 节点输出最大字符数 |

官方文档的对应表述：
> "**Strings**: up to 400,000 characters. **Numbers**: whole numbers up to 19 digits, and decimals up to 20 places. **Objects and arrays**: nested up to five levels deep. **An output that exceeds a limit fails the check**, so trim or split large results before returning them."

**⚠️ 注意 `CODE_MAX_STRING_ARRAY_LENGTH=30` 与 `CODE_MAX_OBJECT_ARRAY_LENGTH=30` 非常紧**——如果 Code 节点要返回一个 8-Agent 的诊断结果数组，30 个元素的上限需要确认是否够用（配合 `CODE_MAX_NUMBER_ARRAY_LENGTH=1000` 的差异，说明是刻意限制对象型数组）。

**重试与错误处理（官方）**
- **最多 10 次自动重试**，重试间隔可配，**最大 5000 ms**。
- 可定义 **Error Handling 的 fallback 路径**，让工作流在代码失败时继续运行。

**依赖（官方）**
- 文档说"Code nodes support external dependencies for both Python and JavaScript"，示例给出 Python `numpy` / `pandas` / `requests` 与 JS `lodash` / `moment`。
- **但**："A **standard set of packages is pre-installed** in the sandbox environment. **Imports outside that set will fail at execution time.**"
- **预装清单在文档中未穷举 → 【未查证】。** 官方仓库有专门的 sandbox 镜像（`langgenius/dify-sandbox:0.2.15`），compose 挂载 `./volumes/sandbox/dependencies` 与 `./volumes/sandbox/conf` —— **精确清单应查该镜像或其源码仓库。**

**内存上限**：文档与 `.env.example` 中**均未见** Code 节点内存限制变量 → **【未查证】**。（沙箱是独立容器，可用容器级 `deploy.resources.limits.memory` 约束，但这不是 Dify 的应用级限制。）

**HTTP Request 节点的相关上限（与 Code 节点配合使用）**
```env
HTTP_REQUEST_NODE_MAX_TEXT_SIZE=1048576       # 1 MB，超出被截断
HTTP_REQUEST_NODE_MAX_BINARY_SIZE=10485760    # 10 MB
HTTP_REQUEST_NODE_SSL_VERIFY=true
HTTP_REQUEST_MAX_CONNECT_TIMEOUT=10           # 用户在编辑器可设的超时上限
HTTP_REQUEST_MAX_READ_TIMEOUT=600
HTTP_REQUEST_MAX_WRITE_TIMEOUT=600
WEBHOOK_REQUEST_BODY_MAX_SIZE=10485760        # webhook 入站体积上限，超出 413
```
> ⚠️ **`HTTP_REQUEST_MAX_READ_TIMEOUT=600`（10 分钟）是 per-node 可设上限**，但**整个工作流的上限是 3600 秒**，且**blocking 模式在 Cloud 上是 100 秒**。设计长耗时节点时必须同时考虑这三层。

来源：<https://docs.dify.ai/en/cloud/use-dify/nodes/code.md>、<https://raw.githubusercontent.com/langgenius/dify/main/docker/.env.example>、<https://raw.githubusercontent.com/langgenius/dify/main/docker/docker-compose.yaml>、<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>

参考链接：
- <https://docs.dify.ai/en/cloud/use-dify/workspace/plugins.md>
- <https://docs.dify.ai/en/cloud/use-dify/workspace/tools.md>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/code.md>
- <https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/agent-strategy-plugin.md>
- <https://raw.githubusercontent.com/langgenius/dify/main/docker/.env.example>

---

## 9. 把 Dify 当"被调用的后端能力层"时的成熟做法与已知坑

### 结论

1. **🔴 头号问题：没有幂等。** 官方 CLI 文档直接写明 `--retry-on-limit` 默认关闭是因为"**a run isn't idempotent**"；**没有 `Idempotency-Key` 头**。把 `/v1/workflows/run` 放在会重试的调用链里（网关重试、SDK 重试、消息队列重投）**必然产生重复 run、重复计费、重复副作用**。社区曾正式提案加 `Idempotency-Key`（Redis 24h TTL），**被以 not planned 关闭**。【官方确认 + GitHub issue 证据】
2. **🔴 没有 fire-and-forget / 异步模式。** `blocking` 与 `streaming` **都会占住 HTTP 连接**。社区提案 `response_mode: "async"`（202 + `workflow_run_id` + 轮询 + 幂等键 + 僵尸回收器）**被以 not planned 关闭**。**长耗时任务只能靠 streaming 挂长连接。**【GitHub issue 证据】
3. **🔴 SSE 断连虽然有恢复手段，但"僵尸 running"问题被明确报告过。** 官方现在提供了 `GET /v1/workflows/run/{run_id}/events` 恢复流 + `Get Workflow Run Detail` 兜底确认；但社区提案里仍写"**the workflow run remains in 'running' status indefinitely with no mechanism to recover**"（并引用了 #26169）。**【官方确认 + issue 证据】**
4. **业务数据库与 Dify 的 run 日志天然割裂，且没有"运行完成回调"。** 1.17 的 Trigger 是**启动**工作流的（webhook / schedule / plugin 事件），**不是完成回调**；且 **trigger 入口的工作流明确不能通过 Service API / WebApp / OpenAPI / MCP 调用**。要感知完成只能：(a) 挂 SSE，(b) 轮询 `Get Workflow Run Detail` / List Workflow Logs，(c) 从 OTel / Langfuse 侧接。**【官方确认】**
5. **三层超时叠加，且必须同步调整**：`WORKFLOW_MAX_EXECUTION_TIME`（工作流引擎层，默认 3600s）+ `APP_MAX_EXECUTION_TIME`（应用队列层，默认 3600s）官方要求"**increase both together**"；另有 `WORKFLOW_MAX_EXECUTION_STEPS=500`。**blocking 模式在 Cloud 上有 100 秒的 Cloudflare 超时。**【官方确认】
6. **自托管默认没有 per-app 并发上限**（`APP_DEFAULT_ACTIVE_REQUESTS=0`、`APP_MAX_ACTIVE_REQUESTS=0` 都表示不限），而 `TENANT_ISOLATED_TASK_CONCURRENCY=1` 又限制了隔离队列的并发 —— **并发行为需要实测，不能只看文档。**【源码确认】

### 9.1 超时与执行上限（全量）

| 层级 | 变量/限制 | 默认 | 说明 |
|---|---|---|---|
| 反向代理 | `NGINX_PROXY_READ_TIMEOUT` / `NGINX_PROXY_SEND_TIMEOUT` | `3600s` | 内置 nginx 对上游的超时（默认已很大，为 SSE 服务） |
| Gunicorn | `GUNICORN_TIMEOUT` | `360` | worker 无响应则被 kill 重启；官方说设 360 是为支持**长连 SSE** |
| 应用队列 | `APP_MAX_EXECUTION_TIME` | `3600` | "Maximum time in seconds an app execution can run before it is terminated"；官方："An outer limit alongside `WORKFLOW_MAX_EXECUTION_TIME`: **both default to one hour**, with this one applied at the **app queue level** and the other at the **workflow engine level**. **Increase both together**" |
| 工作流引擎 | `WORKFLOW_MAX_EXECUTION_TIME` | `3600` | 单次 run 墙钟时间上限，超出**终止工作流** |
| 工作流引擎 | `WORKFLOW_MAX_EXECUTION_STEPS` | `500` | 节点执行次数上限，超出**终止工作流** |
| 工作流引擎 | `WORKFLOW_CALL_MAX_DEPTH` | `5` | 嵌套工作流调用深度 |
| HTTP 节点 | `HTTP_REQUEST_MAX_READ_TIMEOUT` | `600` | 用户可设的 per-node 上限 |
| Code 节点 | `SANDBOX_WORKER_TIMEOUT` | `15` | 沙箱执行超时 |
| Agent（新） | `DIFY_AGENT_RUN_TIMEOUT_SECONDS` | `3600` | 单个 agent run 的模型与工具工作墙钟上限；超出以 `agent_run_limit_exceeded` 失败 |
| Agent（新） | `AGENT_BACKEND_STREAM_READ_TIMEOUT_SECONDS` | `30` | 到 agent backend 的**单条 SSE 连接**读超时 |
| Agent（新） | `AGENT_BACKEND_STREAM_MAX_RECONNECTS` | `3` | 断流后 API 重连次数，用尽即失败 |
| HITL | `HUMAN_INPUT_GLOBAL_TIMEOUT_SECONDS` | `7 天` | run 可暂停等待人工输入的最长时间 |
| HITL | Human Input 节点 Timeout | **默认 3 天** | 表单过期时间；过期走 timeout 分支；**无 timeout 分支则工作流结束** |
| 插件 | `PLUGIN_MAX_EXECUTION_TIMEOUT` | `600` | 插件执行超时上限 |
| Cloud | blocking 模式 | **100 s** | OpenAPI 原文："Cloudflare timeout is `100 s`" |
| 前端 UI | `TEXT_GENERATION_TIMEOUT_MS` | `60000` | 流式文本 UI 停顿超时（前端渲染层，非后端） |

**⚠️ 三层超时的"最短板"陷阱**：官方特别强调，若跑 Agent，**`APP_MAX_EXECUTION_TIME` 与 `WORKFLOW_MAX_EXECUTION_TIME` 不能低于 `DIFY_AGENT_RUN_TIMEOUT_SECONDS`，否则 agent run 会被提前砍断**。
来源：<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>

### 9.2 并发与吞吐

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_DEFAULT_ACTIVE_REQUESTS` | **0（不限）** | app 未自定义并发上限时的默认值；有效上限 = min(此值, `APP_MAX_ACTIVE_REQUESTS`) |
| `APP_MAX_ACTIVE_REQUESTS` | **0（不限）** | 每 app 并发的全局天花板；**会覆盖超过它的 per-app 设置** |
| `SERVER_WORKER_AMOUNT` | `1` | Gunicorn worker 数（gevent 下通常够用；sync worker 用 `2×CPU+1`） |
| `SERVER_WORKER_CONNECTIONS` | `10` | 每 worker 最大并发连接（仅 async worker） |
| `CELERY_WORKER_AMOUNT` | `4` | Celery worker 进程数（未启用 autoscale 时生效） |
| `CELERY_AUTO_SCALE` | `false` | 开启动态扩缩容（`CELERY_MIN_WORKERS` / `CELERY_MAX_WORKERS`） |
| `GRAPH_ENGINE_MIN_WORKERS` / `MAX_WORKERS` | `3` / `10` | GraphEngine 实例工作池上下限 |
| `GRAPH_ENGINE_SCALE_UP_THRESHOLD` | `0` | **只要有任务等待就扩容** |
| `TENANT_ISOLATED_TASK_CONCURRENCY` | **1** | **隔离队列每 tenant 允许并发投递的任务数 = 1** |
| `QUEUE_MONITOR_THRESHOLD` | `200` | 队列积压告警阈值（**只告警，不拒绝**） |
| `QUEUE_MONITOR_ALERT_EMAILS` | （空） | 告警邮箱 |
| `ASYNC_WORKFLOW_SCHEDULER_GRANULARITY` | `120` | 异步工作流调度粒度（秒）；官方注释解释这是为了避免少数用户的长耗时任务阻塞队列 |

**⚠️ `TENANT_ISOLATED_TASK_CONCURRENCY=1` 值得高度警惕**：如果你们的用量走隔离队列，单租户同时只能有 1 个任务在投递 —— 这会成为吞吐瓶颈。**哪些任务走隔离队列我未查证 → 【未查证】。**
**⚠️ `too_many_requests` 在自托管下的触发点未查证**（因为默认两个并发上限都是 0）。**必须压测确认。**

### 9.3 幂等性（重点）

**官方证据**
- `difyctl` 官方文档，`run app` 的 `--retry-on-limit` flag 说明原文：
  > "On a 429 rate limit, wait and retry the run instead of failing with exit `7`. **Off by default, since a run isn't idempotent.**"
  来源：<https://docs.dify.ai/en/cli/reference/apps.md>
- `task_id`（控制用）与 `workflow_run_id`（持久记录）**都是服务端生成的 UUID**，OpenAPI 中：
  - `task_id`："Only valid during execution."
  - `workflow_run_id`："Persistent identifier for this workflow run record."
  **请求体中没有任何客户端可指定 run id 或幂等键的字段。**【官方确认，来自 OpenAPI schema】

**社区提案（被拒绝）**
- issue **#35327** "feat(api): add async response_mode for workflow execution"（2026-04-16 创建，**2026-05-06 以 not planned 关闭**）。提案内容包含：
  > "- Returns `202 Accepted` immediately with a `workflow_run_id`
  > - Poll status via existing `GET /v1/workflows/run/{workflow_run_id}`
  > - **Idempotency key support via `Idempotency-Key` header (Redis, 24h TTL)**
  > - Per-tenant concurrency limit (`ASYNC_WORKFLOW_MAX_CONCURRENT`, default 50)
  > - **Zombie reaper Celery Beat task to force-fail stuck `SCHEDULED` (>5min) and `RUNNING` (>30min) rows**"
  来源：<https://github.com/langgenius/dify/issues/35327>

**推论性证据（仓库自己的测试路线图）**
- issue **#42147** "[Refactor/Chore] Roadmap: strengthen backend tests with business contracts and invariants" 中写道：
  > "Exercise repeated commands and task redelivery. Distinguish idempotent business effects from delivery counts; **do not impose exactly-once delivery unless it is a documented guarantee.**"
  **说明"重复投递会导致重复副作用"在仓库内部是被承认的关切。**
  来源：<https://github.com/langgenius/dify/issues/42147>

**对本方案的含义（事实层面）**：调用 Dify 时必须**在你们的网关/业务层自己做幂等**（业务侧唯一键 → 去重 → 只在首次真正调用 Dify，并把 `workflow_run_id` 落库做对账）。**Dify 不会帮你做这件事。**

### 9.4 流式 / SSE 断流的坑

**曾经的严重 bug（已修）**
- issue **#28640** "Workflow log lost when sse connection close"（v1.10.0，2025-11-25 创建，**2025-11-27 关闭**）。原文：
  > "run a workflow via api request, use streaming mode; when request start response and not finish, refresh the page or manual abort the connect(in browser); **workflow whole process will run normally(we can add log in code to observed), but workflow `/logs` nothing, the log of this request is lost**"
  **即：断连后工作流照跑完，但 run 日志丢失 → 你们永远不知道结果。** 现在官方提供了 `GET /workflows/run/{run_id}/events` 恢复端点与 `Get Workflow Run Detail`。
  来源：<https://github.com/langgenius/dify/issues/28640>

**仍然存在的问题**
- issue **#35327** 的动机段落原文：
  > "**Stuck 'running' workflows** — when SSE connections drop mid-execution (network timeouts, load balancer limits), **the workflow run remains in 'running' status indefinitely with no mechanism to recover.** Related: #26169."
  来源：<https://github.com/langgenius/dify/issues/35327>

**官方给出的正确姿势（务必照做）**
- "**Save `workflow_run_id` as soon as it arrives**: if the connection drops mid-run, it's the only handle you have for reconnecting or checking the outcome."
- 重连用 `Stream Workflow Events` + `workflow_run_id` + **同一个 `user`**（不匹配 → 404）。
- `include_state_snapshot=true` 重放已执行节点状态；`continue_on_pause=true` 跨多个 HITL 暂停保持单流。
- "**After reconnecting to a still-running workflow, confirm completion with `Get Workflow Run Detail` rather than relying on the reconnected stream's final event alone.**"
- **keep-alive**：流以 `ping` 开头，之后约每 10 秒一个；客户端 read timeout 要**明显大于 10 秒**。
- **不要靠 HTTP 状态码判断失败**：流一旦打开状态码就是 200，错误以 `error` 事件送达并结束流。**节点失败则以 `status: "failed"` 的 `node_finished` + `workflow_finished` 事件体现**（两种失败形态都要处理）。
- **Cloudflare 100s** 是 blocking 模式在 Cloud 上的上限。

来源：<https://docs.dify.ai/en/api-reference/guides/streaming.md>、<https://docs.dify.ai/en/api-reference/guides/workflow.md>

### 9.5 日志与业务数据库割裂、完成通知

**Dify 自己的 run 相关表/数据（从官方归档 CSV 构成可反推）**：
`workflow_runs`、`workflow_app_logs`、`workflow_node_executions`、`workflow_node_execution_offload`（超大输入输出单独存）、`workflow_pauses`、`workflow_pause_reasons`、`workflow_trigger_logs`。
**这些数据在 Dify 自己的库里，与你们的业务库天然割裂。**【官方确认】

**有没有"运行完成回调/Webhook"？**
- **1.17 的 Trigger 节点是"入口"不是"出口"**：`webhook-trigger` / `schedule-trigger` / `plugin-trigger` 用于**启动**工作流。
- **且 trigger 入口的工作流明确被禁止通过 Service API / WebApp / OpenAPI / MCP 调用**：
  > 403 `trigger_workflow_service_mode_unavailable`: "This workflow uses a trigger entry and cannot be invoked through Web App, Service API, OpenAPI, or MCP."
  （1.17.1 起在创建 run 或 log 之前就拒绝，#41519）
- **因此：Dify 没有文档化的"run 完成时回调你的系统"能力。** 要感知完成只有三条路：SSE、轮询（`Get Workflow Run Detail` / `List Workflow Logs` / 归档 CSV）、外部追踪系统（OTel / Langfuse 里看 trace）。**【官方确认】**
- 【推断】若一定要"完成即通知"，现实做法是在工作流**末尾**加一个 HTTP Request 节点或 Tool 回调你们的服务 —— 但注意：(a) 走 SSRF 代理，(b) 若 run 因超时/崩溃未能到达该节点则不会回调，(c) 这属于业务层自建，不是 Dify 的能力。

**日志保留与清理（自托管需自己管）**
```env
WORKFLOW_LOG_CLEANUP_ENABLED=false        # 默认关闭 → 日志无限增长
WORKFLOW_LOG_RETENTION_DAYS=30
WORKFLOW_LOG_CLEANUP_BATCH_SIZE=100
WORKFLOW_LOG_CLEANUP_SPECIFIC_WORKFLOW_IDS=
WORKFLOW_NODE_EXECUTION_STORAGE=rdbms     # 或 hybrid
```
**官方 Logs 文档还提醒**："Deleting conversations or cleaning up old logs **does not erase the files uploaded in them**."（**清理日志不会删除其中上传的文件** —— 数据治理与合规风险点）

### 9.6 错误码相关的坑（再强调）

- **流内错误不改 HTTP 状态**（始终 200）—— 靠状态码判断失败的集成会静默丢错。
- **403 混合了"权限"和"套餐限制"**：官方明确 "**don't build a switch on `code` alone for 403s**"，要靠 `message` 区分。
- **413 `file_too_large` 的 `message` 当前返回空字符串**（官方标注为 "a known backend quirk"），只能靠 code + status。
- **`provider_*` 系列 400 是配置问题，重试无用**（官方："retrying won't help"）。
- `invalid_param` 会用于"缺少 `user`"以及"**工作流未发布**"这两种完全不同的情况。

来源：<https://docs.dify.ai/en/api-reference/guides/errors.md>、<https://docs.dify.ai/en/api-reference/files/upload-file.md>

### 9.7 其他反复出现的社区/官方 issue 与升级痛点

**升级类（影响最大，且官方主动在 release note 顶部警告）**
1. **Weaviate 跨 12 个 minor 的强制分阶段升级**（1.27.0 → 1.39.2），官方 WARNING 原文："skipping minors **is unsupported**. Pulling and restarting can **silently and permanently break vector search**."（1.17.1）
2. **模型类型遗留值迁移**：1.15 之前的 `text-generation` / `embeddings` / `reranking` 旧值会让模型相关 API 请求失败且模型无法加载；1.16.1/1.17.0 需手动 `flask data-migrate legacy-model-types`，1.17.1 起并入 `flask db upgrade`。
3. **知识库抽取静默错误**：1.17.1 修复一批不报错的抽取 bug（CSV 前导零变浮点、空单元格变 `nan`、Notion 表格丢列/截断、`.xls` 双引号破坏行、内置网页抓取返回 Python dict 字面量、URL 加载 PDF 崩溃等），官方："**Re-import affected documents after upgrading; indexed text is not retroactively repaired.**"
4. **MySQL 特有 bug**：文档创建/更新在 MySQL 默认 `REPEATABLE READ` 下会报 "One or more files not found."（1.17.1 修复 #41840，"PostgreSQL was never affected"）→ **强烈倾向 PostgreSQL。**

**稳定性类**
5. **协作 WebSocket 与 Redis 的耦合脆弱**：issue **#39423**（1.16.0）`REDIS_SOCKET_TIMEOUT`（默认 5.0）被传给 Socket.IO 的**阻塞式** pub/sub 监听，导致每 5 秒超时重连、事件永不到达 —— "workflow collaboration does not function"，且日志只打出通用的 retry 行（真正的异常被 Dify 的 log formatter 吞掉，这是另一个独立缺陷）。**已修，但这说明 Redis 参数与长连服务的交互容易出问题。**
   来源：<https://github.com/langgenius/dify/issues/39423>
6. **大工作流图触发 Engine.IO 1 MiB 上限**，导致编辑器 WebSocket 被拒并无限重连（#41347）；现在 `WEBSOCKET_MAX_HTTP_BUFFER_SIZE` 默认 10 MiB 可调。
7. **知识库检索在负载下超时**：issue **#29599** "rag workflow and hiting-test timeout"（1.11.1）。
   来源：<https://github.com/langgenius/dify/issues/29599>
8. **HTTP Request / 自定义 Tool 调用超时**：issue **#4545**（0.6.8）"Node <node_name> run failed: Failed to invoke tool: timed out."（约 1 分钟后超时）。此后加入了 `HTTP_REQUEST_MAX_*_TIMEOUT` 与 `API_TOOL_DEFAULT_*_TIMEOUT`。
   来源：<https://github.com/langgenius/dify/issues/4545>
9. **HTTP 节点连不上内网服务**：issue **#12834**（0.15.0）—— `curl` 在服务器上通，但 Dify 的 HTTP 模块总是超时。**这正是 §2.4 的 SSRF 代理坑的典型表现。**
   来源：<https://github.com/langgenius/dify/issues/12834>
10. **Code 节点随机超时**：issue **#14225**（0.15.3, sandbox 0.2.10）—— 极简代码片段随机超时，标签 `cant-reproduce`。结合 `SANDBOX_WORKER_TIMEOUT=15` 的紧限制，**建议 Code 节点保持轻量、把重活交给外部服务。**
    来源：<https://github.com/langgenius/dify/issues/14225>

**架构/契约类**
11. **GET 端点有副作用**：issue **#26342**（由 Dify 成员 laipz8200 提交，已修）—— `GET /console/api/logout` 会登出、`GET .../server/refresh` 每次轮换 server code、`GET .../more-like-this` 每次触发新生成、`GET .../account/delete/verify` 每次发删号邮件。**说明 Console 侧的语义一致性历史上并不严格。**
    来源：<https://github.com/langgenius/dify/issues/26342>
12. **Console API 不是公开稳定 API**：官方 API Reference 只覆盖 `/v1`（Service API，app key 与 knowledge key）与 `/openapi/v1/*`（供 difyctl 等程序化客户端）。**Web 控制台用的 `/console/api/*` 端点没有公开文档、没有版本承诺。**【推断 + 官方文档范围证据】
13. **文件对象跨工作流/工具调用存在序列化边界**：issue **#35032** "Object of type File is not JSON serializable"（把 workflow 发布成 tool 后，files 类型入参在 Chatflow 中报错）。
    来源：<https://github.com/langgenius/dify/issues/35032>
14. **"用 API 做应用配置的 IaC"仍是未完成的提案**：issue **#42075**（2026-09-09，**open**）。
    来源：<https://github.com/langgenius/dify/issues/42075>

**关于多租户 License 约束对 SaaS 设计的影响**：见 §1。**这是"能不能这么卖"的问题，不是"能不能这么跑"的问题** —— 技术上多 workspace 是支持的（`tenant_id` 贯穿全库，还有 RBAC/ACL 开发），**但许可证禁止未经书面授权地对外运营多租户环境。**

参考链接：
- <https://docs.dify.ai/en/api-reference/guides/errors.md>
- <https://docs.dify.ai/en/api-reference/guides/streaming.md>
- <https://docs.dify.ai/en/cli/reference/apps.md>
- <https://github.com/langgenius/dify/issues/35327>
- <https://github.com/langgenius/dify/issues/28640>
- <https://github.com/langgenius/dify/issues/39423>
- <https://github.com/langgenius/dify/issues/29599>
- <https://github.com/langgenius/dify/issues/4545>
- <https://github.com/langgenius/dify/issues/12834>
- <https://github.com/langgenius/dify/issues/26342>
- <https://github.com/langgenius/dify/issues/35032>
- <https://github.com/langgenius/dify/issues/42075>

---

## 10. 与 LangGraph 分工的主流模式

### 结论

1. **🔴 首先纠正一个前提：Dify 1.17 已原生支持 HITL、有界循环、迭代、并行、子流程与"工作流即工具"。** 所以"HITL / 循环 / 复杂控制流必须放 LangGraph"这个论据，**在 2026 年的 Dify 上已经弱化了很多**。真正还需要 LangGraph 的是：**无界/长时状态机、跨会话持久状态、精确重放/时间旅行调试、以及纯粹的确定性业务逻辑。**【官方确认】
2. **Dify 官方提供的"接入自带编排"缝隙有 5 个**：(1) **Agent Strategy 插件**（最贴近"自带推理循环"）、(2) **Swagger/OpenAPI 工具导入**、(3) **MCP client（仅 HTTP transport）**、(4) **HTTP Request 节点**、(5) **Custom Endpoint / API Extension**。反向还有 **(6) 把 Dify app 发布成 MCP Server** 供 LangGraph 调用。**【官方确认】**
3. **⚠️ 我未找到任何 Dify 官方文档把"Dify + LangGraph 混合架构"描述为受支持的参考架构。** 官方没有任何 LangGraph 集成页，LangGraph 文档侧也没有针对 Dify 的指引 → **【未查证 / 文档未说明】**。所以这一节的"主流模式"结论**主要来自第三方内容与推断**，证据强度低于前面各节。
4. **有一个很值得注意的第三方实现方向：把 Dify DSL 当作可编译的产物，而不是运行时依赖。** 项目 `wanmol/goal-flow` 自我描述为"LangGraph 上的生产级框架，**transpile Dify DSL to runnable code**, swap wire protocols (**Dify**/OpenAI)"。**【第三方，质量未评估】**
5. **社区里流传的分工口径**（第三方）大致是："**核心工作流用 LangGraph 写，业务方临时要的小工具用 Dify 让运营自己搭**"。这是我检索到的明确表述，但**属于社区观点，不是官方指引**。**【社区观点】**
6. **最需要警惕的反模式是"双重编排 / 两个事实来源"**——状态机一半在 Dify 画布、一半在 LangGraph，导致 prompt 与配置分散在两处，而 DSL 导出只覆盖 Dify 的那一半，CI 无法整体回滚。**这是【推断】，但由 §6 的 DSL 覆盖面事实直接推出。**

### 10.1 Dify 官方的"接入自有编排"缝隙（逐条带证据）

**(1) Agent Strategy 插件 —— 最贴近"自带编排逻辑"的官方缝隙**
官方定位："An **Agent Strategy Plugin** gives an LLM the reasoning and decision-making logic to choose tools, call them, and handle their results, so it can solve problems autonomously."
- 插件类型枚举里明确有 `Agent Strategy: Implement your own logics here, just by focusing on Agent itself`。
- 可用 `session.model.llm.invoke()` 与 `session.tool.invoke()`，并可反向调用 Dify 内的模型与工具。
- 官方给出完整 Function Calling 参考实现：<https://github.com/langgenius/dify-official-plugins/blob/main/agent-strategies/cot_agent/strategies/function_calling.py>
- Agent 节点侧：内置 **Function Calling** 与 **ReAct（Thought → Action → Observation）** 两种策略，"Install additional strategies from **Marketplace → Agent Strategies**"。
来源：<https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/agent-strategy-plugin.md>、<https://docs.dify.ai/en/cloud/use-dify/nodes/agent.md>
> 【推断】理论上可以写一个"Agent Strategy 插件"把请求转给 LangGraph 服务并流式回传结果 —— 但**官方没有任何文档或示例这样做**，且插件的执行超时上限是 `PLUGIN_MAX_EXECUTION_TIMEOUT=600` 秒。**这属于未经验证的集成方式。**

**(2)(3)(4) 把 LangGraph 暴露成 Dify 能调的东西**
| 缝隙 | 官方支持 | 关键约束（对本方案） |
|---|---|---|
| **HTTP Request 节点** | ✅ | 走 SSRF 代理，**必须配 `SSRF_PROXY_ALLOW_PRIVATE_IPS` 才能访问内网**；响应上限 **text 1 MB / binary 10 MB**（text **超限会被截断**，不报错）；per-node 超时上限 **600 s**；整个工作流上限 3600 s |
| **Swagger/OpenAPI 工具导入** | ✅ | 支持粘贴 schema / URL 导入 / 示例；工具调用默认 `API_TOOL_DEFAULT_CONNECT_TIMEOUT=10`、`API_TOOL_DEFAULT_READ_TIMEOUT=60` |
| **MCP client** | ✅ | **仅 HTTP transport**；支持 DCR 或手填 Client ID/Secret；支持 custom headers 与 **`{{request.headers.X-Custom-Auth}}` 动态透传调用方凭据**；server identifier 变更会打断已有 app；可调 request timeout 与 SSE read timeout |
| **Custom Endpoint / API Extension** | ✅ | 官方定位是"给 app 加内容审核或外部数据工具"；文档有 `moderation-api-extension`、`external-data-tool-api-extension`、`cloudflare-worker` 页 |
来源：<https://docs.dify.ai/en/cloud/use-dify/workspace/tools.md>、<https://docs.dify.ai/en/self-host/deploy/configuration/environments.md>

**(5) 把 Dify app 发布成 MCP Server（反向：让 LangGraph 调 Dify）**
- 在 app 的 **Access Point** tab 找到 **MCP Server** 卡片；**默认关闭**，打开后 Dify 生成唯一 MCP Server 地址。
- **⚠️ "Your MCP Server URL contains authentication credentials, so treat it like an API key."** 疑似泄露可用 regenerate 按钮重生成，旧 URL 立即失效。
- 官方给出的实践注意点：
  - **Descriptiveness**："When designing descriptions for your tool and its input parameters, think about how an AI would interpret them. Clear, specific descriptions lead to better invocations."
  - **Latency**："If your app typically takes 30 seconds to process, that latency will be felt in the client application. Consider adding progress indicators or **breaking complex workflows into smaller, faster operations**." ← **官方自己承认 Dify app 作为被调工具的延迟问题**
来源：<https://docs.dify.ai/en/cloud/use-dify/publish/publish-mcp.md>

**(6) difyctl 作为 LangGraph 节点调用 Dify 的方式**
- 官方明确把"让 AI agent 把 Dify app 当工具发现与调用"作为 difyctl 的用例之一：`/en/cli/integrate-agents/overview`、`/en/cli/integrate-agents/install-the-difyctl-skill`、`/en/cli/integrate-agents/error-handling-and-retries-for-agents`、`/en/cli/integrate-agents/auth-for-agent-deployments`。
- `difyctl run app ... --inputs '<json>' -o json` 适合脚本化；`difyctl resume app <app-id> <form-token> --workflow-run-id <id> --action <id>` **让 LangGraph 侧也能驱动 Dify 的 HITL。**
- 退出码语义适合编排：`0` 成功（含暂停）/`1` 网络或服务错误/`2` 用法错误/`4` 认证失败/`7` 限流。**但官方警告："a paused run and a completed run both exit 0, so don't branch on the exit code."**
来源：<https://docs.dify.ai/en/cli/overview.md>、<https://docs.dify.ai/en/cli/reference/apps.md>

### 10.2 Dify 原生能力对齐表（判断"是否需要 LangGraph"的事实基础）

| 能力 | Dify 1.17.x 原生 | 上限/约束 | 是否还需要 LangGraph |
|---|---|---|---|
| **HITL / 人工审核** | ✅ **Human Input 节点**，表单 + 动作按钮 + 超时分支；**API 全流程可驱动** | 表单超时默认 3 天；全局暂停上限 7 天；**表单走 Email 时 `form_token` 为 `null`，无法 API 驱动**；**Agent app 形态下不产生 `human_input_required`（#41996）** | 【推断】表单式审批可以留在 Dify；但 **"Agent 形态 + 结构化确认"目前有缺口** |
| **循环** | ✅ Loop 节点 | `LOOP_NODE_MAX_COUNT=100`（**有界**） | 无界/超 100 轮 → LangGraph |
| **迭代（遍历集合）** | ✅ Iteration 节点 + List Operator | `MAX_ITERATIONS_NUM=99`（agent 迭代） | 大规模批处理 → 视情况 |
| **并行分支** | ✅ 画布扇出（隐式） | `MAX_PARALLEL_LIMIT=10`；**并行分支之间不能互读变量** | 需要分支间共享可变状态 → LangGraph/外部服务 |
| **条件分支** | ✅ If-Else + Question Classifier | — | 不需要 |
| **子流程 / 复用** | ✅ Workflow-as-Tool（**Chatflow 不能当 tool**） | `WORKFLOW_CALL_MAX_DEPTH=5` | 不需要 |
| **图规模** | — | 单执行路径 ≤ **50 节点**（`MAX_TREE_DEPTH`，超长**无法发布**）；单 run ≤ **500 步** | 大型图 → LangGraph |
| **跨会话持久状态** | ⚠️ 仅 Chatflow 有会话态；Workflow **"no conversation state between calls"**；Agent V2 用 Redis 存 run 状态（`DIFY_AGENT_RUN_RETENTION_SECONDS=7200` 2 小时） | run 事件流默认保留 2 小时（`DIFY_AGENT_RUN_EVENT_STREAM_MAX_LENGTH=5000`，trim 后无法重放） | 长时/跨会话状态 → LangGraph（durable execution / checkpointing） |
| **确定性业务逻辑** | ⚠️ Code 节点（Python/JS）可写，但沙箱 15 s 超时 + 输出结构限制（数组对象 ≤30 元素、嵌套 ≤5 层）+ **预装库清单未穷举** | 见 §8.3 | 复杂确定性逻辑 → 外部服务/LangGraph 更合适 |
| **RAG** | ✅ 内置 Knowledge（向量/全文/混合 + rerank + metadata 过滤）+ External Knowledge API + Knowledge Pipeline | 见 §5 | 不需要；**Dify 的 RAG 是它最强项** |
| **Prompt 可视化编辑与运营** | ✅ Prompt IDE、版本控制（Draft/Latest/Previous）、Annotations、Logs、Dashboard | **配置无公开写 API**；DSL 是唯一程序化路径（写 Draft，需手动 Publish） | 需要 API 化 prompt 版本管理 → 用 Langfuse 或代码仓库 |

### 10.3 第三方观点的分工口径（**证据强度低，仅作参考**）

**对比型内容（厂商内容营销页面，权威性低）**
`respan.ai` 的 "Dify vs LangGraph (2026)"（更新于 2026-04-29）给出的定位：
- Dify "Best For: Technical teams who want a **visual builder** for AI applications with the option to self-host"；Pros: "Open-source with strong community / Visual workflow builder accelerates development / Production-ready platform"。
- LangGraph "Best For: Production engineering teams building reliable, **multi-step AI agents at scale with full observability**"；Pros: "**Most production-ready open-source agent framework in 2026** / Full enterprise certifications + Fortune 500 references / **Durable execution survives crashes and restarts** / **Time-travel debugging is unique in the space** / Tight LangChain ecosystem integration"；Cons: "Steeper learning curve / **Paid LangSmith / LangGraph Platform for managed observability** / Best with LangChain ecosystem"。
- 选择建议：选 Dify 用于"无深度编码的 chatbot、RAG 开发、工作流原型、多模型应用设计"；选 LangGraph 用于"**Fortune 500 规模的生产多步 agent**、**跨小时/天持久状态的长时工作流**、**HITL 审批流**、**可回滚的分支决策树**、**跨工具编排 + 完整可观测性**"。
> ⚠️ **这是厂商内容（Respan 卖 routing/tracing），双方都打 10/10 分，明显是 SEO/获客页面。引用时请降权。** 其中"LangGraph 有 durable execution / checkpointing / time-travel debugging / interrupts"是**来自官方 LangGraph 定位的常见说法**，但**我这次没有抓 LangGraph 官方文档核实 → 【未查证】。**
来源：<https://www.respan.ai/market-map/compare/dify-vs-langgraph>

**"把 Dify 当产物而非运行时"的方向（第三方项目）**
- `wanmol/goal-flow`：GitHub 自述 "**Graph-Orchestrated Agent Loop** — a production-grade framework on LangGraph. Combine workflow graphs and agent loops, **transpile Dify DSL to runnable code**, swap wire protocols (**Dify**/OpenAI)."
  → 【推断】**这是一个很有启发性的模式：把 Dify 的 DSL 当成"可视化设计稿/产物"，编译成 LangGraph 代码运行，从而避免运行时双编排。** 但**该项目质量、维护状态、可靠性我未评估 → 【未查证】。**
  来源：<https://github.com/wanmol/goal-flow>

**中文社区的口径（社区观点，非官方）**
- SegmentFault 一篇 AI 工作流文章中写道："实际项目中，这些工具经常组合使用——比如**核心工作流用 LangGraph 写，但业务方临时要的小工具就用 Dify 让运营自己搭**。"
  来源：<https://segmentfault.com/a/1190000047746298>
- 另有技术对比文章《Dify 与 LangGraph 图执行引擎原理对比：从定义层到运行时的架构拆解》（jishuzhan.net），属第三方技术分析。
  来源：<https://jishuzhan.net/article/2046417492596293634>
- 腾讯云开发者社区《把 Dify 工作流接入业务系统：先在网关层补上这些生产化护栏》（标题本身即建议"在网关层补生产化护栏"）。
  来源：<https://cloud.tencent.com.cn/developer/article/2719465>
- 另外检索到 CSDN/腾讯云上若干"Dify + LangGraph 企业级多智能体"类文章（如 `https://cloud.tencent.com.cn/developer/article/2695234`、`https://wenku.csdn.net/doc/9c0u6f0d95d8`）。**内容质量未评估，不作为技术依据。**

### 10.4 反模式清单（**多数为【推断】，由前文确认事实推出**）

| 反模式 | 为什么（依据） | 证据强度 |
|---|---|---|
| **双重编排 / 两个事实来源**：状态机一半在 Dify 画布、一半在 LangGraph | Dify 的配置只能用 DSL 导出，而 DSL 只覆盖 Dify 那一半 → CI 无法整体回滚、prompt 与路由规则分散在两处、排障要跨两个系统 | 【推断】，基于 §6 DSL 覆盖面事实 |
| **把 Dify 的 Console API（`/console/api/*`）当集成面** | 官方 API Reference 只覆盖 `/v1` 与 `/openapi/v1/*`；Console 端点无文档、无版本承诺，历史上还有 GET 有副作用的问题（#26342） | 【推断】+ GitHub issue 证据 |
| **把长耗时/需要 fire-and-forget 的任务放 Dify** | 没有 async 模式（#35327 被 not planned）；blocking 在 Cloud 上限 100 s；streaming 必须挂长连接；SSE 断连会产生僵尸 running（#35327 动机段） | 【GitHub issue 证据】 |
| **把 `/v1/workflows/run` 放在会自动重试的调用链里** | 官方明说"a run isn't idempotent"，且无 Idempotency-Key；重试 → 重复 run + 重复副作用 + 重复计费 | 【官方确认】 |
| **在 Dify 里建无界/超长 agent 循环或超大图** | Loop ≤100、agent 迭代 ≤99、并行 ≤10、单路径 ≤50 节点（**超长无法发布**）、单 run ≤500 步 | 【官方确认，环境变量】 |
| **把业务系统 of record 放进 Dify 的库** | Dify 的 run 日志在自己库里（workflow_runs / node_executions / pauses…）；没有完成回调；`WORKFLOW_LOG_CLEANUP_ENABLED=false` 默认不清会膨胀，开了又会按保留期删；Cloud 上 3 个月后转月度 CSV | 【推断】+ 官方确认 |
| **依赖 Dify 做 prompt 的版本化与回滚** | 没有公开的配置写 API；版本控制的 publish 只有 UI；restore/export 指定版本是 Cloud 付费套餐特性 | 【官方确认】 |
| **在 Dify 的 Code 节点里跑重活** | 沙箱 15 s 超时；输出结构限制（对象数组 ≤30、嵌套 ≤5）；预装库清单不透明；曾有随机超时 issue | 【官方确认 + 源码确认】 |
| **多租户 SaaS 直接对外售卖 Dify** | LICENSE 明确禁止未经书面授权运营多租户环境 | 【官方确认】 |
| **不做三层超时的对齐** | `APP_MAX_EXECUTION_TIME`、`WORKFLOW_MAX_EXECUTION_TIME`、`DIFY_AGENT_RUN_TIMEOUT_SECONDS` 必须一起调，否则 agent run 被提前砍断 | 【官方确认】 |
| **在 Dify 与 LangGraph 之间来回"乒乓"调用**（Dify→LangGraph→Dify→LangGraph…） | 每次跨越都增加一次网络跳、一次序列化边界（#35032 证明文件对象跨工具调用会炸）、一次超时叠加；且 `WORKFLOW_CALL_MAX_DEPTH=5` | 【推断】+ issue 证据 |

### 10.5 我没能查证的关键问题（请主 Agent 注意）

1. **没有官方 Dify 文档把 "Dify + LangGraph 混合架构" 列为推荐模式。** → **文档未说明。**
2. **没有官方 Dify ↔ LangGraph 集成示例或插件。** → **未查证到。**
3. **LangGraph 侧是否有针对"Dify 作为工具/前端的集成指引"** → 我这次**没有抓取 LangGraph 官方文档**，因此**未查证**。（LangGraph Platform / LangGraph Server 是否提供 OpenAI 兼容或标准 REST 端点、能否被 Dify 直接调用，**未查证**。）
4. **是否存在"用 Agent Strategy 插件把请求转发给 LangGraph"的成功实践** → **未查证**；理论上可行（插件可反向调用 Dify 的模型与工具，且能流式产出），但官方无任何示例。
5. **上文第三方中文/厂商文章的质量与准确性未逐一核实**，引用时请降权。

参考链接：
- <https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/agent-strategy-plugin.md>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/agent.md>
- <https://docs.dify.ai/en/cloud/use-dify/workspace/tools.md>
- <https://docs.dify.ai/en/cloud/use-dify/publish/publish-mcp.md>
- <https://docs.dify.ai/en/cli/overview.md>
- <https://docs.dify.ai/en/cli/reference/apps.md>
- <https://docs.dify.ai/en/cloud/use-dify/build/version-control.md>
- <https://www.respan.ai/market-map/compare/dify-vs-langgraph>（厂商内容，低权威）
- <https://github.com/wanmol/goal-flow>（第三方项目）
- <https://segmentfault.com/a/1190000047746298>（社区观点）

---

## 附录 A：参考链接总清单

### 官方文档（docs.dify.ai，均可加 `.md` 取纯文本）
**概览 / 版本 / 部署**
- <https://docs.dify.ai/en/cloud/use-dify/getting-started/introduction>
- <https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose>
- <https://docs.dify.ai/en/self-host/deploy/configuration/environments>
- <https://docs.dify.ai/en/self-host/deploy/troubleshooting/weaviate-server-migration-path>
- <https://docs.dify.ai/sitemap.xml>

**编排 / 节点**
- <https://docs.dify.ai/en/cloud/use-dify/build/workflow-chatflow>
- <https://docs.dify.ai/en/cloud/use-dify/build/orchestrate-node>
- <https://docs.dify.ai/en/cloud/use-dify/build/version-control>
- <https://docs.dify.ai/en/cloud/use-dify/build/predefined-error-handling-logic>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/user-input>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/human-input>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/code>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/knowledge-retrieval>
- <https://docs.dify.ai/en/cloud/use-dify/nodes/agent>

**API 参考**
- <https://docs.dify.ai/en/api-reference/guides/get-started>
- <https://docs.dify.ai/en/api-reference/guides/workflow>
- <https://docs.dify.ai/en/api-reference/guides/streaming>
- <https://docs.dify.ai/en/api-reference/guides/errors>
- <https://docs.dify.ai/en/api-reference/guides/human-input-flow>
- <https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow>
- <https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow-by-id>
- <https://docs.dify.ai/en/api-reference/files/upload-file>
- <https://docs.dify.ai/en/api-reference/openapi_service.json>

**知识库**
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/external-knowledge-api>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/connect-external-knowledge-base>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/setting-indexing-methods>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/metadata>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/integrate-knowledge-within-application>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/test-retrieval>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/knowledge-request-rate-limit>
- <https://docs.dify.ai/en/cloud/use-dify/knowledge/knowledge-pipeline/readme>

**可观测性**
- <https://docs.dify.ai/en/cloud/use-dify/monitor/logs>
- <https://docs.dify.ai/en/cloud/use-dify/monitor/integrations/integrate-langfuse>
- <https://docs.dify.ai/en/cloud/use-dify/monitor/integrations/integrate-phoenix>
- （同目录还有 integrate-langsmith / integrate-arize / integrate-opik / integrate-weave / integrate-aliyun）

**插件 / 工具 / CLI / 发布**
- <https://docs.dify.ai/en/cloud/use-dify/workspace/plugins>
- <https://docs.dify.ai/en/cloud/use-dify/workspace/tools>
- <https://docs.dify.ai/en/cloud/use-dify/workspace/model-providers>
- <https://docs.dify.ai/en/cloud/use-dify/publish/publish-mcp>
- <https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/agent-strategy-plugin>
- <https://docs.dify.ai/en/cli/overview>
- <https://docs.dify.ai/en/cli/reference/apps>

### 官方仓库
- <https://github.com/langgenius/dify/releases>
- <https://api.github.com/repos/langgenius/dify/releases?per_page=6>
- <https://github.com/langgenius/dify/blob/main/LICENSE>
- <https://raw.githubusercontent.com/langgenius/dify/main/docker/docker-compose.yaml>
- <https://raw.githubusercontent.com/langgenius/dify/main/docker/.env.example>
- <https://raw.githubusercontent.com/langgenius/dify/main/docker/envs/core-services/shared.env.example>
- <https://raw.githubusercontent.com/langgenius/dify/main/api/configs/feature/__init__.py>
- <https://raw.githubusercontent.com/langgenius/dify/main/api/controllers/common/controller_schemas.py>
- <https://raw.githubusercontent.com/langgenius/dify/main/api/controllers/service_api/app/workflow.py>
- <https://api.github.com/repos/langgenius/dify/contents/api/core/workflow/nodes>
- <https://github.com/langgenius/dify/commit/83e76817b60c0489abddedb5844f36675cdc9aaf>（JSON object 入参支持）
- <https://github.com/langgenius/dify/pull/29053>
- <https://github.com/langgenius/dify-official-plugins/blob/main/agent-strategies/cot_agent/strategies/function_calling.py>

### GitHub Issues / Proposals（证据）
| Issue | 标题 | 关联章节 |
|---|---|---|
| [#22010](https://github.com/langgenius/dify/issues/22010) | Support nested JSON object input in "Start" node（not planned） | §4.2 |
| [#29846](https://github.com/langgenius/dify/issues/29846) | Support Object and Array Types in Start Node（not planned） | §4.2 |
| [#35032](https://github.com/langgenius/dify/issues/35032) | Object of type File is not JSON serializable | §4.2 / §10.4 |
| [#35327](https://github.com/langgenius/dify/issues/35327) | add async response_mode + Idempotency-Key（not planned） | §9.1 / §9.3 / §9.4 |
| [#28640](https://github.com/langgenius/dify/issues/28640) | Workflow log lost when SSE connection close | §9.4 |
| [#26342](https://github.com/langgenius/dify/issues/26342) | GET endpoints mutate state | §9.7 |
| [#39423](https://github.com/langgenius/dify/issues/39423) | socket_timeout breaks Socket.IO pub/sub | §2.3 / §9.7 |
| [#29599](https://github.com/langgenius/dify/issues/29599) | rag workflow and hit-testing timeout | §9.7 |
| [#4545](https://github.com/langgenius/dify/issues/4545) | HTTP Request / custom tool timeout | §9.7 |
| [#12834](https://github.com/langgenius/dify/issues/12834) | API Timeout in self-hosted workflow（SSRF 代理坑） | §2.4 / §9.7 |
| [#14225](https://github.com/langgenius/dify/issues/14225) | Workflow code block randomly timeout | §9.7 |
| [#41996](https://github.com/langgenius/dify/issues/41996) | New Agent 不渲染 HITL 表单 | §3.4 |
| [#42075](https://github.com/langgenius/dify/issues/42075) | Proposal: extend openapi group for IaC（open） | §6.3 |
| [#42147](https://github.com/langgenius/dify/issues/42147) | Roadmap: business contracts and invariants（提及 exactly-once 不是保证） | §9.3 |

### 第三方（低权威，已标注）
- <https://www.respan.ai/market-map/compare/dify-vs-langgraph>（厂商内容营销）
- <https://github.com/wanmol/goal-flow>（第三方 LangGraph 项目，transpile Dify DSL）
- <https://segmentfault.com/a/1190000047746298>（社区观点）
- <https://cloud.tencent.com.cn/developer/article/2719465>（社区观点："在网关层补生产化护栏"）
- <https://jishuzhan.net/article/2046417492596293634>（第三方引擎对比分析）

---

## 附录 B：对原方案的关键"认知纠正"清单（供主 Agent 快速对齐）

| 原假设 | 实际（1.17.x） | 证据 |
|---|---|---|
| `inputs` 只接受字符串与文件对象 | **API schema 接受任意 JSON 值**（`dict[str, Any]`）；能被消费的是**已声明的 User Input 变量**；**JSON Code（`json-object`）类型自 2025-12-03 起支持**，并可用 JSON Schema（Draft 7）强校验；**没有顶层数组类型** | §4.2 |
| Dify 不支持 HITL，必须放 LangGraph | **Human Input 节点原生支持**，且**暂停/恢复全流程可 API 驱动**（`form_token` → GET/POST `/form/human_input/{token}` → 重连 events 流） | §3.4 |
| Dify 不支持循环/迭代 | **Loop（≤100）与 Iteration 节点均存在**；并行分支 ≤10；子流程 ≤5 层 | §3.3 |
| Dify 可以靠 API 改 prompt | **没有公开的配置写 API**；唯一程序化路径是 **DSL + difyctl**，且导出是 Draft、导入写 Draft、需手动 Publish | §6 |
| Dify 可以当多租户 SaaS 卖 | **LICENSE 明确禁止**未经书面授权的多租户运营 | §1.2 |
| 用 docker compose 只有 9 个组件 | 1.17.x 实际有 **api / api_websocket / worker / worker_beat / web / db / redis / sandbox / local_sandbox / plugin_daemon / agent_backend / ssrf_proxy / agent_ssrf_proxy / nginx + 向量库**（另有 milvus 时 +3） | §2.1 |
| Code 节点沙箱能装任意库 / 有充足超时 | **执行超时 15 s**；输出限制严格（字符串 40 万、嵌套 5 层、**对象数组仅 30 元素**）；**预装库清单文档未穷举**；文档说"禁网"但默认 `SANDBOX_ENABLE_NETWORK=true`（走 SSRF 代理） | §8.3 |
| Dify 调用内网服务很直接 | **SSRF 代理默认 deny 私网**，必须配 `SSRF_PROXY_ALLOW_PRIVATE_IPS` | §2.4 |
| LLM 调用不幂等但可以靠重试 | **官方明说 run 不是幂等的**，无 Idempotency-Key，重试会产生重复 run | §9.3 |
