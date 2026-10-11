# syntax=docker/dockerfile:1

FROM python:3.11-bookworm AS requirements-stage

WORKDIR /tmp

# 国内网络加速：poetry 安装器与 poetry 自身都通过 pip 取包，统一指向 PyPI 镜像
ENV POETRY_HOME="/opt/poetry" PATH="${PATH}:/opt/poetry/bin" \
  PIP_INDEX_URL="https://mirrors.aliyun.com/pypi/simple" \
  PIP_TRUSTED_HOST="mirrors.aliyun.com"

# 同时写入 pip 用户级配置，覆盖 poetry 安装器自建 venv 里的 pip
RUN python -m pip config set global.index-url "${PIP_INDEX_URL}" && \
  python -m pip config set global.trusted-host "${PIP_TRUSTED_HOST}"

RUN curl -sSL https://install.python-poetry.org | python - -y && \
  poetry self add poetry-plugin-export

COPY ./pyproject.toml ./poetry.lock* /tmp/

RUN poetry export -f requirements.txt --output requirements.txt --without-hashes --with deploy

FROM python:3.11-bookworm AS build-stage

WORKDIR /wheel

COPY --from=requirements-stage /tmp/requirements.txt /wheel/requirements.txt

# 国内网络加速：下载 wheel 走 PyPI 镜像
ENV PIP_INDEX_URL="https://mirrors.aliyun.com/pypi/simple" \
  PIP_TRUSTED_HOST="mirrors.aliyun.com"

RUN pip wheel --wheel-dir=/wheel --no-cache-dir --requirement /wheel/requirements.txt

FROM python:3.11-bookworm AS metadata-stage

WORKDIR /tmp

RUN --mount=type=bind,source=./.git/,target=/tmp/.git/ \
  git describe --tags --exact-match > /tmp/VERSION 2>/dev/null \
  || git rev-parse --short HEAD > /tmp/VERSION \
  && echo "Building version: $(cat /tmp/VERSION)"

FROM python:3.11-slim-bookworm

WORKDIR /app

ENV TZ=Asia/Shanghai DEBIAN_FRONTEND=noninteractive

COPY ./docker/start.sh /start.sh
RUN chmod +x /start.sh

COPY ./docker/gunicorn_conf.py /gunicorn_conf.py

ENV PYTHONPATH=/app

EXPOSE 8086

ENV APP_MODULE=bot:app

# 国内网络加速：Debian 官方源换阿里云镜像。
# bookworm 用 deb822 格式的 /etc/apt/sources.list.d/debian.sources，老版本才是 sources.list，两个都兜住。
RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null \
  || sed -i "s|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list

# Sarasa 字体走 gh-proxy 代理（GitHub releases 直连约 57KB/s，代理约 180KB/s）
RUN apt-get update \
  && apt-get install -y --no-install-recommends curl p7zip-full fontconfig fonts-noto-color-emoji \
  && curl -sSL https://gh-proxy.com/https://github.com/be5invis/Sarasa-Gothic/releases/download/v1.0.14/Sarasa-TTC-1.0.14.7z -o /tmp/sarasa.7z \
  && 7z x /tmp/sarasa.7z -o/tmp/sarasa \
  && install -d /usr/share/fonts/sarasa-gothic \
  && install -m644 /tmp/sarasa/*.ttc /usr/share/fonts/sarasa-gothic \
  && fc-cache -fv \
  && apt-get purge -y --auto-remove curl p7zip-full \
  && rm -rf /tmp/sarasa /tmp/sarasa.7z /var/lib/apt/lists/*

COPY --from=build-stage /wheel /wheel

RUN pip install --no-cache-dir --no-index --find-links=/wheel -r /wheel/requirements.txt && rm -rf /wheel

# 浏览器二进制走 npmmirror（实测约 10MB/s，官方 cdn.playwright.dev 约 2.8MB/s）
ENV PLAYWRIGHT_DOWNLOAD_HOST="https://cdn.npmmirror.com/binaries/playwright"

RUN playwright install --with-deps chromium \
  && rm -rf /var/lib/apt/lists/*

COPY --from=metadata-stage /tmp/VERSION /app/VERSION

COPY . /app/

CMD ["/start.sh"]
