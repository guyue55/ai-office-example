# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.18@sha256:5713fa8217f92b80223bc83aac7db36ec80a84437dbc0d04bbc659cae030d8c9
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/office/venv
WORKDIR /build
COPY pyproject.toml uv.lock VERSION template.toml template-state.json LICENSE NOTICE ./
COPY scripts/configure_template.py scripts/configure_template.py
COPY contracts/openapi.json contracts/openapi.json
COPY deploy/installer/product.json deploy/installer/product.json
COPY deploy/identity/ deploy/identity/
COPY installers/client/product.json installers/client/product.json
COPY clients/python/ clients/python/
COPY clients/agent/ clients/agent/
COPY packages/api-contracts/ packages/api-contracts/
COPY office-data-service/ office-data-service/
RUN python scripts/configure_template.py --check
ARG OFFICE_AGENT=0
RUN --mount=type=cache,target=/root/.cache/uv \
    case "$OFFICE_AGENT" in \
      0) uv sync --locked --no-dev --no-editable --package office-data-service ;; \
      1) uv sync --locked --no-dev --no-editable --package office-data-service --extra agent ;; \
      *) echo 'OFFICE_AGENT须为0或1' >&2; exit 2 ;; \
    esac

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/office/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OFFICE_CONFIG=/etc/office/service.json
RUN groupadd --gid 10001 office && useradd --uid 10001 --gid office --no-create-home office \
    && mkdir -p /etc/office && chown office:office /etc/office
COPY --from=builder /opt/office/venv /opt/office/venv
COPY office-data-service/alembic.ini /opt/office/alembic.ini
COPY office-data-service/migrations /opt/office/migrations
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /opt/office/
WORKDIR /opt/office
USER 10001:10001
EXPOSE 8800
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8800/health/ready',timeout=3).close()"]
CMD ["office-service", "serve", "--host", "0.0.0.0", "--port", "8800"]
