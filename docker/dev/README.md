# Dify 本地开发部署（前后端热重载）

这份配置让你 **在 Docker 里跑全部服务**，同时保留前后端的热重载体验：

- **后端**：修改 `api/**/*.py` → Werkzeug 自动重载 Flask 进程
- **前端**：修改 `web/app/**/*.{ts,tsx,css,...}` → Turbopack HMR 立即刷新浏览器

不需要每改一行就 `docker compose restart`。

## 架构

```
┌────────────────────────────────────────────────────────────────────────┐
│                  docker-compose.dev.yaml (include)                     │
│                                                                        │
│   docker-compose.middleware.yaml                                       │
│   ┌────────────┬──────────┬──────────────┬──────────────┬────────────┐  │
│   │db_postgres │  redis   │   weaviate   │   sandbox    │ssrf_proxy  │  │
│   └────────────┴──────────┴──────────────┴──────────────┴────────────┘  │
│   ┌─────────────────────────┐                                          │
│   │      plugin_daemon      │ ←── PLUGIN_DIFY_INNER_API_URL=http://api  │
│   └─────────────────────────┘                                          │
│                                                                        │
│   + dev 服务（**复用生产镜像**，覆盖 entrypoint + bind 挂源码）         │
│   ┌─────────────────────────┐  ┌─────────────────────────────────┐      │
│   │  api  dify-api-lube     │  │  web  dify-web-lube             │      │
│   │  CMD: flask run --debug │  │  CMD: pnpm run dev  (Turbopack) │      │
│   │  bind ./api → /app/api  │  │  bind ./web → /app/web          │      │
│   │  bind ./dify-agent → …  │  │  (匿名卷保留 .next, node_modules)│     │
│   │  → :5001                │  │  → :3000                         │      │
│   └─────────────────────────┘  └─────────────────────────────────┘      │
└────────────────────────────────────────────────────────────────────────┘
```

## 策略：复用生产镜像

> **不要构建新的 dev 镜像**。

生产镜像 `dify-api-lube:20260808` / `dify-web-lube:20260808` 已经把所有 Python/Node 依赖都装好了（api 镜像 3.4 GB、web 镜像 544 MB），compose 文件里直接 `image:` 引用它们，再覆盖 `entrypoint` / `command` + bind 挂源码实现热重载。

这避免了 uv 在容器里重新拉 50+ 个 Python 包、编译 C 扩展（numpy、pandas、spacy、llvmlite 等），那个过程单次就要 20+ 分钟而且容易卡死。

**镜像从哪儿来**：项目根目录的 `docker/.env` 里有 `DIFY_API_IMAGE=dify-api-lube:20260808` / `DIFY_WEB_IMAGE=dify-web-lube:20260808`。如果是新的机器，需要先跑一次 `./docker/docker-compose.yaml` 的生产部署把它们拉/构出来。

## 一次性配置

```bash
cd docker

# 1. 复制中间件环境变量
cp envs/middleware.env.example middleware.env

# （可选）改 middleware.env 里的密码、暴露端口、向量库等

# 2. 检查你的 docker/.env 里的 COMPOSE_PROFILES 是否匹配 DB_TYPE
grep -E "^(DB_TYPE|COMPOSE_PROFILES)" .env
#   DB_TYPE=mysql
#   COMPOSE_PROFILES=${VECTOR_STORE:-weaviate},${DB_TYPE:-postgresql},collaboration
#
# ⚠️ 已知问题：docker-compose（独立二进制 v1.x）在同一个 .env 文件里做
# ${DB_TYPE:-postgresql} 展开时可能取不到同文件上文的 DB_TYPE，导致
# COMPOSE_PROFILES 错误地展开为 postgresql 而不是 mysql。
#
# 解决方案（二选一）：
#   a) 在 docker/.env 中把 COMPOSE_PROFILES 改成字面值，去掉 ${...}：
#        COMPOSE_PROFILES=weaviate,mysql,collaboration
#   b) 启动时手动指定 profile：
#        docker compose -f docker-compose.dev.yaml --profile dev --profile mysql up -d
```

## 启动

```bash
# 直接 up —— 因为用的是现成镜像，不需要 --build
# 注意：要带 --profile dev 让 api / web 启动；如果用 MySQL，按上面提示加 --profile mysql
docker compose -f docker-compose.dev.yaml --profile dev up -d

# 查看日志
docker compose -f docker-compose.dev.yaml --profile dev logs -f api web

# 浏览器打开
#   http://localhost:3000       ← Next.js dev（带 HMR）
#   http://localhost:5001       ← Flask dev server（带 Werkzeug reload）
#   http://localhost:8080       ← Weaviate
```

## 验证热重载

### 后端
```bash
# 编辑任意 .py 文件
echo "# test" >> api/controllers/web/app.py

# 观察日志，应看到：
#  * Detected change in '/app/api/...', reloading
docker compose -f docker-compose.dev.yaml logs -f api
```
刷新浏览器或 curl 接口，新行为立即生效。

### 前端
在 `web/app/` 下修改任意 `.tsx` 文件 → 浏览器应自动刷新（Turbopack HMR），无需重启容器。

## 常用命令

```bash
# 下面的所有命令都需要 --profile dev；如果用 MySQL，再加 --profile mysql

# 停止（保留数据卷）
docker compose -f docker-compose.dev.yaml --profile dev stop

# 完全清理（删除容器 + 网络；数据卷保留）
docker compose -f docker-compose.dev.yaml --profile dev down

# 重启某个服务
docker compose -f docker-compose.dev.yaml --profile dev restart api

# 进入容器调试
docker compose -f docker-compose.dev.yaml --profile dev exec api bash
docker compose -f docker-compose.dev.yaml --profile dev exec web sh

# 数据库迁移
docker compose -f docker-compose.dev.yaml --profile dev exec api flask upgrade-db

# 查看注册的 Flask 路由
docker compose -f docker-compose.dev.yaml --profile dev exec api flask routes
```

## 已知限制

| 限制 | 原因 | 临时方案 |
|------|------|---------|
| WebSocket 流式聊天接口断开 | `flask run` 不支持 WS 握手 | 见下文「切换到 Gunicorn」 |
| macOS / Windows 文件监听可能失效 | bind mount 不触发 inotify | 在 web 服务加 `WATCHPACK_POLLING=true` `CHOKIDAR_USEPOLLING=true` |
| 容器内 `pytest` / `ruff` 不可用 | 生产镜像里没装 dev 组（`dify-agent` 是 `dev` 组里的 path 依赖，运行时拿不到） | 在宿主机跑测试 / lint；要在容器里跑，把 `entrypoint` 改成 `["bash"]` 然后 exec 手动 `pip install pytest ruff` |

### 临时切到 Gunicorn（需要测流式聊天）

进入 api 容器手动跑 gunicorn：

```bash
docker compose -f docker-compose.dev.yaml exec api bash
# 在容器内：
gunicorn --bind 0.0.0.0:5001 --workers 1 \
         --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
         app:socketio_app
```

或者改 `docker-compose.dev.yaml` 把 `command:` 改成上面那条命令，`docker compose restart api` 即可。

## 从源码构建 dev 镜像（可选）

> 默认**不需要**。只有在生产镜像不可用、或者想完全控制构建过程时才用。

`api/Dockerfile.dev` 和 `web/Dockerfile.dev` 提供了从 `pyproject.toml` / `pnpm-lock.yaml` 重新构建的路径，但**这两个文件目前已知在 uv 装包阶段会卡死**（阿里云镜像对某些 wheel 提取超时）。如果要启用，需要：

1. 临时换源：
   ```bash
   docker buildx build --build-arg UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
     -f api/Dockerfile.dev -t dify-api-dev:local api/
   ```
2. 把 `docker-compose.dev.yaml` 里 `api.image` 改回 `dify-api-dev:local` 并启用 `build:` 段。

## 与生产部署的关系

- 这份配置与 `docker/docker-compose.yaml`（生产）**复用同一组镜像**：
  - `dify-api-lube:20260808` / `dify-web-lube:20260808` 来自生产构建
  - 数据卷 `api-storage` 是新建的命名卷
- 生产部署仍走 `./docker/docker-compose.yaml`。

## 故障排查

**`api` 容器立刻退出**
- 多半是缺环境变量。检查 `docker/.env` 里的 `DB_HOST` / `REDIS_HOST` 等。
- 或 `dify-agent/` 目录不存在（`api` 容器需要它做 type 注解）。检查仓库根目录下有 `../dify-agent/`。

**`web` 容器端口占用**
- 宿主机 3000 端口被占用。改 `docker-compose.dev.yaml` 里 `web.ports` 的宿主机端口。

**HMR 不生效**
- Linux bind mount 默认 inotify 工作正常；如果不行，在 web 服务加 `WATCHPACK_POLLING=true`。

**`plugin_daemon` 连不上 `api`**
- 检查 `PLUGIN_DIFY_INNER_API_URL` 是否为 `http://api:5001`（不是 `host.docker.internal:5001`）。

**`flask run --debug` 报错 `Could not locate a Flask application`**
- 容器内 WorkDir 应该是 `/app/api`。检查生产镜像的 WorkDir：
  ```bash
  docker inspect dify-api-lube:20260808 --format '{{.Config.WorkingDir}}'
  ```
  应该是 `/app/api`，如果不是，改 `docker-compose.dev.yaml` 加 `working_dir: /app/api`。