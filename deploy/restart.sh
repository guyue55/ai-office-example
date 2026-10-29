#!/usr/bin/env bash
# 自动恢复实例：检测活动内网IPv4，地址变化时保留数据迁址，否则重启入口与服务。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTANCE="${OFFICE_INSTANCE_DIR:-}"
REQUESTED_IP=""

usage() {
  cat <<'EOF'
用法：bash deploy/restart.sh [--instance 实例目录] [--ip 登录主地址]

不给--instance时优先选择正在运行的服务器实例；只有一个候选时自动选择。
HTTP验收实例检测到IP变化后保留数据库和身份，执行同端口迁址。
多个活动内网地址且旧地址已消失时，交互选择唯一登录主地址；其余地址仍由lan监听。
正式HTTPS稳定域名实例不按IP改写公开地址，只恢复容器与健康状态。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance) INSTANCE="${2:?--instance需要目录}"; shift 2 ;;
    --ip) REQUESTED_IP="${2:?--ip需要IPv4}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

probe_docker() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 6 docker info >/dev/null 2>&1
    return
  fi
  docker info >/dev/null 2>&1 &
  local pid=$!
  local i
  for i in $(seq 1 12); do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid"
      return
    fi
    sleep 0.25
  done
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  return 1
}

ensure_docker() {
  probe_docker && return
  echo 'Docker引擎未响应，正在尝试启动…' >&2
  if [[ "$(uname -s)" == Darwin ]]; then
    open -a Docker
  elif command -v systemctl >/dev/null 2>&1 && [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    systemctl start docker
  fi
  local i
  for i in $(seq 1 12); do
    probe_docker && { echo 'Docker引擎已就绪。' >&2; return; }
    sleep 1
  done
  if [[ "$(uname -s)" == Darwin ]]; then
    echo 'Docker Desktop未恢复，重启其桌面进程（保留镜像、容器和卷）…' >&2
    ps ax -o pid=,command= | awk '/\/Applications\/Docker.app\/Contents\/MacOS\/(Docker Desktop|com.docker.backend|com.docker.build)/ {print $1}' | xargs kill -TERM 2>/dev/null || true
    for i in $(seq 1 20); do
      ps ax -o command= | grep -q '/Applications/Docker.app/Contents/MacOS/com.docker.backend' || break
      sleep 0.5
    done
    ps ax -o pid=,command= | awk '/\/Applications\/Docker.app\/Contents\/MacOS\/(Docker Desktop|com.docker.backend|com.docker.build)/ {print $1}' | xargs kill -KILL 2>/dev/null || true
    open -a Docker
    for i in $(seq 1 30); do
      probe_docker && { echo 'Docker引擎已重新启动。' >&2; return; }
      sleep 1
    done
  fi
  echo 'Docker引擎仍未就绪。请查看 Docker Desktop 的故障处理页面。' >&2
  exit 2
}

json_marker() {
  local file="$1"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$file" <<'PY'
import json, sys
v=json.load(open(sys.argv[1]))
s=v.get('settings', {})
print('\t'.join(str(x or '') for x in (v.get('project'),v.get('image'),s.get('public_url'),s.get('port'),'1' if s.get('allow_lan_http_demo') else '0',s.get('hostname'))))
PY
  else
    local image
    image="$(sed -n 's/^  "image": "\([^"]*\)",$/\1/p' "$file" | head -n 1)"
    docker run --rm --network none -v "$file:/state/installation.json:ro" "$image" python -c \
      'import json;v=json.load(open("/state/installation.json"));s=v.get("settings",{});print("\\t".join(str(x or "") for x in (v.get("project"),v.get("image"),s.get("public_url"),s.get("port"),"1" if s.get("allow_lan_http_demo") else "0",s.get("hostname"))))'
  fi
}

is_private_ipv4() {
  local ip="$1"
  awk -F. 'NF==4 && $1==10 {ok=1} NF==4 && $1==192 && $2==168 {ok=1} NF==4 && $1==172 && $2>=16 && $2<=31 {ok=1} END {exit ok?0:1}' <<<"$ip"
}

active_addresses() {
  if [[ "$(uname -s)" == Darwin ]]; then
    local iface
    for iface in $(ifconfig -l); do
      case "$iface" in
        lo*|utun*|gif*|stf*|awdl*|llw*|bridge*|ap*) continue ;;
      esac
      ifconfig "$iface" 2>/dev/null | awk -v i="$iface" '$1=="inet"{print i "|" $2}'
    done
  elif command -v ip >/dev/null 2>&1; then
    ip -4 -o addr show scope global | awk '{split($4,a,"/"); if ($2 !~ /^(lo|docker|br-|veth|virbr|tun|tap|wg|zt)/) print $2 "|" a[1]}'
  else
    local ip
    for ip in $(hostname -I 2>/dev/null || true); do printf 'host|%s\n' "$ip"; done
  fi
}

collect_candidates() {
  local iface ip line
  active_addresses | while IFS='|' read -r iface ip; do
    if is_private_ipv4 "$ip"; then printf '%s|%s\n' "$iface" "$ip"; fi
  done | sort -u
}

instance_candidates() {
  find "$ROOT/runtime" -type f -name installation.json \
    ! -path '*/relocations/*' ! -path '*/.office/*' -print 2>/dev/null | while read -r file; do
      local folder
      folder="$(dirname "$file")"
      [[ -f "$folder/service/service.json" ]] || continue
      grep -q '"project": "office-' "$file" && printf '%s\n' "$folder"
    done | sort -u
}

if [[ -z "$INSTANCE" ]]; then
  ensure_docker
  INSTANCES="$(instance_candidates)"
  COUNT="$(printf '%s\n' "$INSTANCES" | awk 'NF{n++} END{print n+0}')"
  if [[ "$COUNT" -eq 1 ]]; then
    INSTANCE="$INSTANCES"
  elif [[ "$COUNT" -gt 1 ]]; then
    RUNNING=""
    while read -r folder; do
      [[ -n "$folder" ]] || continue
      project="$(sed -n 's/^  "project": "\([^"]*\)",$/\1/p' "$folder/installation.json" | head -n 1)"
      if [[ -n "$project" ]] && docker ps --filter "name=${project}-gateway-1" --format '{{.Names}}' | grep -qx "${project}-gateway-1"; then
        RUNNING="${RUNNING}${folder}"$'\n'
      fi
    done <<<"$INSTANCES"
    RUNNING_COUNT="$(printf '%s' "$RUNNING" | awk 'NF{n++} END{print n+0}')"
    if [[ "$RUNNING_COUNT" -eq 1 ]]; then
      INSTANCE="$(printf '%s\n' "$RUNNING" | awk 'NF{print;exit}')"
    elif [[ -t 0 ]]; then
      echo '检测到多个服务器实例，请选择要重启的实例：' >&2
      n=0
      while read -r folder; do
        [[ -n "$folder" ]] || continue
        n=$((n + 1)); echo "  $n) $folder" >&2
      done <<<"$INSTANCES"
      read -r -p "实例编号 [1]: " choice
      choice="${choice:-1}"
      INSTANCE="$(printf '%s\n' "$INSTANCES" | sed -n "${choice}p")"
    else
      echo '检测到多个服务器实例；非交互环境请用--instance指定。' >&2
      exit 2
    fi
  else
    echo '未找到已安装的服务器实例；请用--instance指定。' >&2
    exit 2
  fi
fi

[[ -d "$INSTANCE" && -f "$INSTANCE/installation.json" && -f "$INSTANCE/compose.json" ]] || {
  echo "实例目录无效：$INSTANCE" >&2
  exit 2
}
INSTANCE="$(cd "$INSTANCE" && pwd)"
ensure_docker
IFS=$'\t' read -r PROJECT IMAGE PUBLIC_URL PORT DEMO CURRENT_HOST <<<"$(json_marker "$INSTANCE/installation.json")"
[[ -n "$PROJECT" && -n "$IMAGE" && -n "$PORT" ]] || { echo '实例配置缺少项目、镜像或端口。' >&2; exit 2; }

refresh_gateway() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$ROOT/deploy/installer/refresh_gateway.py" "$INSTANCE"
  else
    docker run --rm --network none --user 0:0 \
      -v "$ROOT:$ROOT:ro" -v "$INSTANCE:$INSTANCE" "$IMAGE" \
      python "$ROOT/deploy/installer/refresh_gateway.py" "$INSTANCE"
  fi
}

if [[ "$DEMO" != 1 ]]; then
  echo "实例：$INSTANCE"
  echo "稳定公开地址：$PUBLIC_URL"
  echo '正式地址不按服务器IP改写；恢复容器并重载网关与服务…' >&2
  refresh_gateway
  docker compose -p "$PROJECT" -f "$INSTANCE/compose.json" up -d
  docker compose -p "$PROJECT" -f "$INSTANCE/compose.json" restart gateway service
  ready=0
  for i in $(seq 1 60); do
    if command -v curl >/dev/null 2>&1 \
      && curl -fsS --noproxy '*' --max-time 3 -H "Host: $CURRENT_HOST" \
        "http://127.0.0.1:${PORT}/health/ready" >/dev/null 2>&1; then
      ready=1; break
    fi
    sleep 1
  done
  [[ "$ready" == 1 ]] || { echo '容器已启动，但健康检查未通过。' >&2; exit 1; }
  echo "完成：$PUBLIC_URL"
  exit 0
fi

CANDIDATES="$(collect_candidates)"
COUNT="$(printf '%s\n' "$CANDIDATES" | awk -F'|' 'NF==2{n++} END{print n+0}')"
if [[ "$COUNT" -eq 0 ]]; then
  echo '未检测到可用的RFC1918内网IPv4地址。' >&2
  exit 2
fi

address_is_active() {
  printf '%s\n' "$CANDIDATES" | awk -F'|' -v ip="$1" '$2==ip{found=1} END{exit found?0:1}'
}

if [[ -n "$REQUESTED_IP" ]]; then
  is_private_ipv4 "$REQUESTED_IP" || { echo "--ip不是内网IPv4：$REQUESTED_IP" >&2; exit 2; }
  address_is_active "$REQUESTED_IP" || { echo "--ip不在当前活动地址中：$REQUESTED_IP" >&2; exit 2; }
  SELECTED="$REQUESTED_IP"
elif [[ "$DEMO" == 1 ]] && address_is_active "$CURRENT_HOST"; then
  SELECTED="$CURRENT_HOST"
elif [[ "$COUNT" -eq 1 ]]; then
  SELECTED="$(printf '%s\n' "$CANDIDATES" | awk -F'|' 'NF==2{print $2}')"
elif [[ -t 0 ]]; then
  echo '检测到多个活动内网地址。端口会监听全部地址，但Keycloak登录只能固定一个主地址：' >&2
  n=0
  while IFS='|' read -r iface ip; do
    [[ -n "$ip" ]] || continue
    n=$((n + 1)); echo "  $n) $ip  ($iface)" >&2
  done <<<"$CANDIDATES"
  read -r -p '请选择员工登录主地址编号 [1]: ' choice
  choice="${choice:-1}"
  SELECTED="$(printf '%s\n' "$CANDIDATES" | awk -F'|' -v n="$choice" 'NR==n{print $2}')"
else
  echo '检测到多个活动内网地址，非交互环境无法确定登录主地址；请使用--ip。' >&2
  exit 2
fi
[[ -n "$SELECTED" ]] || { echo '未选择有效登录地址。' >&2; exit 2; }

echo "实例：$INSTANCE"
echo "检测地址：$(printf '%s\n' "$CANDIDATES" | awk -F'|' '{printf "%s%s(%s)",sep,$2,$1;sep=", "}')"
echo "登录主地址：$SELECTED"

if [[ "$SELECTED" != "$CURRENT_HOST" ]]; then
  echo "检测到HTTP实例地址由 $CURRENT_HOST 变为 $SELECTED，执行保留数据迁址…" >&2
  bash "$ROOT/deploy/relocate.sh" "$INSTANCE" \
    --url "http://$SELECTED:$PORT" --listen lan --image "$IMAGE" --apply
else
  echo '公开地址未变化，恢复容器并重载网关与服务…' >&2
  refresh_gateway
  docker compose -p "$PROJECT" -f "$INSTANCE/compose.json" up -d
  docker compose -p "$PROJECT" -f "$INSTANCE/compose.json" restart gateway service
fi

ready=0
for i in $(seq 1 60); do
  if command -v curl >/dev/null 2>&1 && curl -fsS --noproxy '*' --max-time 3 "http://127.0.0.1:${PORT}/health/ready" >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  echo '容器已启动，但健康检查未通过；请检查 docker compose ps 与日志。' >&2
  exit 1
fi

echo "完成：本机 http://127.0.0.1:$PORT/"
echo "完成：局域网 http://$SELECTED:$PORT/"
echo '重新执行同一脚本会再次检测地址；IP未变时不会改写Keycloak或结束现有会话。'
