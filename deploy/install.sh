#!/usr/bin/env bash
# 新企业专属实例；宿主只需Linux Bash、Docker/Compose和flock，不安装Python。
set -euo pipefail
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '用法：sudo bash deploy/install.sh [--configure] [配置文件] [实例目录覆盖]'
  echo '首次无配置会交互引导；已有配置不提问。Linux需Docker Engine、Compose、util-linux；macOS需Docker Desktop。'
  echo '例如：sudo bash deploy/install.sh；非交互：sudo bash deploy/install.sh deploy/.env'
  exit 0
fi
CONFIGURE=false
if [[ "${1:-}" == --configure ]]; then CONFIGURE=true; shift; fi
if [[ $# -gt 2 ]]; then echo '参数过多，请运行bash deploy/install.sh --help' >&2; exit 2; fi
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v docker >/dev/null 2>&1; then
  echo '缺少Docker。服务器先安装Docker Engine及Compose插件，Mac先启动Docker Desktop。示例检查：docker --version；docker compose version。官方指引：https://docs.docker.com/engine/install/' >&2
  exit 2
fi
if ! docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
  echo 'Docker服务不可用或当前账号无权限。Linux示例：sudo systemctl start docker，再用sudo执行安装；Mac请启动Docker Desktop。' >&2; exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
  echo '缺少Docker Compose插件。安装后用docker compose version验证：https://docs.docker.com/compose/install/' >&2; exit 2
fi
CONFIG_INPUT="${1:-$REPO_ROOT/deploy/.env}"
if [[ "$CONFIGURE" == true || ! -f "$CONFIG_INPUT" ]]; then
  if [[ ! -t 0 ]]; then
    echo "配置不存在或请求交互，但当前无终端。请人工运行bash deploy/configure.sh '$CONFIG_INPUT'，或由AI按deploy/.env.example生成配置后重试。" >&2; exit 2
  fi
  bash "$REPO_ROOT/deploy/configure.sh" "$CONFIG_INPUT"
fi
if [[ "$(uname -s)" == Darwin ]]; then
  # Docker Desktop提供Linux执行环境；默认实例和引用文件须放在完整交付目录内。
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  ENDPOINT="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
  [[ "$ENDPOINT" == unix://* && -z "${DOCKER_HOST:-}" ]] || { echo 'macOS安装要求本机Docker Desktop上下文' >&2; exit 2; }
  # 仓库外的公开配置在容器里也必须可见；否则会误报“配置不存在”。
  CONFIG_HOST="${1:-$REPO_ROOT/deploy/.env}"
  case "$CONFIG_HOST" in
    /*) ;;
    *) CONFIG_HOST="$REPO_ROOT/$CONFIG_HOST" ;;
  esac
  CONFIG_DIR="$(cd "$(dirname "$CONFIG_HOST")" 2>/dev/null && pwd || true)"
  EXTRA_MOUNTS=()
  if [[ -n "$CONFIG_DIR" && "$CONFIG_DIR/" != "$REPO_ROOT/"* ]]; then
    EXTRA_MOUNTS=(-v "$CONFIG_DIR:$CONFIG_DIR:ro")
  fi
  CLI_ARGS=(run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$REPO_ROOT:$REPO_ROOT")
  if [[ ${#EXTRA_MOUNTS[@]} -gt 0 ]]; then CLI_ARGS+=("${EXTRA_MOUNTS[@]}"); fi
  exec docker "${CLI_ARGS[@]}" \
    -w "$REPO_ROOT" -e "OFFICE_IMAGE=${OFFICE_IMAGE:-}" -e "OFFICE_AGENT=${OFFICE_AGENT:-}" docker:28-cli \
    sh -c 'apk add --no-cache bash coreutils util-linux >/dev/null && exec bash deploy/install.sh "$@"' office-install "$@"
fi
[[ "$(uname -s)" == Linux && "$EUID" -eq 0 ]] || { echo "Linux服务器请使用sudo；macOS需Docker Desktop" >&2; exit 2; }
command -v docker >/dev/null
command -v flock >/dev/null || { echo "缺少flock。Debian/Ubuntu示例：sudo apt-get install util-linux；其他Linux请安装util-linux。" >&2; exit 2; }
docker compose version >/dev/null
ENDPOINT="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
[[ "$ENDPOINT" == unix://* ]] || { echo '当前安装器要求服务器本机Docker，不能使用远程Docker上下文' >&2; exit 2; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="$(realpath "${1:-$REPO_ROOT/deploy/.env}")"
# 首次仅用固定摘要Python解释公开配置，不依赖业务镜像或宿主Python。
BOOTSTRAP_IMAGE='python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641'
SELECTION="$(docker run --rm --network none --read-only --cap-drop=ALL --security-opt=no-new-privileges:true \
  -e "OFFICE_IMAGE=${OFFICE_IMAGE:-}" -e "OFFICE_AGENT=${OFFICE_AGENT:-}" \
  -v "$REPO_ROOT/scripts/delivery_config.py:/bootstrap.py:ro" -v "$CONFIG_PATH:/input/config:ro" \
  "$BOOTSTRAP_IMAGE" python /bootstrap.py /input/config)"
mapfile -t SELECTED <<< "$SELECTION"
AGENT="${SELECTED[0]}"
IMAGE="${SELECTED[1]:-}"
resolve_image() {
  docker image inspect "$1" >/dev/null 2>&1 || docker pull "$1" >&2
  docker image inspect "$1" --format '{{.Id}}'
}
if [[ -z "$IMAGE" ]]; then
  IMAGE="office-data-service:installer-local-$AGENT"
  docker build --provenance=false --build-arg "OFFICE_AGENT=$AGENT" -f "$REPO_ROOT/deploy/Dockerfile" -t "$IMAGE" "$REPO_ROOT"
fi
IMAGE_ID="$(resolve_image "$IMAGE")"
# 预检只导入包、不读实例秘密、不请求模型；能力错误在建库前拒绝。
docker run --rm --network none --read-only --cap-drop=ALL "$IMAGE_ID" python -c \
  'import importlib.util,sys; enabled=importlib.util.find_spec("office_agent") is not None; expected=sys.argv[1]=="1"; assert enabled==expected,"镜像与OFFICE_AGENT不匹配"; exec("import office_agent.runtime, openai, anthropic" if enabled else "pass")' "$AGENT"
# 后续固定实际镜像ID，避免安装中标签被更换。
BASE=(docker run --rm --user 0:0 -v "$REPO_ROOT/deploy/installer:/installer:ro" -v "$REPO_ROOT/scripts:/helpers:ro" -v "$CONFIG_PATH:/input/settings.json:ro" -v "$REPO_ROOT/deploy/database/init.sh:/input/database-init.sh:ro")
PARAMETERS="$("${BASE[@]}" "$IMAGE_ID" python /installer/server.py parameters)"
mapfile -t PARAMETERS_LINES <<< "$PARAMETERS"
INSTANCE_INPUT="${2:-${PARAMETERS_LINES[0]}}"
if [[ "$INSTANCE_INPUT" != /* ]]; then INSTANCE_INPUT="$REPO_ROOT/$INSTANCE_INPUT"; fi
INSTANCE_PATH="$(realpath -m "$INSTANCE_INPUT")"
[[ "$INSTANCE_PATH" != / && "$INSTANCE_PATH" != "$REPO_ROOT" && "$INSTANCE_PATH" != *$'\n'* ]]
mkdir -p "$INSTANCE_PATH"
exec 9>"$INSTANCE_PATH/.install.lock"
flock -n 9 || { echo '此实例已有安装任务运行'; exit 2; }
MOUNTS=()
if [[ -n "${PARAMETERS_LINES[2]:-}" || -n "${PARAMETERS_LINES[3]:-}" ]]; then
  [[ -f "${PARAMETERS_LINES[2]}" && -f "${PARAMETERS_LINES[3]}" ]]
  MOUNTS=(-v "${PARAMETERS_LINES[2]}:/input/fullchain.pem:ro" -v "${PARAMETERS_LINES[3]}:/input/privkey.pem:ro")
fi
if [[ -n "${PARAMETERS_LINES[4]:-}" ]]; then MOUNTS+=(-v "${PARAMETERS_LINES[4]}:/input/ca.pem:ro"); fi
for OFFSET in 5 6 7; do
  if [[ -n "${PARAMETERS_LINES[$OFFSET]:-}" ]]; then
    SECRET_PATH="${PARAMETERS_LINES[$OFFSET]}"
    [[ "$SECRET_PATH" == /* && -f "$SECRET_PATH" && ! -L "$SECRET_PATH" ]]
    case "$OFFSET" in 5) SECRET_NAME=owner_password_file;; 6) SECRET_NAME=app_password_file;; 7) SECRET_NAME=identity_password_file;; esac
    MOUNTS+=(-v "$SECRET_PATH:/input/$SECRET_NAME:ro")
  fi
done
# 所有组件固定到本机实际镜像ID；不在重跑时静默拉取新版。
PG_IMAGE="$(resolve_image pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0)"
# 多架构索引摘要：amd64与arm64服务器解析到各自平台，避免同名标签跨时间漂移。
KC_IMAGE="$(resolve_image quay.io/keycloak/keycloak:26.8.1@sha256:a78d6b79572168cb25886b80a8b5b8d4ed4390743e3eecb810538683e209dd6b)"
GW_IMAGE="$(resolve_image nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236)"
PG_UID="$(docker run --rm --entrypoint id "$PG_IMAGE" -u postgres)"
PG_GID="$(docker run --rm --entrypoint id "$PG_IMAGE" -g postgres)"
KC_UID="$(docker run --rm --entrypoint id "$KC_IMAGE" -u)"
KC_GID="$(docker run --rm --entrypoint id "$KC_IMAGE" -g)"
INFRASTRUCTURE="{\"postgres\":\"$PG_IMAGE\",\"identity\":\"$KC_IMAGE\",\"gateway\":\"$GW_IMAGE\"}"
"${BASE[@]}" "${MOUNTS[@]}" -v "$INSTANCE_PATH:/state" "$IMAGE_ID" python /installer/server.py prepare --host-root "$INSTANCE_PATH" --source "$REPO_ROOT" --image "$IMAGE_ID" --pg-uid "$PG_UID" --pg-gid "$PG_GID" --kc-uid "$KC_UID" --kc-gid "$KC_GID" --infrastructure "$INFRASTRUCTURE"
# 明确-p优先级最高，不受宿主COMPOSE_PROJECT_NAME影响，不清理其他项目。
PROJECT="$(docker run --rm --user 0:0 -v "$INSTANCE_PATH:/state:ro" "$IMAGE_ID" python -c 'import json;print(json.load(open("/state/installation.json"))["project"])')"
# 本地实例独立保留镜像引用，不因下一次源码构建替换默认标签而丢失。
docker tag "$IMAGE_ID" "office-instance:$PROJECT"
COMPOSE=(docker compose -p "$PROJECT" -f "$INSTANCE_PATH/compose.json")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" --profile setup run --rm setup database
if "${COMPOSE[@]}" config --services | grep -qx postgres; then
  "${COMPOSE[@]}" up -d postgres
fi
"${COMPOSE[@]}" up -d identity gateway
"${COMPOSE[@]}" --profile setup run --rm setup identity
"${COMPOSE[@]}" --profile setup run --rm --entrypoint python setup -m alembic -c /opt/office/alembic.ini upgrade head
"${COMPOSE[@]}" --profile setup run --rm setup bootstrap
"${COMPOSE[@]}" --profile setup run --rm setup database-check
"${COMPOSE[@]}" up -d service
"${COMPOSE[@]}" --profile setup run --rm setup verify
echo "部署检查已通过。请核对当前身份；首次登录时由本人读取受限初始资料并改密：$INSTANCE_PATH/credentials/first-login.txt"
echo "状态报告：$INSTANCE_PATH/result.json；重跑相同命令可续跑，不会删除卷或重置密码。"
