FROM ghcr.io/astral-sh/uv:0.9.2 AS uv
FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY scripts/prepare_model.py scripts/prepare_model.py
COPY scripts/prepare_vad.py scripts/prepare_vad.py
ENV NEEDLE_TELEMETRY=0 DO_NOT_TRACK=1
RUN .venv/bin/python scripts/prepare_model.py /opt/whistle
RUN .venv/bin/python scripts/prepare_vad.py /opt/whistle

# HaRP's client includes Nextcloud's metadata authentication extension.
ARG TARGETARCH
COPY native/cpu_limit.c /tmp/cpu_limit.c
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gcc libc6-dev \
    && gcc -shared -fPIC -O2 -Wall -Wextra -Werror -o /opt/whistle/libcassini_cpu_limit.so /tmp/cpu_limit.c -ldl -pthread \
    && curl -fsSL "https://raw.githubusercontent.com/nextcloud/HaRP/e28dcb5872267a20af101f882778527523aa6849/exapps_dev/frp_0.61.1_linux_${TARGETARCH}.tar.gz" -o /tmp/frp.tar.gz \
    && tar -xzf /tmp/frp.tar.gz -C /tmp \
    && cp /tmp/frp_0.61.1_linux_${TARGETARCH}/frpc /usr/local/bin/frpc

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tini fonts-dejavu-core fonts-dejavu-extra \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home cassini
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /opt/whistle /opt/whistle
COPY --from=build /usr/local/bin/frpc /usr/local/bin/frpc
COPY cassini_live cassini_live
COPY scripts/entrypoint.py scripts/entrypoint.py
COPY LICENSE NOTICE.md ./
COPY LICENSES LICENSES
ENV PATH="/app/.venv/bin:$PATH" APP_HOST=0.0.0.0 APP_PORT=8080 \
    NEEDLE_TELEMETRY=0 DO_NOT_TRACK=1 HF_HUB_OFFLINE=1 ORT_DISABLE_TELEMETRY=1 \
    NEEDLE3_LIB_PATH=/opt/whistle/libneedle3.so \
    CASSINI_LIVE_WEIGHTS=/opt/whistle/whistle.cact \
    CASSINI_WHISTLE_CPU_SHIM=/opt/whistle/libcassini_cpu_limit.so \
    CASSINI_WHISTLE_THREADS=2 PYTHONDONTWRITEBYTECODE=1
# HaRP installs certificates with docker exec as the image's default user.
# The entrypoint drops privileges before starting FRP or the caption service.
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "python", "scripts/entrypoint.py"]
