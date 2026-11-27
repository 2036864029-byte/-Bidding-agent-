# ============================================================
# ztb 主程序（业务应用）镜像
# ============================================================
# 边界：本镜像只装**业务代码 + 运行期依赖**，**不含模型、不含 torch**
#   - embedding / rerank / MinerU 是**独立服务**，通过 HTTP base_url 调用
#   - 因此体积约 1~1.5GB（对比：模型服务镜像 ~4GB+）
#   - 这条边界是 P6 清理死依赖（-915MB）的直接收益
#
# 基础镜像走 ARG：本机 Docker Hub 可达用默认值；**服务器 Docker Hub 直连不通**，
# 构建时传 `--build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim`
#
# 构建（仓库根目录执行）：
#   docker compose -f deploy/api/docker-compose.yml up -d --build
# ============================================================
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# pip 走阿里云镜像：本机与服务器实测均可达（pypi.org 跨境在服务器上仅 36~60 KB/s）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    UV_LINK_MODE=copy

# uv：项目用它管理依赖（pyproject + uv.lock），容器内也用同一套保证版本一致
RUN pip install --no-cache-dir uv

WORKDIR /app

# ① 先只拷依赖清单 —— 利用层缓存：改代码不必重装依赖
COPY pyproject.toml uv.lock ./

# ② 装依赖（进 /app/.venv）
#    --frozen             严格按 uv.lock（lock 过期会失败，需先 uv lock）
#    --no-dev             生产不带评测依赖（ragas 等）
#    --no-install-project 本项目不是包（无 [build-system]），只装依赖
RUN uv sync --frozen --no-dev --no-install-project

# ③ 再拷业务代码（含包内的运行时资源：
#    core/config/profiles/models.yaml、orchestration/routing_rules.yaml 等）
COPY agent/ ./agent/
COPY api/ ./api/
# channels/ 是运行期依赖：api/server.py 在 create_app() 里**无条件**导入
# channels.feishu.push（未配 FEISHU_APP_ID 时该推送器是 no-op）。漏拷会导致
# 容器启动即 ModuleNotFoundError，而不是功能降级 —— 2026-12 实测踩过。
COPY channels/ ./channels/
COPY core/ ./core/
COPY orchestration/ ./orchestration/
COPY price_engine/ ./price_engine/
COPY public_kb/ ./public_kb/

# 数据目录（检查点/缓存/manifest）：运行时用卷挂载，先建好保证可写
RUN mkdir -p /app/DATA

ENV PATH="/app/.venv/bin:$PATH" \
    API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000

# 用 python 做健康检查（slim 镜像无 curl）
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# 入口：python -m api.server（读 API_HOST/API_PORT/API_WORKERS 等环境变量）
CMD ["python", "-m", "api.server"]
