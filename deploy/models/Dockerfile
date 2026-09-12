# ztb 模型服务镜像 — embedding(bge-m3) + rerank(bge-reranker-v2-m3)
#
# 基础镜像走 daocloud 镜像源：服务器实测 registry-1.docker.io 直连不通（curl 000），
# docker.m.daocloud.io 可达（302）。runtime 变体足够——只做推理，不需要编译器。
ARG BASE_IMAGE=docker.m.daocloud.io/pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime

FROM ${BASE_IMAGE}

# pip 走阿里云镜像：服务器实测 pypi.org 直连虽可达但仅 36~60 KB/s（跨境限速），
# mirrors.aliyun.com 为国内源。HF 走 hf-mirror（huggingface.co 不通）
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HOME=/root/.cache/huggingface \
    PYTHONUNBUFFERED=1 \
    TOKENIZERS_PARALLELISM=false

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app.py /app/app.py
WORKDIR /app

# 容器内固定 8000；宿主机端口映射见 docker-compose.yml
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
