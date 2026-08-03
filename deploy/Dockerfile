# 后端镜像：FastAPI + 已构建知识库
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 使用清华 Debian 镜像源（腾讯云访问官方 deb.debian.org 极慢）
RUN sed -i 's|deb.debian.org/debian|mirrors.tuna.tsinghua.edu.cn/debian|g; s|deb.debian.org/debian-security|mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources

# 安装构建工具（tree-sitter/numpy 等需要编译）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ make \
    && rm -rf /var/lib/apt/lists/*

# 配置国内镜像源：清华 PyPI 作为默认源（腾讯云访问官方 PyPI 会超时）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

# 先升级构建工具链，再安装 CPU 版 torch（服务器无 GPU，避免拉取数 GB 的 CUDA 版）
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 复制项目源码（打包目录已剔除 venv/node_modules/.git 等）
COPY pyproject.toml ./
COPY app ./app
COPY agent ./agent
COPY eval ./eval
COPY graph_builder ./graph_builder
COPY knowledge_builder ./knowledge_builder
COPY sdk_parser ./sdk_parser
COPY scripts ./scripts
COPY vector_store ./vector_store
COPY prompts ./prompts

# 已构建好的知识库数据（chroma 向量库 + knowledge + graph）
COPY data ./data

# embedding 模型离线缓存（all-MiniLM-L6-v2）
COPY hf_cache /root/.cache/huggingface

# 使用 --no-build-isolation，避免 PEP 517 隔离环境额外下载构建依赖
RUN pip install --no-cache-dir --no-build-isolation -e . \
    && rm -rf /root/.cache/pip

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
