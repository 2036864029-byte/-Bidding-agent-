# Langfuse 自托管（评测与可观测性 · E0-2）

本地开发用的 Langfuse 观测 + 评测平台。**官方 compose 原样保留**（6 服务），便于跟随上游升级。

## 组成

| 服务 | 作用 | 主机端口 |
|---|---|---|
| `langfuse-web` | 控制台 / API | `3000`（对外） |
| `langfuse-worker` | 异步摄取与评测执行 | `3030`（仅本机） |
| `postgres` | 元数据（组织/项目/数据集/score） | `5432`（仅本机） |
| `clickhouse` | trace / span 事件存储 | `8123`、`9000`（仅本机） |
| `redis` | 摄取队列 | `6379`（仅本机） |
| `minio` | 事件与媒体 blob 存储 | `9090`（对外）、`9091`（控制台） |

数据全部使用 **named volume**，不使用 bind mount——规避 Windows/WSL2 跨文件系统 I/O 慢的问题。

## 首次启动

```bash
cp deploy/langfuse/.env.example deploy/langfuse/.env
# 编辑 .env：填 NEXTAUTH_SECRET / ENCRYPTION_KEY / SALT 及各数据层密码
# 生成密钥：python -c "import secrets;print(secrets.token_hex(32))"

docker compose --project-directory deploy/langfuse \
  -f deploy/langfuse/docker-compose.yml --env-file deploy/langfuse/.env up -d
```

`LANGFUSE_INIT_*` 会在首次启动时自动创建组织 / 项目 / 用户并下发固定 API Key，
无需到控制台手工复制。

- 控制台：http://localhost:3000
- 默认账号：`.env` 里的 `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD`

## 日常操作

```bash
# 查看状态
docker compose --project-directory deploy/langfuse -f deploy/langfuse/docker-compose.yml ps

# 停止（保留数据）
docker compose --project-directory deploy/langfuse -f deploy/langfuse/docker-compose.yml down

# 停止并清空数据（⚠️ 会删除全部 trace / 数据集 / score）
docker compose --project-directory deploy/langfuse -f deploy/langfuse/docker-compose.yml down -v

# 跟随日志
docker compose --project-directory deploy/langfuse -f deploy/langfuse/docker-compose.yml logs -f langfuse-web
```

## 与 Milvus 栈的关系

**独立**：不复用 Milvus 的 MinIO。若复用，`milvus/docker-compose.yml down -v` 会连带
清空 Langfuse 的 blob。代价是多一个约 100MB 的容器，换取故障隔离。

端口无冲突（已核实）：Milvus 用 `19531`/`9093`/`2380`/`9003`/`9004`，MySQL 用 `3306`。

## 生产迁移

上线时把本目录整体搬到 Linux 服务器（Linux 上无 Docker Desktop 的资源与授权限制），
`docker compose up -d` 即可；数据持久化沿用 named volume，或改挂独立数据盘。
