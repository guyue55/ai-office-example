#!/usr/bin/env bash
# 显式维护入口；宿主仅需Docker，预检默认不修改实例。
set -euo pipefail
if [[ "${1:-}" == -h || "${1:-}" == --help || $# -eq 0 ]]; then
  echo '用法：bash deploy/relocate.sh <实例目录> --url http://新内网IP:端口 --listen 新内网IP --image 已验证镜像 [--apply]'
  echo '仅支持内置PG、同端口HTTP演示实例。--apply会停写、备份、迁址并要求所有终端重新登录。'
  exit 0
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTANCE="$(cd "$1" && pwd)"
shift
command -v docker >/dev/null || { echo '请先安装Docker及Compose' >&2; exit 2; }
docker compose version >/dev/null
ENDPOINT="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
[[ "$ENDPOINT" == unix://* ]] || { echo '只允许本机Docker上下文' >&2; exit 2; }
exec docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$ROOT:$ROOT" -v "$INSTANCE:$INSTANCE" -w "$ROOT" docker:28-cli \
  sh -c 'apk add --no-cache python3 >/dev/null && exec python3 deploy/installer/relocate.py "$@"' office-relocate "$INSTANCE" "$@"
