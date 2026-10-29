#!/usr/bin/env bash
# 企业办公服务统一管理入口：启动、停止、重启、状态、检查、地址与日志。
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTIVE_POINTER="$ROOT/runtime/.active-server-instance"
INSTANCE="${OFFICE_INSTANCE_DIR:-}"
REQUESTED_IP=""
COMMAND=""
LOG_SERVICE="service"
LOG_TAIL=100
FOLLOW=0
HELP=0
WAIT_SECONDS=120
LOCK_DIR="$ROOT/runtime/.manage.lock"

section() {
  printf '\n%s\n' "$1"
  printf '============================================================\n'
}

step() {
  printf '\n[%s] %s\n' "$1" "$2"
}

ok() { printf '  [正常] %s\n' "$*" ; }
note() { printf '  [信息] %s\n' "$*" ; }
warn() { printf '  [注意] %s\n' "$*" >&2 ; }
bad() { printf '  [失败] %s\n' "$*" >&2 ; }

main_help() {
  cat <<'EOF'
企业办公服务管理脚本

用法
  ./manage.sh <命令> [选项]
  ./manage.sh help [命令]

命令
  start       启动 Docker、同步登录地址、启动全部服务并等待健康
  stop        停止全部容器；保留镜像、配置、数据库和卷
  restart     完整重启；HTTP验收实例IP变化时先安全迁址
  status      只读查看实例、Docker、容器、地址、端口和健康状态
  check       只读检查配置、容器、监听、健康及OIDC入口；异常返回非零
  address     查看活动内网IPv4和当前登录主地址；多个地址时交互选择
  logs        查看日志，默认service最近100行
  instances   列出发现的服务器实例及活动标记

选项
  --instance <目录>  指定实例；也可用环境变量 OFFICE_INSTANCE_DIR
  --ip <IPv4>        指定当前活动的登录主地址，start/restart/address可用
  --service <名称>   logs服务：service|gateway|identity|postgres，默认service
  --tail <行数>      logs显示行数，默认100
  --follow           logs持续跟随
  --timeout <秒>     启动、重启与健康等待上限，默认120
  -h, --help         查看总帮助；命令后加--help查看该命令说明

自动选择实例
  首次运行会优先选择正在运行的服务器实例，并记录到 runtime/.active-server-instance。
  记录不存在且尚未运行时，只有一个已安装实例则自动选择；多个实例会要求选择。

地址策略
  gateway监听lan，即0.0.0.0，所有当前内网接口均接收连接。
  Keycloak一个realm只维持一个规范登录地址；非主地址由gateway跳转到主地址。
  HTTP验收实例发现IP变化时，会备份数据库、更新Keycloak并结束旧来源会话，员工需重新登录。
  正式HTTPS实例不改写稳定公开地址，start/restart只恢复服务。

退出码
  0 成功；1 健康或检查失败；2 参数、实例或Docker环境错误

只读边界
  status、check、address、instances、logs不写活动实例文件，不会启动或重启服务。
  start、stop、restart使用本机目录锁，拒绝并发修改；正常退出或中断时释放锁。

示例
  ./manage.sh status
  ./manage.sh check
  ./manage.sh start
  ./manage.sh restart --ip 172.16.170.83
  ./manage.sh logs --service gateway --tail 200
  ./manage.sh address --instance runtime/ui-live-20261026/instance
EOF
}

command_help() {
  case "$1" in
    start)
      cat <<'EOF'
start：启动并确保服务健康
  1. 必要时启动Docker桌面引擎；
  2. 解析实例并检测活动内网IPv4；
  3. HTTP验收实例若IP变化，执行保留数据迁址；
  4. 刷新网关、启动容器并等待健康检查。
示例：./manage.sh start [--instance 目录] [--ip IPv4]
EOF
      ;;
    stop)
      cat <<'EOF'
stop：停止全部容器
  不删除镜像、配置、数据库或卷，不关闭Docker Desktop，也不修改登录地址。
示例：./manage.sh stop [--instance 目录]
EOF
      ;;
    restart)
      cat <<'EOF'
restart：完整重启
  检测IP并在需要时先执行迁址，然后重启postgres、identity、gateway和service。
示例：./manage.sh restart [--instance 目录] [--ip IPv4]
EOF
      ;;
    status)
      cat <<'EOF'
status：只读状态
  展示实例配置摘要、Docker状态、活动地址、容器状态、端口映射和健康结果。
  该命令不会启动Docker或修改配置。
示例：./manage.sh status [--instance 目录]
EOF
      ;;
    check)
      cat <<'EOF'
check：只读验收
  检查实例文件、Compose配置、核心容器、端口监听、健康端点和OIDC issuer。
  任一项失败返回1；不会自动启动或重启服务。
示例：./manage.sh check [--instance 目录]
EOF
      ;;
    address)
      cat <<'EOF'
address：地址检测
  列出活动RFC1918内网IPv4、当前配置地址和脚本可选择的主地址。
  多个地址时交互选择；--ip只接受当前活动地址。
示例：./manage.sh address [--ip IPv4] [--instance 目录]
EOF
      ;;
    logs)
      cat <<'EOF'
logs：查看容器日志
  --service service|gateway|identity|postgres，默认service
  --tail 行数，默认100；--follow持续跟随。
示例：./manage.sh logs --service gateway --tail 200
EOF
      ;;
    instances)
      cat <<'EOF'
instances：列出实例
  扫描runtime下的有效服务器实例，显示活动标记和网关运行状态。
示例：./manage.sh instances
EOF
      ;;
    *) main_help ;;
  esac
}

if [[ "${1:-}" == help ]]; then
  command_help "${2:-}"
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance) INSTANCE="${2:?--instance需要目录}"; shift 2 ;;
    --ip) REQUESTED_IP="${2:?--ip需要IPv4}"; shift 2 ;;
    --service) LOG_SERVICE="${2:?--service需要名称}"; shift 2 ;;
    --tail) LOG_TAIL="${2:?--tail需要行数}"; shift 2 ;;
    --follow) FOLLOW=1; shift ;;
    --timeout) WAIT_SECONDS="${2:?--timeout需要秒数}"; shift 2 ;;
    -h|--help) HELP=1; shift ;;
    --*) echo "未知选项：$1" >&2; main_help >&2; exit 2 ;;
    *)
      if [[ -z "$COMMAND" ]]; then COMMAND="$1"; else echo "多余参数：$1" >&2; exit 2; fi
      shift
      ;;
  esac
done

if [[ "$HELP" == 1 ]]; then
  command_help "$COMMAND"
  exit 0
fi
if [[ -z "$COMMAND" ]]; then main_help; exit 0; fi
case "$COMMAND" in
  start|stop|restart|status|check|address|logs|instances) ;;
  *) echo "未知命令：$COMMAND" >&2; main_help >&2; exit 2 ;;
esac
[[ "$WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo '--timeout须为正整数秒' >&2; exit 2; }

probe_docker() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 6 docker info >/dev/null 2>&1
    return
  fi
  docker info >/dev/null 2>&1 &
  local pid=$!
  local remaining=12
  while (( remaining > 0 )); do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid"
      return
    fi
    sleep 0.25
    remaining=$((remaining - 1))
  done
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  return 1
}

release_lock() {
  [[ -d "$LOCK_DIR" ]] || return 0
  if [[ -f "$LOCK_DIR/pid" && "$(cat "$LOCK_DIR/pid" 2>/dev/null || true)" == "$$" ]]; then
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
}

acquire_lock() {
  mkdir -p "$ROOT/runtime"
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" >"$LOCK_DIR/pid"
    trap release_lock EXIT INT TERM
    return 0
  fi
  local holder
  holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ "$holder" =~ ^[0-9]+$ ]] && ! kill -0 "$holder" 2>/dev/null; then
    warn "发现失效管理锁（PID ${holder}），正在清理"
    if mv "$LOCK_DIR" "$LOCK_DIR.stale.$$"; then
      rm -f "$LOCK_DIR.stale.$$/pid"
      rmdir "$LOCK_DIR.stale.$$" 2>/dev/null || true
      if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" >"$LOCK_DIR/pid"
        trap release_lock EXIT INT TERM
        return 0
      fi
    fi
  fi
  bad "已有管理操作正在执行${holder:+（PID ${holder}）}；请等待结束"
  return 2
}

ensure_docker() {
  probe_docker && return 0
  echo 'Docker引擎未响应，正在尝试启动…' >&2
  if [[ "$(uname -s)" == Darwin ]]; then
    open -a Docker
  elif command -v systemctl >/dev/null 2>&1 && [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    systemctl start docker
  fi
  local remaining=12
  while (( remaining > 0 )); do
    probe_docker && { ok 'Docker引擎已就绪'; return 0; }
    sleep 1
    remaining=$((remaining - 1))
  done
  if [[ "$(uname -s)" == Darwin ]]; then
    echo 'Docker Desktop未恢复，重启其桌面进程（保留镜像、容器和卷）…' >&2
    ps ax -o pid=,command= | awk '/\/Applications\/Docker.app\/Contents\/MacOS\/(Docker Desktop|com.docker.backend|com.docker.build)/ {print $1}' | xargs kill -TERM 2>/dev/null || true
    remaining=20
    while (( remaining > 0 )); do
      pgrep -f '/Applications/Docker.app/Contents/MacOS/com.docker.backend' >/dev/null || break
      sleep 0.5
      remaining=$((remaining - 1))
    done
    ps ax -o pid=,command= | awk '/\/Applications\/Docker.app\/Contents\/MacOS\/(Docker Desktop|com.docker.backend|com.docker.build)/ {print $1}' | xargs kill -KILL 2>/dev/null || true
    open -a Docker
    remaining=30
    while (( remaining > 0 )); do
      probe_docker && { ok 'Docker引擎已重新启动'; return 0; }
      sleep 1
      remaining=$((remaining - 1))
    done
  fi
  bad 'Docker引擎仍未就绪；请查看Docker Desktop故障处理页面'
  return 2
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
      case "$iface" in lo*|utun*|gif*|stf*|awdl*|llw*|bridge*|ap*) continue ;; esac
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
  local iface ip
  active_addresses | while IFS='|' read -r iface ip; do
    if is_private_ipv4 "$ip"; then printf '%s|%s\n' "$iface" "$ip"; fi
  done | sort -u
}

instance_candidates() {
  find "$ROOT/runtime" -type f -name installation.json \
    ! -path '*/relocations/*' ! -path '*/.office/*' -print 2>/dev/null | while read -r file; do
      local folder
      folder="$(dirname "$file")"
      [[ -f "$folder/compose.json" && -f "$folder/service/service.json" ]] || continue
      grep -q '"project": "office-' "$file" && printf '%s\n' "$folder"
    done | sort -u
}

valid_instance() {
  local folder="$1"
  [[ ! -L "$folder" && -d "$folder" && -f "$folder/installation.json" && -f "$folder/compose.json" && -f "$folder/service/service.json" ]]
}

record_instance() {
  mkdir -p "$ROOT/runtime"
  printf '%s\n' "$INSTANCE" >"$ACTIVE_POINTER.$$"
  chmod 600 "$ACTIVE_POINTER.$$"
  mv "$ACTIVE_POINTER.$$" "$ACTIVE_POINTER"
}

resolve_instance() {
  local record="${1:-0}"
  local docker_ready=0
  probe_docker && docker_ready=1
  if valid_instance "$INSTANCE"; then
    INSTANCE="$(cd "$INSTANCE" && pwd)"
    [[ "$record" == 1 ]] && record_instance
    return 0
  fi
  if [[ -f "$ACTIVE_POINTER" ]]; then
    INSTANCE="$(sed -n '1p' "$ACTIVE_POINTER")"
    if valid_instance "$INSTANCE"; then
      INSTANCE="$(cd "$INSTANCE" && pwd)"
      return 0
    fi
  fi
  local instances running_folder folder project
  instances="$(instance_candidates)"
  if [[ "$docker_ready" == 1 ]]; then
    while read -r folder; do
      [[ -n "$folder" ]] || continue
      project="$(sed -n 's/^  "project": "\([^"]*\)",$/\1/p' "$folder/installation.json" | head -n 1)"
      if [[ -n "$project" ]] && docker ps --filter "name=${project}-gateway-1" --format '{{.Names}}' | grep -qx "${project}-gateway-1"; then
        if [[ -n "$running_folder" ]]; then running_folder=""; break; fi
        running_folder="$folder"
      fi
    done <<<"$instances"
    if [[ -n "$running_folder" ]]; then
      INSTANCE="$(cd "$running_folder" && pwd)"
      [[ "$record" == 1 ]] && record_instance
      return 0
    fi
  fi
  local count
  count="$(printf '%s\n' "$instances" | awk 'NF{n++} END{print n+0}')"
  if [[ "$count" -eq 1 ]]; then
    INSTANCE="$(cd "$instances" && pwd)"
    [[ "$record" == 1 ]] && record_instance
    return 0
  elif [[ "$count" -eq 0 ]]; then
    bad '未找到已安装的服务器实例；请用--instance指定'
    return 2
  fi
  if [[ ! -t 0 ]]; then
    bad '检测到多个服务器实例；非交互环境请用--instance指定'
    return 2
  fi
  echo '检测到多个服务器实例，请选择：' >&2
  local n=0
  while read -r folder; do
    [[ -n "$folder" ]] || continue
    n=$((n + 1)); echo "  $n) $folder" >&2
  done <<<"$instances"
  local choice
  read -r -p '实例编号 [1]: ' choice
  choice="${choice:-1}"
  INSTANCE="$(printf '%s\n' "$instances" | sed -n "${choice}p")"
  valid_instance "$INSTANCE" || return 2
  INSTANCE="$(cd "$INSTANCE" && pwd)"
  [[ "$record" == 1 ]] && record_instance
  return 0
}

load_instance() {
  resolve_instance "${1:-0}" || return 2
  IFS=$'\t' read -r PROJECT IMAGE PUBLIC_URL PORT DEMO CURRENT_HOST <<<"$(json_marker "$INSTANCE/installation.json")"
  if [[ -z "$PROJECT" || -z "$IMAGE" || -z "$PORT" ]]; then
    bad '实例配置缺少项目、镜像或端口'
    return 2
  fi
}

compose() {
  docker compose -p "$PROJECT" -f "$INSTANCE/compose.json" "$@"
}

refresh_gateway() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$ROOT/deploy/installer/refresh_gateway.py" "$INSTANCE"
  else
    docker run --rm --network none --user 0:0 \
      -v "$ROOT:$ROOT:ro" -v "$INSTANCE:$INSTANCE" "$IMAGE" \
      python "$ROOT/deploy/installer/refresh_gateway.py" "$INSTANCE"
  fi
}

current_candidates() {
  CANDIDATES="$(collect_candidates)"
  CANDIDATE_COUNT="$(printf '%s\n' "$CANDIDATES" | awk -F'|' 'NF==2{n++} END{print n+0}')"
}

address_is_active() {
  printf '%s\n' "$CANDIDATES" | awk -F'|' -v ip="$1" '$2==ip{found=1} END{exit found?0:1}'
}

choose_address() {
  current_candidates
  if [[ "$CANDIDATE_COUNT" -eq 0 ]]; then
    bad '未检测到可用的RFC1918内网IPv4地址'
    return 2
  fi
  if [[ -n "$REQUESTED_IP" ]]; then
    is_private_ipv4 "$REQUESTED_IP" || { bad "--ip不是内网IPv4：$REQUESTED_IP"; return 2; }
    address_is_active "$REQUESTED_IP" || { bad "--ip不在当前活动地址中：$REQUESTED_IP"; return 2; }
    SELECTED="$REQUESTED_IP"
  elif [[ "$DEMO" == 1 ]] && address_is_active "$CURRENT_HOST"; then
    SELECTED="$CURRENT_HOST"
  elif [[ "$CANDIDATE_COUNT" -eq 1 ]]; then
    SELECTED="$(printf '%s\n' "$CANDIDATES" | awk -F'|' 'NF==2{print $2}')"
  elif [[ -t 0 ]]; then
    echo '检测到多个活动内网地址。gateway会监听全部地址，但Keycloak登录只能固定一个主地址：' >&2
    local n=0 iface ip
    while IFS='|' read -r iface ip; do
      [[ -n "$ip" ]] || continue
      n=$((n + 1)); echo "  $n) $ip  ($iface)" >&2
    done <<<"$CANDIDATES"
    local choice
    read -r -p '请选择员工登录主地址编号 [1]: ' choice
    choice="${choice:-1}"
    SELECTED="$(printf '%s\n' "$CANDIDATES" | awk -F'|' -v n="$choice" 'NR==n{print $2}')"
  else
    bad '检测到多个活动内网地址；非交互环境请用--ip指定登录主地址'
    return 2
  fi
  [[ -n "$SELECTED" ]] || { bad '未选择有效登录地址'; return 2; }
}

sync_address() {
  if [[ "$DEMO" != 1 ]]; then
    echo "  稳定公开地址：$PUBLIC_URL"
    return 0
  fi
  choose_address || return 2
  echo "  激活地址：$(printf '%s\n' "$CANDIDATES" | awk -F'|' '{printf "%s%s(%s)",sep,$2,$1;sep=", "}')"
  echo "  当前地址：$CURRENT_HOST"
  echo "  登录主地址：$SELECTED"
  if [[ "$SELECTED" != "$CURRENT_HOST" ]]; then
    warn "HTTP实例地址由 $CURRENT_HOST 变为 ${SELECTED}，执行保留数据迁址"
    bash "$ROOT/deploy/relocate.sh" "$INSTANCE" \
      --url "http://$SELECTED:$PORT" --listen lan --image "$IMAGE" --apply
  fi
}

health_once() {
  if command -v curl >/dev/null 2>&1; then
    if [[ "$DEMO" == 1 ]]; then
      curl -fsS --noproxy '*' --max-time 3 "http://$SELECTED:$PORT/health/ready" >/dev/null 2>&1
    else
      if [[ -f "$INSTANCE/service/ca.pem" ]]; then
        curl -fsS --noproxy '*' --max-time 3 --cacert "$INSTANCE/service/ca.pem" \
          "$PUBLIC_URL/health/ready" >/dev/null 2>&1
      else
        curl -fsS --noproxy '*' --max-time 3 "$PUBLIC_URL/health/ready" >/dev/null 2>&1
      fi
    fi
    return
  fi
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    "$PROJECT-service-1" 2>/dev/null | grep -qx 'healthy\|running'
}

wait_health() {
  local remaining="$WAIT_SECONDS"
  while (( remaining > 0 )); do
    if health_once; then ok '健康检查通过'; return 0; fi
    sleep 1
    remaining=$((remaining - 1))
  done
  bad '健康检查未通过；请查看容器状态和服务日志'
  return 1
}

all_services_running() {
  local services name
  services="$(compose ps --status running --services 2>/dev/null || true)"
  for name in postgres identity gateway service; do
    printf '%s\n' "$services" | grep -qx "$name" || return 1
  done
}

start_stack() {
  step '1/4' '同步服务器地址'
  sync_address || return 2
  step '2/4' '刷新网关配置'
  local gateway_change
  gateway_change="$(refresh_gateway)"
  if all_services_running; then
    if [[ "$gateway_change" == updated ]] || ! health_once; then
      step '3/4' '重载网关与服务'
      compose restart gateway service >/dev/null
    else
      step '3/4' '服务已运行'
      ok '无需重启，当前实例已就绪'
    fi
    step '4/4' '核验就绪状态'
    wait_health || return 1
    if [[ "$DEMO" == 1 ]]; then
      printf '\n服务已就绪：http://%s:%s/\n' "$SELECTED" "$PORT"
    else
      printf '\n服务已就绪：%s\n' "$PUBLIC_URL"
    fi
    return 0
  fi
  step '3/4' '启动或补齐容器'
  compose up -d --wait --wait-timeout "$WAIT_SECONDS" || return 2
  if [[ "$gateway_change" == updated ]]; then compose restart gateway >/dev/null; fi
  step '4/4' '核验就绪状态'
  wait_health || return 1
  if [[ "$DEMO" == 1 ]]; then
    printf '\n服务已启动\n'
    echo "  本机入口：http://localhost:$PORT/（自动转到登录主地址）"
    echo "  局域网入口：http://$SELECTED:$PORT/"
  else
    printf '\n服务已启动：%s\n' "$PUBLIC_URL"
  fi
}

stop_stack() {
  if ! probe_docker; then
    warn 'Docker未运行，容器已经处于停止状态'
    return 0
  fi
  step '1/1' '停止全部容器'
  compose stop --timeout 20 || return 2
  printf '\n已停止；数据、配置、镜像和卷均已保留。\n'
}

restart_stack() {
  step '1/4' '同步服务器地址'
  sync_address || return 2
  step '2/4' '刷新网关配置'
  refresh_gateway >/dev/null
  step '3/4' '重启全部容器'
  compose restart --timeout 20 || return 2
  compose up -d --wait --wait-timeout "$WAIT_SECONDS" || return 2
  step '4/4' '核验就绪状态'
  wait_health || return 1
  if [[ "$DEMO" == 1 ]]; then
    printf '\n服务已重启：http://%s:%s/\n' "$SELECTED" "$PORT"
  else
    printf '\n服务已重启：%s\n' "$PUBLIC_URL"
  fi
}

show_status() {
  local docker_ready=0 health=fail
  probe_docker && docker_ready=1
  section '实例'
  note "目录：$INSTANCE"
  note "项目：$PROJECT"
  note "模式：$([[ "$DEMO" == 1 ]] && echo 'HTTP验收' || echo 'HTTPS')"
  note "公开地址：$PUBLIC_URL"
  note "端口：${PORT}；监听：lan（0.0.0.0）"
  current_candidates
  section '服务器地址'
  note "当前配置：$CURRENT_HOST"
  if [[ "$CANDIDATE_COUNT" -gt 0 ]]; then
    while IFS='|' read -r iface ip; do [[ -n "$ip" ]] && note "检测到：${ip}（${iface}）"; done <<<"$CANDIDATES"
  else
    warn '未检测到RFC1918内网IPv4'
  fi
  section 'Docker与容器'
  if [[ "$docker_ready" == 0 ]]; then
    printf '  [失败] Docker引擎未运行或未响应\n'
  else
    ok 'Docker引擎运行中'
    compose ps -a --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}\t{{.Ports}}' || warn '无法读取容器状态'
    if [[ "$DEMO" == 1 ]]; then SELECTED="$CURRENT_HOST"; else SELECTED="$CURRENT_HOST"; fi
    if health_once; then health=ok; ok '健康端点可用'; else printf '  [注意] 健康端点不可用\n'; fi
    local binding
    binding="$(docker port "$PROJECT-gateway-1" "$PORT/tcp" 2>/dev/null || true)"
    if [[ "$binding" == "0.0.0.0:$PORT" ]]; then ok "gateway发布：$binding"; else printf '  [注意] gateway发布：%s\n' "${binding:-未运行}"; fi
  fi
  printf '\n'
  [[ "$docker_ready" == 1 && "$health" == ok ]]
}

run_check() {
  local failures=0 binding issuer expected_issuer issuer_url
  section '检查结果'
  if [[ -f "$INSTANCE/installation.json" && -f "$INSTANCE/compose.json" ]]; then
    ok '实例配置文件存在'
  else
    bad '实例配置文件缺失'
    failures=$((failures+1))
  fi
  if ! probe_docker; then bad 'Docker引擎不可用'; return 1; fi
  ok 'Docker引擎可用'
  if compose config --quiet >/dev/null 2>&1; then ok 'Compose配置有效'; else bad 'Compose配置无效'; failures=$((failures+1)); fi
  local name
  for name in postgres identity gateway service; do
    if compose ps --status running --services | grep -qx "$name"; then ok "容器运行：$name"; else bad "容器未运行：$name"; failures=$((failures+1)); fi
  done
  binding="$(docker port "$PROJECT-gateway-1" "$PORT/tcp" 2>/dev/null || true)"
  if [[ "$binding" == "0.0.0.0:$PORT" ]]; then ok "端口监听：$binding"; else bad "端口监听异常：${binding:-无}"; failures=$((failures+1)); fi
  SELECTED="$CURRENT_HOST"
  if health_once; then ok '健康端点返回200'; else bad '健康端点不可用'; failures=$((failures+1)); fi
  if [[ "$DEMO" == 1 ]]; then
    issuer_url="http://$CURRENT_HOST:$PORT/identity/realms/office/.well-known/openid-configuration"
    expected_issuer="http://$CURRENT_HOST:$PORT/identity/realms/office"
  else
    issuer_url="$PUBLIC_URL/identity/realms/office/.well-known/openid-configuration"
    expected_issuer="$PUBLIC_URL/identity/realms/office"
  fi
  if [[ "$DEMO" == 1 || ! -f "$INSTANCE/service/ca.pem" ]]; then
    issuer="$(curl -fsS --noproxy '*' --max-time 5 "$issuer_url" 2>/dev/null | sed -n 's/.*"issuer"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
  else
    issuer="$(curl -fsS --noproxy '*' --max-time 5 --cacert "$INSTANCE/service/ca.pem" "$issuer_url" 2>/dev/null | sed -n 's/.*"issuer"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
  fi
  if [[ "$issuer" == "$expected_issuer" ]]; then ok "OIDC issuer：$issuer"; else bad "OIDC issuer不一致：${issuer:-未读取}"; failures=$((failures+1)); fi
  if [[ "$failures" -eq 0 ]]; then printf '\n检查通过。\n'; return 0; fi
  printf '\n检查失败：%s 项。\n' "$failures" >&2
  return 1
}

show_instances() {
  local folders folder project state active
  folders="$(instance_candidates)"
  section '服务器实例'
  active=""
  [[ -f "$ACTIVE_POINTER" ]] && active="$(sed -n '1p' "$ACTIVE_POINTER")"
  if [[ -z "$folders" ]]; then warn '未发现服务器实例'; return 0; fi
  while read -r folder; do
    [[ -n "$folder" ]] || continue
    project="$(sed -n 's/^  "project": "\([^"]*\)",$/\1/p' "$folder/installation.json" | head -n 1)"
    state='未运行'
    if probe_docker && docker ps --filter "name=${project}-gateway-1" --format '{{.Names}}' | grep -qx "${project}-gateway-1"; then state='运行中'; fi
    local mark=' '
    [[ "$folder" == "$active" ]] && mark='*'
    printf '  %s %-10s %s\n' "$mark" "$state" "$folder"
  done <<<"$folders"
}

show_addresses() {
  current_candidates
  section '地址检测'
  note "实例：$INSTANCE"
  note "当前配置：$CURRENT_HOST"
  if [[ "$CANDIDATE_COUNT" -gt 0 ]]; then
    while IFS='|' read -r iface ip; do [[ -n "$ip" ]] && note "活动地址：${ip}（${iface}）"; done <<<"$CANDIDATES"
  else
    warn '未检测到RFC1918内网IPv4'
  fi
  if [[ -n "$REQUESTED_IP" ]]; then
    if is_private_ipv4 "$REQUESTED_IP" && address_is_active "$REQUESTED_IP"; then
      ok "指定地址可用：$REQUESTED_IP"
    else
      bad "指定地址不可用：$REQUESTED_IP"
      return 2
    fi
  elif [[ "$DEMO" != 1 ]]; then
    note '正式实例不按IP改写公开地址'
  elif address_is_active "$CURRENT_HOST"; then
    ok "继续使用当前登录主地址：$CURRENT_HOST"
  elif [[ "$CANDIDATE_COUNT" -eq 1 ]]; then
    ok "start/restart将自动选择唯一地址"
  elif [[ -t 0 ]]; then
    choose_address || return 2
    ok "选择的主地址：${SELECTED}（仅本次展示，不写配置）"
  else
    warn '多个地址且旧地址已消失；非交互环境请用--ip指定'
  fi
}

show_logs() {
  case "$LOG_SERVICE" in service|gateway|identity|postgres) ;; *) bad "日志服务无效：$LOG_SERVICE"; return 2 ;; esac
  [[ "$LOG_TAIL" =~ ^[0-9]+$ ]] || { bad '--tail需为非负整数'; return 2; }
  if ! probe_docker; then bad 'Docker引擎不可用'; return 2; fi
  section "日志：${LOG_SERVICE}（最近${LOG_TAIL}行）"
  if [[ "$FOLLOW" == 1 ]]; then compose logs --tail "$LOG_TAIL" --follow --no-color "$LOG_SERVICE"; else compose logs --tail "$LOG_TAIL" --no-color "$LOG_SERVICE"; fi
}

case "$COMMAND" in
  instances)
    show_instances
    ;;
  start|restart)
    acquire_lock || exit 2
    ensure_docker || exit 2
    load_instance 1 || exit 2
    if [[ "$COMMAND" == start ]]; then
      start_stack
    else
      restart_stack
    fi
    ;;
  stop)
    acquire_lock || exit 2
    load_instance 1 || exit 2
    stop_stack
    ;;
  status)
    load_instance 0 || exit 2
    show_status
    ;;
  check)
    load_instance 0 || exit 2
    run_check
    ;;
  address)
    load_instance 0 || exit 2
    show_addresses
    ;;
  logs)
    load_instance 0 || exit 2
    show_logs
    ;;
esac
