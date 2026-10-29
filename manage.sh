#!/usr/bin/env bash
# 企业办公服务统一管理入口：启动、停止、重启、状态、检查、地址与日志。
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGE_CONFIG_FILE="${OFFICE_MANAGE_CONFIG:-$ROOT/deploy/manage.env}"
case "$MANAGE_CONFIG_FILE" in
  /*) ;;
  *) MANAGE_CONFIG_FILE="$ROOT/$MANAGE_CONFIG_FILE" ;;
esac
RUNTIME_DIR="$ROOT/runtime"
ACTIVE_POINTER=""
DEFAULT_BACKUP_ROOT=""
DOCKER_APP_NAME="Docker"
INSTANCE=""
REQUESTED_IP=""
CONFIRM_TOKEN=""
BACKUP_OUTPUT=""
COPY_TARGET=""
KEEP_COUNT=""
BUILD_AGENT=""
BUILD_TAG=""
INSTALL_CONFIG=""
INSTALL_CONFIGURE=0
UPGRADE_IMAGE=""
COMMAND=""
PLAN_TARGET=""
LOG_SERVICE="service"
LOG_TAIL=100
FOLLOW=0
HELP=0
WAIT_SECONDS=120
LOCK_DIR=""

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

manage_path() {
  local value="$1" base="$2" fallback="$3"
  [[ -n "$value" ]] || value="$fallback"
  [[ -n "$value" ]] || return 0
  case "$value" in
    /*) printf '%s' "$value" ;;
    *) printf '%s/%s' "$base" "$value" ;;
  esac
}

normalize_path() {
  local p="$1"
  while [[ "$p" == */ && "$p" != / ]]; do p="${p%/}"; done
  printf '%s' "$p"
}

load_manage_config() {
  local file="$MANAGE_CONFIG_FILE" line key value seen=" "
  local cfg_timeout='' cfg_runtime='' cfg_backup='' cfg_active='' cfg_lock='' cfg_docker='' cfg_instance=''
  if [[ -e "$file" ]]; then
    [[ -f "$file" && ! -L "$file" ]] || { echo "管理配置必须是普通文件且不能是软链：$file" >&2; return 2; }
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      line="${line#"${line%%[![:space:]]*}"}"
      line="${line%"${line##*[![:space:]]}"}"
      [[ -z "$line" || "$line" == \#* ]] && continue
      [[ "$line" == *=* ]] || { echo "管理配置行格式无效：$file" >&2; return 2; }
      key="${line%%=*}"; value="${line#*=}"
      [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "管理配置键格式无效：$key" >&2; return 2; }
      [[ "$seen" != *" $key "* ]] || { echo "管理配置键重复：$key" >&2; return 2; }
      seen="${seen}${key} "
      local quote="${value:0:1}" body rest
      case "$quote" in
        \'|\")
          body="${value:1}"
          case "$quote" in
            \') body="${body%%\'*}" ;;
            \") body="${body%%\"*}" ;;
          esac
          rest="${value:$((1 + ${#body}))}"
          [[ "$rest" == "$quote"* ]] || { echo "管理配置引号未闭合：$key" >&2; return 2; }
          rest="${rest:1}"
          rest="${rest#"${rest%%[![:space:]]*}"}"
          [[ -z "$rest" || "$rest" == \#* ]] \
            || { echo "管理配置引号后存在多余内容：$key" >&2; return 2; }
          value="$body"
          ;;
        *)
          [[ "$value" == *' #'* ]] && value="${value%% #*}"
          value="${value%"${value##*[![:space:]]}"}"
          ;;
      esac
      [[ "$value" != *'$'* && "$value" != *'`'* && "$value" != *$'\n'* && "$value" != *$'\r'* ]] \
        || { echo "管理配置只接受普通字面量：$key" >&2; return 2; }
      case "$key" in
        OFFICE_MANAGE_RUNTIME_DIR|OFFICE_MANAGE_BACKUP_DIR|OFFICE_MANAGE_ACTIVE_INSTANCE|OFFICE_MANAGE_LOCK_DIR|OFFICE_MANAGE_INSTANCE_DIR)
          [[ "$value" != *..* ]] || { echo "管理配置路径不得包含父目录跳转：$key" >&2; return 2; } ;;
      esac
      case "$key" in
        OFFICE_MANAGE_TIMEOUT) cfg_timeout="$value" ;;
        OFFICE_MANAGE_RUNTIME_DIR) cfg_runtime="$value" ;;
        OFFICE_MANAGE_BACKUP_DIR) cfg_backup="$value" ;;
        OFFICE_MANAGE_ACTIVE_INSTANCE) cfg_active="$value" ;;
        OFFICE_MANAGE_LOCK_DIR) cfg_lock="$value" ;;
        OFFICE_MANAGE_DOCKER_APP) cfg_docker="$value" ;;
        OFFICE_MANAGE_INSTANCE_DIR) cfg_instance="$value" ;;
        *) echo "管理配置包含未知键：$key" >&2; return 2 ;;
      esac
    done <"$file"
  elif [[ -n "${OFFICE_MANAGE_CONFIG:-}" ]]; then
    echo "指定的管理配置不存在：$file" >&2; return 2
  fi
  WAIT_SECONDS="${OFFICE_MANAGE_TIMEOUT:-${cfg_timeout:-120}}"
  [[ "$WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]] \
    || { echo "管理等待秒数必须是正整数：$WAIT_SECONDS" >&2; return 2; }
  RUNTIME_DIR="$(normalize_path "$(manage_path "${OFFICE_MANAGE_RUNTIME_DIR:-$cfg_runtime}" "$ROOT" runtime)")"
  DEFAULT_BACKUP_ROOT="$(normalize_path "$(manage_path "${OFFICE_MANAGE_BACKUP_DIR:-$cfg_backup}" "$RUNTIME_DIR" backups)")"
  ACTIVE_POINTER="$(normalize_path "$(manage_path "${OFFICE_MANAGE_ACTIVE_INSTANCE:-$cfg_active}" "$RUNTIME_DIR" .active-server-instance)")"
  LOCK_DIR="$(normalize_path "$(manage_path "${OFFICE_MANAGE_LOCK_DIR:-$cfg_lock}" "$RUNTIME_DIR" .manage.lock)")"
  DOCKER_APP_NAME="${OFFICE_MANAGE_DOCKER_APP:-${cfg_docker:-Docker}}"
  INSTANCE="${OFFICE_INSTANCE_DIR:-$(manage_path "$cfg_instance" "$ROOT" "")}"
  [[ "$RUNTIME_DIR" != "/" && "$RUNTIME_DIR" != "$ROOT" ]] \
    || { echo "运行目录不能是根目录或项目根目录" >&2; return 2; }
  case "$ACTIVE_POINTER" in
    "$RUNTIME_DIR/"*) ;;
    *) echo "活动实例文件必须位于运行目录内" >&2; return 2 ;;
  esac
  case "$LOCK_DIR" in
    "$RUNTIME_DIR/"*) ;;
    *) echo "管理锁必须位于运行目录内" >&2; return 2 ;;
  esac
  [[ "$DEFAULT_BACKUP_ROOT" != "/" ]] || { echo "备份目录不能是根目录" >&2; return 2; }
  [[ "$DOCKER_APP_NAME" != */* && -n "$DOCKER_APP_NAME" ]] \
    || { echo "Docker应用名不能为空或包含路径分隔符" >&2; return 2; }
}

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
  plan        只读预览down或recreate的影响，不执行修改
  recreate    强制重建无状态gateway/service，数据库卷和身份数据保留
  down        删除容器与项目网络，数据库卷、镜像、配置和密钥保留
  backup      备份业务库、身份库、配置和秘密到受限制目录
  prune       只删除本项目旧备份，保留最新N份；须预览并确认
  install     在新环境执行现有安装器；未指定OFFICE_IMAGE时由安装器构建
  build       从本地源码构建普通或Agent镜像，不自动安装或升级
  upgrade     使用已存在且已验证的镜像执行备份、迁移和受控升级
  config      展示生效的管理配置和路径，不输出秘密

高级操作
  recreate、down与upgrade必须先用plan/预览确认影响，再输入精确确认串。
  不提供purge、delete、delete-data或uninstall；任何删除卷、镜像、实例密钥的请求都会被拒绝。

选项
  --instance <目录>  指定实例；也可用环境变量 OFFICE_INSTANCE_DIR
  --ip <IPv4>        指定当前活动的登录主地址，start/restart/address可用
  --service <名称>   logs服务：service|gateway|identity|postgres，默认service
  --tail <行数>      logs显示行数，默认100
  --follow           logs持续跟随
  --timeout <秒>     启动、重启与健康等待上限，默认120
  --confirm <值>     down/recreate/upgrade确认串
  --output <目录>    backup父目录，必须是绝对路径；默认使用管理配置
  --copy-to <目录>   backup完成后复制到第二个绝对目录并校验摘要
  --keep <数量>      prune保留的最新备份份数，必须≥1
  --config <文件>    install使用的公开配置文件
  --configure        install先进入配置向导
  --agent <0|1>      build镜像变体
  --tag <名称>       build镜像标签
  --image <镜像>     upgrade目标镜像；必须已在本地存在
  -h, --help         查看总帮助；命令后加--help查看该命令说明

自动选择实例
  首次运行会优先选择正在运行的服务器实例，并记录到受配置的活动实例文件。
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
  ./manage.sh plan down
  ./manage.sh down --confirm office-example:down
  ./manage.sh plan recreate
  ./manage.sh recreate --confirm office-example:recreate
  ./manage.sh backup --output /secure/backups
  ./manage.sh backup --copy-to /mnt/offsite/backups
  ./manage.sh plan prune --keep 10
  ./manage.sh prune --keep 10 --confirm office-example:prune:10
  ./manage.sh install
  ./manage.sh build --agent 1 --tag office-data-service:local-agent
  ./manage.sh upgrade --image office-data-service:local-agent \
    --confirm office-example:upgrade:<镜像摘要前12位>
  ./manage.sh start
  ./manage.sh restart --ip 192.0.2.10
  ./manage.sh logs --service gateway --tail 200
  ./manage.sh address --instance runtime/instance
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
    plan)
      cat <<'EOF'
plan：只读预览危险维护动作
  用法：./manage.sh plan down|recreate [--instance 目录]
  显示将删除的容器与网络、必须保留的数据库卷、确认串和恢复方式。
  不删除容器、卷、镜像或文件，不写活动实例记录。
EOF
      ;;
    recreate)
      cat <<'EOF'
recreate：保留数据重建无状态容器
  默认仅重建gateway和service；postgres与identity容器保持运行。
  数据库卷、身份数据、配置、密钥和镜像全部保留。
  先执行plan recreate，再输入“项目名:recreate”或传--confirm。
示例：./manage.sh recreate --confirm office-example:recreate
EOF
      ;;
    down)
      cat <<'EOF'
down：删除容器和项目网络
  数据库卷、镜像、实例配置、秘密、审计和备份全部保留。
  命令固定不传-v/--volumes，也不删除镜像。
  先执行plan down，再输入“项目名:down”或传--confirm。
示例：./manage.sh down --confirm office-example:down
EOF
      ;;
    backup)
      cat <<'EOF'
backup：创建受限制的完整实例备份
  备份业务库、身份库、实例配置、网关配置和必要秘密。
  默认写入管理配置中的备份目录；--output可指定其他绝对目录。
  不删除旧备份，不执行保留策略。
  --copy-to把完成的备份复制到第二个目录（如挂载的异地盘）并复核SHA-256。
示例：./manage.sh backup --output /secure/backups
示例：./manage.sh backup --copy-to /mnt/offsite/backups
EOF
      ;;
    prune)
      cat <<'EOF'
prune：删除本项目旧备份
  只处理备份根目录下名称严格匹配“项目-UTC时间-PID”的目录，且清单project须一致。
  永远保留最新N份；不会删除数据库卷、镜像、实例目录、审计或秘密。
  先执行plan prune --keep N，再输入“项目名:prune:N”或传--confirm。
示例：./manage.sh plan prune --keep 10
示例：./manage.sh prune --keep 10 --confirm office-example:prune:10
EOF
      ;;
    install)
      cat <<'EOF'
install：新环境安装
  调用现有deploy/install.sh。未配置OFFICE_IMAGE时，安装器从当前源码构建镜像。
  使用--configure可先进入配置向导；--config指定公开.env。
  --instance可指定首装目标实例目录，缺省用deploy/.env中的OFFICE_INSTANCE_DIR。
示例：./manage.sh install --configure
 示例：./manage.sh install --instance runtime/instance
EOF
      ;;
    build)
      cat <<'EOF'
build：本地镜像构建
  必须指定--agent 0（普通）或1（含Agent运行能力）。
  只构建镜像，不安装实例，不切换运行版本，不删除旧镜像。
示例：./manage.sh build --agent 1 --tag office-data-service:local-agent
EOF
      ;;
    upgrade)
      cat <<'EOF'
upgrade：受控镜像升级
  目标镜像必须已在本地存在；本命令不自动拉取、不自动构建。
  执行顺序：镜像与变体预检 → 完整备份 → 停写service/gateway →
  更新Compose镜像 → 数据库迁移 → 启动 → 健康及OIDC检查。
  不删除旧镜像、数据库卷、配置、秘密或备份；失败时保留停写现场。
  确认串为“项目名:upgrade:目标镜像ID前12位”。
示例：./manage.sh upgrade --image office-data-service:local-agent \
  --confirm office-example:upgrade:123456789abc
EOF
      ;;
    config)
      cat <<'EOF'
config：查看生效配置
  显示管理配置文件、运行目录、锁、活动实例、备份目录、超时和Docker应用名。
  只读，不启动容器，不输出数据库密码、模型密钥或证书内容。
示例：./manage.sh config
EOF
      ;;
    *) main_help ;;
  esac
}

if [[ "${1:-}" == help ]]; then
  command_help "${2:-}"
  exit 0
fi

case "${1:-}" in
  -h|--help) ;;
  *) load_manage_config || exit 2 ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance) INSTANCE="${2:?--instance需要目录}"; shift 2 ;;
    --ip) REQUESTED_IP="${2:?--ip需要IPv4}"; shift 2 ;;
    --service) LOG_SERVICE="${2:?--service需要名称}"; shift 2 ;;
    --tail) LOG_TAIL="${2:?--tail需要行数}"; shift 2 ;;
    --follow) FOLLOW=1; shift ;;
    --timeout) WAIT_SECONDS="${2:?--timeout需要秒数}"; shift 2 ;;
    --confirm) CONFIRM_TOKEN="${2:?--confirm需要确认串}"; shift 2 ;;
    --output) BACKUP_OUTPUT="${2:?--output需要目录}"; shift 2 ;;
    --copy-to) COPY_TARGET="${2:?--copy-to需要绝对目录}"; shift 2 ;;
    --keep) KEEP_COUNT="${2:?--keep需要保留数量}"; shift 2 ;;
    --config) INSTALL_CONFIG="${2:?--config需要文件}"; shift 2 ;;
    --configure) INSTALL_CONFIGURE=1; shift ;;
    --agent) BUILD_AGENT="${2:?--agent需要0或1}"; shift 2 ;;
    --tag) BUILD_TAG="${2:?--tag需要镜像标签}"; shift 2 ;;
    --image) UPGRADE_IMAGE="${2:?--image需要镜像引用}"; shift 2 ;;
    -v|--volumes|--rmi|--remove-images|--delete-data|--purge)
      echo "拒绝危险选项：$1。本管理脚本不删除持久数据或镜像" >&2
      exit 2
      ;;
    -h|--help) HELP=1; shift ;;
    --*) echo "未知选项：$1" >&2; main_help >&2; exit 2 ;;
    *)
      if [[ -z "$COMMAND" ]]; then
        COMMAND="$1"
      elif [[ "$COMMAND" == plan && -z "$PLAN_TARGET" ]]; then
        PLAN_TARGET="$1"
      else
        echo "多余参数：$1" >&2
        exit 2
      fi
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
  start|stop|restart|status|check|address|logs|instances|plan|recreate|down|backup|prune|install|build|upgrade|config) ;;
  purge|delete|delete-data|uninstall|uninstall-data|remove|rm)
    echo '拒绝：本管理脚本不提供删除数据或卸载命令；数据库卷、身份、审计、密钥和备份不会被删除' >&2
    exit 2
    ;;
  *) echo "未知命令：$COMMAND" >&2; main_help >&2; exit 2 ;;
esac
[[ "$WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo '--timeout须为正整数秒' >&2; exit 2; }
if [[ -n "$CONFIRM_TOKEN" && "$COMMAND" != recreate && "$COMMAND" != down && "$COMMAND" != upgrade && "$COMMAND" != prune ]]; then
  echo '--confirm只用于recreate、down、upgrade和prune' >&2
  exit 2
fi
if [[ -n "$BACKUP_OUTPUT" && "$COMMAND" != backup && "$COMMAND" != prune ]] \
  && ! [[ "$COMMAND" == plan && "$PLAN_TARGET" == prune ]]; then
  echo '--output只用于backup和prune' >&2
  exit 2
fi
if [[ -n "$COPY_TARGET" && "$COMMAND" != backup ]]; then
  echo '--copy-to只用于backup' >&2
  exit 2
fi
if [[ -n "$COPY_TARGET" && "$COPY_TARGET" != /* ]]; then
  echo '--copy-to必须为绝对目录' >&2
  exit 2
fi
if [[ "$COMMAND" == prune ]] || [[ "$COMMAND" == plan && "$PLAN_TARGET" == prune ]]; then
  [[ -n "$KEEP_COUNT" ]] || { echo 'prune必须用--keep指定保留份数' >&2; exit 2; }
  [[ "$KEEP_COUNT" =~ ^[1-9][0-9]*$ ]] || { echo '--keep须为不小于1的整数' >&2; exit 2; }
elif [[ -n "$KEEP_COUNT" ]]; then
  echo '--keep只用于prune' >&2
  exit 2
fi
if [[ "$COMMAND" == plan && "$PLAN_TARGET" != down && "$PLAN_TARGET" != recreate && "$PLAN_TARGET" != prune ]]; then
  echo 'plan目标必须是down、recreate或prune' >&2
  exit 2
fi
if [[ -n "$BUILD_AGENT" || -n "$BUILD_TAG" ]] && [[ "$COMMAND" != build ]]; then
  echo '--agent和--tag只用于build' >&2
  exit 2
fi
if [[ -n "$UPGRADE_IMAGE" && "$COMMAND" != upgrade ]]; then
  echo '--image只用于upgrade' >&2
  exit 2
fi
if [[ -n "$INSTALL_CONFIG" || "$INSTALL_CONFIGURE" == 1 ]] && [[ "$COMMAND" != install ]]; then
  echo '--config和--configure只用于install' >&2
  exit 2
fi
if [[ "$COMMAND" == build && "$BUILD_AGENT" != 0 && "$BUILD_AGENT" != 1 ]]; then
  echo 'build必须使用--agent 0或--agent 1' >&2
  exit 2
fi
if [[ "$COMMAND" == upgrade && -z "$UPGRADE_IMAGE" ]]; then
  echo 'upgrade必须使用--image指定已在本地存在的目标镜像' >&2
  exit 2
fi

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
  mkdir -p "$(dirname "$LOCK_DIR")"
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

docker_desktop_pids() {
  {
    pgrep -f 'Docker\.app/Contents/MacOS/com\.docker\.(backend|build)' || true
    pgrep -x 'Docker Desktop' || true
  } | sort -u
}

ensure_docker() {
  probe_docker && return 0
  echo 'Docker引擎未响应，正在尝试启动…' >&2
  if [[ "$(uname -s)" == Darwin ]]; then
    open -a "$DOCKER_APP_NAME"
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
    docker_desktop_pids | xargs kill -TERM 2>/dev/null || true
    remaining=20
    while (( remaining > 0 )); do
      [[ -z "$(docker_desktop_pids)" ]] && break
      sleep 0.5
      remaining=$((remaining - 1))
    done
    docker_desktop_pids | xargs kill -KILL 2>/dev/null || true
    open -a "$DOCKER_APP_NAME"
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

json_images() {
  local file="$INSTANCE/installation.json" compose="$INSTANCE/compose.json"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$file" "$compose" <<'PY'
import json, sys
marker=json.load(open(sys.argv[1]))
compose=json.load(open(sys.argv[2]))
service=compose.get('services',{}).get('service',{}).get('image','')
setup=compose.get('services',{}).get('setup',{}).get('image','')
print('\t'.join(str(x or '') for x in (marker.get('image'),service,setup)))
PY
    return
  fi
  local marker service setup
  marker="$(sed -n 's/^  "image": "\([^"]*\)",$/\1/p' "$file" | head -n 1)"
  service="$(awk '/^    "service": \{/{found=1} found && /^      "image":/{gsub(/[",]/,"",$2); print $2; exit}' "$compose")"
  setup="$(awk '/^    "setup": \{/{found=1} found && /^      "image":/{gsub(/[",]/,"",$2); print $2; exit}' "$compose")"
  printf '%s\t%s\t%s\n' "$marker" "$service" "$setup"
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
  find "$RUNTIME_DIR" -type f -name installation.json \
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
  mkdir -p "$(dirname "$ACTIVE_POINTER")"
  printf '%s\n' "$INSTANCE" >"$ACTIVE_POINTER.$$"
  chmod 600 "$ACTIVE_POINTER.$$"
  mv "$ACTIVE_POINTER.$$" "$ACTIVE_POINTER"
}

resolve_instance() {
  local docker_ready=0
  probe_docker && docker_ready=1
  if valid_instance "$INSTANCE"; then
    INSTANCE="$(cd "$INSTANCE" && pwd)"
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
      return 0
    fi
  fi
  local count
  count="$(printf '%s\n' "$instances" | awk 'NF{n++} END{print n+0}')"
  if [[ "$count" -eq 1 ]]; then
    INSTANCE="$(cd "$instances" && pwd)"
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
  return 0
}

load_instance() {
  resolve_instance || return 2
  IFS=$'\t' read -r PROJECT IMAGE PUBLIC_URL PORT DEMO CURRENT_HOST <<<"$(json_marker "$INSTANCE/installation.json")"
  if [[ -z "$PROJECT" || -z "$IMAGE" || -z "$PORT" ]]; then
    bad '实例配置缺少项目、镜像或端口'
    return 2
  fi
  IFS=$'\t' read -r MARKER_IMAGE SERVICE_IMAGE SETUP_IMAGE <<<"$(json_images)"
  if [[ -z "$SERVICE_IMAGE" || "$SERVICE_IMAGE" != "$SETUP_IMAGE" ]]; then
    bad 'Compose中的service/setup镜像不一致或缺失'
    return 2
  fi
  IMAGE="$SERVICE_IMAGE"
}

require_instance_images() {
  docker image inspect "$SERVICE_IMAGE" >/dev/null 2>&1 || {
    bad "service镜像不存在：$SERVICE_IMAGE"
    bad '请恢复原镜像，或先执行build再执行受控upgrade；start/restart不会自动构建'
    return 2
  }
  docker image inspect "$SETUP_IMAGE" >/dev/null 2>&1 || {
    bad "setup镜像不存在：$SETUP_IMAGE"
    return 2
  }
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

has_local_postgres() {
  compose config --services 2>/dev/null | grep -qx postgres
}

protected_volume_state() {
  if ! has_local_postgres; then return 0; fi
  docker volume inspect --format '{{.Name}}|{{.Driver}}|{{.CreatedAt}}' \
    "${PROJECT}_database" 2>/dev/null || true
}

confirmation_required() {
  local action="$1" expected="${2:-$PROJECT:$1}" entered
  if [[ -n "$CONFIRM_TOKEN" ]]; then
    [[ "$CONFIRM_TOKEN" == "$expected" ]] && return 0
    bad "确认串不匹配；应精确输入 ${expected}"
    return 2
  fi
  if [[ -t 0 ]]; then
    echo >&2
    read -r -p "确认执行 ${action}，请输入 ${expected}：" entered
    [[ "$entered" == "$expected" ]] && return 0
    bad '确认失败，未执行任何修改'
    return 2
  fi
  bad "非交互环境必须传 --confirm ${expected}"
  return 2
}

show_plan() {
  local action="$1" folder
  section "维护计划：${action}"
  note "实例：$INSTANCE"
  note "项目：$PROJECT"
  if ! probe_docker; then
    printf '  [注意] Docker不可用，以下基于持久配置；执行前会重新检查现场\n'
  fi
  case "$action" in
    down)
      note '将删除项目容器和项目网络'
      note '不会删除数据库卷、镜像、实例配置、秘密、审计或备份'
      if probe_docker; then
        echo '  容器：'
        compose ps -a --format '    {{.Name}}  {{.Status}}' || true
        echo '  项目网络：'
        docker network ls --filter "label=com.docker.compose.project=$PROJECT" --format '    {{.Name}}' || true
        echo '  必须保留的数据卷：'
        if has_local_postgres; then
          folder="$(protected_volume_state)"
          if [[ -n "$folder" ]]; then echo "    ${PROJECT}_database  ${folder}"; else echo "    ${PROJECT}_database  当前不存在"; fi
        else
          echo '    无内置数据库卷；外部数据库不受本操作影响'
        fi
      fi
      ;;
    recreate)
      note '将强制重建gateway和service容器'
      note 'postgres与identity保持运行；所有卷、镜像、配置和密钥保留'
      if probe_docker; then
        echo '  重建目标：'
        compose ps -a --format '    {{.Name}}  {{.Status}}' gateway service || true
        folder="$(protected_volume_state)"
        [[ -n "$folder" ]] && echo "  数据卷保持不变：${folder}"
      fi
      ;;
    prune)
      note "备份目录：$(backup_root)"
      note "保留最新${KEEP_COUNT}份本项目备份"
      note '只处理名称匹配“项目-UTC时间-PID”且清单project一致的目录'
      note '不删除卷、镜像、实例目录、审计、秘密或其他项目备份'
      ;;
  esac
  if [[ "$action" == prune ]]; then
    printf '\n  确认串：%s:%s:%s\n' "$PROJECT" "$action" "$KEEP_COUNT"
    printf '  预览：./manage.sh plan prune --keep %s\n' "$KEEP_COUNT"
    printf '  执行：./manage.sh prune --keep %s --confirm %s:%s:%s\n' "$KEEP_COUNT" "$PROJECT" "$action" "$KEEP_COUNT"
  else
    printf '\n  确认串：%s:%s\n' "$PROJECT" "$action"
    printf '  预览：./manage.sh plan %s\n' "$action"
    printf '  执行：./manage.sh %s --confirm %s:%s\n' "$action" "$PROJECT" "$action"
  fi
}

recreate_stack() {
  local before after running
  if [[ "$DEMO" == 1 ]]; then
    current_candidates
    if ! address_is_active "$CURRENT_HOST"; then
      bad '当前登录地址已不在活动网卡上；请先执行start或restart完成地址迁移'
      return 2
    fi
    SELECTED="$CURRENT_HOST"
  fi
  show_plan recreate
  confirmation_required recreate || return 2
  running="$(compose ps --status running --services 2>/dev/null || true)"
  printf '%s\n' "$running" | grep -qx postgres || { bad 'postgres未运行；请先执行start'; return 1; }
  printf '%s\n' "$running" | grep -qx identity || { bad 'identity未运行；请先执行start'; return 1; }
  before="$(protected_volume_state)"
  if has_local_postgres && [[ -z "$before" ]]; then
    bad '未找到内置数据库卷；拒绝重建，先核对实例数据状态'
    return 1
  fi
  step '1/3' '刷新网关配置'
  refresh_gateway
  step '2/3' '保留数据并重建gateway/service'
  compose up -d --force-recreate --no-deps --wait --wait-timeout "$WAIT_SECONDS" gateway service || return 2
  after="$(protected_volume_state)"
  if [[ -n "$before" && "$before" != "$after" ]]; then
    bad '数据库卷状态发生变化；停止后续操作并立即核对'
    return 1
  fi
  step '3/3' '核验就绪状态'
  wait_ready || return 1
  ok '容器已重建；数据库卷、镜像、配置和密钥未删除'
}

down_stack() {
  local before after image_ids="" image cid
  show_plan down
  confirmation_required down || return 2
  if has_local_postgres; then
    before="$(protected_volume_state)"
    [[ -n "$before" ]] || { bad '未找到内置数据库卷；拒绝执行down'; return 1; }
  fi
  if probe_docker; then
    for name in postgres identity gateway service; do
      cid="$(compose ps -q "$name" 2>/dev/null || true)"
      [[ -n "$cid" ]] || continue
      image="$(docker inspect --format '{{.Image}}' "$cid" 2>/dev/null || true)"
      [[ -n "$image" ]] && image_ids="${image_ids}${image}"$'\n'
    done
  fi
  step '1/2' '删除容器和项目网络（不删除卷和镜像）'
  compose down --timeout 20 || return 2
  after="$(protected_volume_state)"
  if [[ -n "$before" && "$before" != "$after" ]]; then
    bad '数据库卷在down后状态不一致；立即停止并核对，禁止继续清理'
    return 1
  fi
  if [[ -n "$image_ids" ]]; then
    while IFS= read -r image; do
      [[ -n "$image" ]] || continue
      docker image inspect "$image" >/dev/null 2>&1 || { bad "镜像意外缺失：$image"; return 1; }
    done <<<"$image_ids"
  fi
  step '2/2' '核验保留项'
  ok '容器和项目网络已删除'
  ok '数据库卷、镜像、实例配置、秘密、审计和备份均保留'
  echo '  恢复容器：./manage.sh start'
}

copy_backup_item() {
  local relative="$1" source="$INSTANCE/$1" target="$1"
  [[ -e "$source" ]] || return 0
  [[ -L "$source" ]] && { bad "拒绝备份软链：$relative"; return 2; }
  mkdir -p "$(dirname "$BACKUP_DIR/$target")"
  cp -Rp "$source" "$BACKUP_DIR/$target"
}

dump_database() {
  local database="$1" target="$BACKUP_DIR/$1.dump" pg
  pg="$(compose ps -q postgres 2>/dev/null || true)"
  [[ -n "$pg" ]] || { bad 'postgres容器未运行，无法备份'; return 1; }
  docker exec "$pg" sh -c \
    'export PGPASSWORD="$(cat /run/secrets/postgres_password)"; exec pg_dump -h 127.0.0.1 -U postgres -d "$1" -Fc' \
    office-backup "$database" >"$target"
  chmod 600 "$target"
  docker exec -i "$pg" pg_restore --list <"$target" >/dev/null || { bad "备份校验失败：$database"; return 1; }
}

backup_instance() {
  local root stamp manifest
  if ! has_local_postgres; then bad 'backup当前只支持内置PostgreSQL实例'; return 2; fi
  compose ps --status running --services | grep -qx postgres || { bad 'postgres未运行；无法创建一致性备份'; return 1; }
  root="${BACKUP_OUTPUT:-$DEFAULT_BACKUP_ROOT}"
  [[ "$root" == /* ]] || { bad '--output必须为绝对目录'; return 2; }
  mkdir -p "$root"
  [[ ! -L "$root" ]] || { bad '备份目录不能是软链'; return 2; }
  root="$(cd "$root" && pwd -P)"
  case "$root/" in
    "$INSTANCE/"*) bad '备份目录不能位于实例目录内'; return 2 ;;
  esac
  chmod 700 "$root"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP_DIR="$root/${PROJECT}-${stamp}-$$"
  mkdir "$BACKUP_DIR" || { bad '备份目录已存在或无法创建'; return 2; }
  chmod 700 "$BACKUP_DIR"
  step '1/4' '备份业务库和身份库'
  dump_database office || return 1
  dump_database office_identity || return 1
  step '2/4' '复制实例配置与秘密'
  for item in installation.json compose.json result.json gateway service maintenance identity credentials postgres; do
    copy_backup_item "$item" || return 1
  done
  chmod -R go-rwx "$BACKUP_DIR"
  step '3/4' '生成备份清单'
  manifest="$BACKUP_DIR/MANIFEST.txt"
  {
    printf 'project=%s\n' "$PROJECT"
    printf 'created_at=%s\n' "$stamp"
    printf 'instance=%s\n' "$INSTANCE"
    printf 'office_dump=office.dump\n'
    printf 'identity_dump=office_identity.dump\n'
    printf 'volume=%s\n' "$(protected_volume_state)"
    printf 'no_deletion_by_manage= true\n'
  } >"$manifest"
  chmod 600 "$manifest"
  if command -v shasum >/dev/null 2>&1; then
    (cd "$BACKUP_DIR" && find . -type f ! -name SHA256SUMS -print | sort | xargs shasum -a 256) >"$BACKUP_DIR/SHA256SUMS"
  elif command -v sha256sum >/dev/null 2>&1; then
    (cd "$BACKUP_DIR" && find . -type f ! -name SHA256SUMS -print | sort | xargs sha256sum) >"$BACKUP_DIR/SHA256SUMS"
  else
    warn '未找到SHA-256工具；数据库归档已通过pg_restore清单校验'
  fi
  [[ -f "$BACKUP_DIR/SHA256SUMS" ]] && chmod 600 "$BACKUP_DIR/SHA256SUMS"
  step '4/4' '完成'
  ok "备份目录：$BACKUP_DIR"
  ok '业务库与身份库已通过pg_restore清单校验'
  ok '本命令未删除任何旧备份或运行数据'
  backup_copy_offsite || return 1
}

backup_copy_offsite() {
  local source="$BACKUP_DIR" target="$COPY_TARGET" name
  [[ -n "$target" ]] || return 0
  [[ -f "$source/SHA256SUMS" ]] || { bad '备份缺少SHA256SUMS，拒绝无校验复制'; return 1; }
  mkdir -p "$target" || { bad "无法创建异地目录：$target"; return 1; }
  [[ ! -L "$target" ]] || { bad '异地目录不能是软链'; return 1; }
  target="$(cd "$target" && pwd -P)"
  case "$target/" in
    "$source/"*|"$INSTANCE/"*) bad '异地目录不能位于备份或实例目录内'; return 2 ;;
  esac
  name="$(basename "$source")"
  [[ ! -e "$target/$name" ]] || { bad "异地已存在同名备份：$target/$name"; return 1; }
  step '附加' '复制到异地目录并复核摘要'
  cp -Rp "$source" "$target/$name" || { bad '异地复制失败，原备份保留'; return 1; }
  if command -v shasum >/dev/null 2>&1; then
    (cd "$target/$name" && shasum -a 256 -c SHA256SUMS >/dev/null) \
      || { bad '异地副本摘要不一致，请核对复制介质'; return 1; }
  elif command -v sha256sum >/dev/null 2>&1; then
    (cd "$target/$name" && sha256sum -c SHA256SUMS >/dev/null) \
      || { bad '异地副本摘要不一致，请核对复制介质'; return 1; }
  else
    bad '缺少SHA-256工具，无法复核异地副本'; return 1
  fi
  ok "异地副本：$target/${name}（摘要一致）"
  ok '本命令未删除任何旧备份或运行数据'
}

backup_root() {
  printf '%s' "${BACKUP_OUTPUT:-$DEFAULT_BACKUP_ROOT}"
}

collect_project_backups() {
  local root="$1" dir base list=''
  [[ -d "$root" && ! -L "$root" ]] || return 0
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    [[ -L "$dir" ]] && continue
    base="$(basename "$dir")"
    [[ "$base" =~ ^${PROJECT}-[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || continue
    [[ -f "$dir/MANIFEST.txt" ]] || continue
    grep -qx "project=$PROJECT" "$dir/MANIFEST.txt" 2>/dev/null || continue
    list="${list}${dir}"$'\n'
  done < <(find "$root" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort)
  printf '%s' "$list"
}

prune_backups() {
  local root list total keep drop n dir remaining
  keep="$KEEP_COUNT"
  root="$(backup_root)"
  [[ "$root" == /* ]] || { bad '备份目录必须为绝对路径'; return 2; }
  [[ -d "$root" ]] || { bad "备份目录不存在：$root"; return 1; }
  [[ ! -L "$root" ]] || { bad '备份目录不能是软链'; return 2; }
  root="$(cd "$root" && pwd -P)"
  [[ "$root" != / ]] || { bad '备份目录不能是根目录'; return 2; }
  section "备份清理：保留最新${keep}份"
  note "实例：$INSTANCE"
  note "项目：$PROJECT"
  note "备份目录：$root"
  list="$(collect_project_backups "$root")"
  total="$(printf '%s' "$list" | awk 'NF{n++} END{print n+0}')"
  note "匹配的本项目备份：${total}份"
  if (( total <= keep )); then
    ok '备份数量未超过保留份数，无需清理'
    return 0
  fi
  drop=$((total - keep))
  printf '  将删除以下%s份最旧备份：\n' "$drop"
  n=0
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    n=$((n + 1))
    (( n <= drop )) && printf '    %s\n' "$dir"
  done <<<"$list"
  printf '  保留最新的%s份；不会删除其他项目、卷、镜像、实例目录或秘密\n' "$keep"
  printf '  确认串：%s:prune:%s\n' "$PROJECT" "$keep"
  confirmation_required prune "$PROJECT:prune:$keep" || return 2
  n=0
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    n=$((n + 1))
    (( n > drop )) && continue
    case "$dir" in
      "$root"/*) ;;
      *) bad "拒绝删除备份目录之外的路径：$dir"; return 1 ;;
    esac
    [[ ! -L "$dir" && -f "$dir/MANIFEST.txt" ]] || { bad "备份目录状态异常：$dir"; return 1; }
    grep -qx "project=$PROJECT" "$dir/MANIFEST.txt" || { bad "备份清单项目不符：$dir"; return 1; }
    rm -rf -- "$dir" || { bad "删除失败：$dir"; return 1; }
    ok "已删除旧备份：$dir"
  done <<<"$list"
  remaining="$(collect_project_backups "$root" | awk 'NF{n++} END{print n+0}')"
  (( remaining >= keep )) || { bad '清理后保留数量不足，立即停止并核对'; return 1; }
  ok "剩余本项目备份：${remaining}份"
  ok '数据库卷、镜像、实例目录、审计和秘密未删除'
}

install_environment() {
  local args=()
  [[ "$INSTALL_CONFIGURE" == 1 ]] && args+=(--configure)
  if [[ -n "$INSTANCE" ]]; then
    args+=("${INSTALL_CONFIG:-$ROOT/deploy/.env}" "$INSTANCE")
  elif [[ -n "$INSTALL_CONFIG" ]]; then
    args+=("$INSTALL_CONFIG")
  fi
  section '新环境安装'
  note "将调用deploy/install.sh${INSTALL_CONFIG:+：$INSTALL_CONFIG}${INSTANCE:+；实例目录：$INSTANCE}"
  if [[ ${#args[@]} -gt 0 ]]; then
    bash "$ROOT/deploy/install.sh" "${args[@]}"
  else
    bash "$ROOT/deploy/install.sh"
  fi
}

image_agent_variant() {
  local image="$1" value
  value="$(docker run --rm --network none --read-only --cap-drop=ALL "$image" python -c \
    'import importlib.util; print(1 if importlib.util.find_spec("office_agent") else 0)' 2>/dev/null)" || {
    bad "无法识别镜像运行能力：$image"
    return 2
  }
  [[ "$value" == 0 || "$value" == 1 ]] || { bad "镜像能力回执异常：$image"; return 2; }
  printf '%s' "$value"
}

build_image() {
  local short_sha dirty tag image_id variant
  section '镜像构建'
  [[ "$BUILD_AGENT" == 0 || "$BUILD_AGENT" == 1 ]] || { bad '必须指定--agent 0或1'; return 2; }
  short_sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo local)"
  dirty=''
  if [[ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null || true)" ]]; then dirty='-dirty'; fi
  tag="${BUILD_TAG:-office-data-service:manage-${BUILD_AGENT}-${short_sha}${dirty}}"
  note "源码提交：$short_sha${dirty:+（工作区有未提交修改）}"
  note "镜像变体：OFFICE_AGENT=$BUILD_AGENT"
  note "目标标签：$tag"
  [[ -z "$dirty" ]] || warn '本地镜像包含未提交修改，不能作为未经验证的生产发布版本'
  step '1/2' '构建镜像'
  docker build --provenance=false \
    --build-arg "OFFICE_AGENT=$BUILD_AGENT" \
    -f "$ROOT/deploy/Dockerfile" -t "$tag" "$ROOT"
  image_id="$(docker image inspect "$tag" --format '{{.Id}}')"
  variant="$(image_agent_variant "$image_id")" || return 2
  [[ "$variant" == "$BUILD_AGENT" ]] || { bad '构建镜像的运行能力与OFFICE_AGENT不一致'; return 2; }
  step '2/2' '完成'
  ok "镜像：$tag"
  ok "镜像ID：$image_id"
  note '本命令未安装实例，未切换运行版本，也未删除旧镜像'
  note "切换已有实例：./manage.sh upgrade --image $tag"
}

set_instance_image() {
  local target="$1" status="$2" migration="${3:-}" commit
  commit="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  if command -v python3 >/dev/null 2>&1; then
    python3 "$ROOT/deploy/installer/set_instance_image.py" "$INSTANCE" \
      --image "$target" --status "$status" --from-image "$SERVICE_IMAGE" \
      --backup "${BACKUP_DIR:-}" --migration "$migration" --commit "$commit" >/dev/null
  else
    docker run --rm --user 0:0 -v "$INSTANCE:$INSTANCE" \
      -v "$ROOT/deploy/installer:/installer:ro" "$SERVICE_IMAGE" \
      python /installer/set_instance_image.py "$INSTANCE" \
      --image "$target" --status "$status" --from-image "$SERVICE_IMAGE" \
      --backup "${BACKUP_DIR:-}" --migration "$migration" --commit "$commit" >/dev/null
  fi
}

current_migration() {
  compose --profile setup run --rm --entrypoint python setup \
    -m alembic -c /opt/office/alembic.ini current 2>/dev/null | awk 'NF{line=$0} END{print line}'
}

image_migrations() {
  docker run --rm --network none "$SERVICE_IMAGE" python -m alembic \
    -c /opt/office/alembic.ini heads 2>/dev/null
}

migration_revisions() {
  awk 'NF{print $1}' | sort -u
}

upgrade_instance() {
  local target_ref="$UPGRADE_IMAGE" target_id short current_variant target_variant
  local expected old_migration migration
  docker image inspect "$target_ref" >/dev/null 2>&1 || {
    bad "目标镜像不存在：$target_ref"
    bad '请先用build生成，或由管理员将已验证镜像加载到本机；upgrade不会静默拉取或构建'
    return 2
  }
  target_id="$(docker image inspect "$target_ref" --format '{{.Id}}')"
  if [[ "$target_id" == "$SERVICE_IMAGE" ]]; then
    bad '目标镜像已经运行；请重新build生成新的镜像ID'
    return 2
  fi
  current_variant="$(image_agent_variant "$SERVICE_IMAGE")" || return 2
  target_variant="$(image_agent_variant "$target_id")" || return 2
  if [[ "$current_variant" != "$target_variant" ]]; then
    bad "镜像变体不匹配：当前=${current_variant}，目标=${target_variant}；不允许在普通与Agent镜像间直接升级"
    return 2
  fi
  docker run --rm --network none "$target_id" python -m alembic \
    -c /opt/office/alembic.ini heads >/dev/null 2>&1 || {
      bad '目标镜像缺少可用的数据库迁移工具'
      return 2
    }
  SELECTED="$CURRENT_HOST"
  if [[ "$DEMO" == 1 ]]; then
    current_candidates
    address_is_active "$CURRENT_HOST" || { bad '当前登录地址已失效；请先执行start/restart完成地址迁移'; return 2; }
  fi
  short="${target_id#sha256:}"
  short="${short:0:12}"
  expected="$PROJECT:upgrade:$short"
  section '维护计划：upgrade'
  note "当前service镜像：$SERVICE_IMAGE"
  note "目标镜像：$target_ref"
  note "目标镜像ID：$target_id"
  note '将先完整备份业务库、身份库、配置和秘密'
  note '将停止service和gateway，执行数据库迁移后启动新镜像'
  note '不删除旧镜像、卷、配置、秘密或备份；失败时保留停写现场'
  printf '  确认串：%s\n' "$expected"
  confirmation_required upgrade "$expected" || return 2
  step '1/6' '创建升级前备份'
  backup_instance || { bad '升级前备份未完成，取消镜像切换'; return 1; }
  old_migration="$(current_migration || true)"
  step '2/6' '停止写入入口'
  compose stop --timeout 20 service gateway || return 2
  step '3/6' '切换实例镜像记录'
  if ! set_instance_image "$target_id" migrating "$old_migration"; then
    bad '镜像记录更新失败；service/gateway保持停止'
    return 1
  fi
  SERVICE_IMAGE="$target_id"; SETUP_IMAGE="$target_id"; MARKER_IMAGE="$target_id"
  step '4/6' '执行数据库迁移'
  if ! compose --profile setup run --rm --entrypoint python setup \
    -m alembic -c /opt/office/alembic.ini upgrade head; then
    set_instance_image "$target_id" failed "$old_migration" || true
    bad '数据库迁移失败；service/gateway保持停止，使用备份目录核对恢复'
    return 1
  fi
  compose --profile setup run --rm setup database-check || {
    set_instance_image "$target_id" failed "$old_migration" || true
    bad '数据库迁移后检查失败；service/gateway保持停止'
    return 1
  }
  migration="$(current_migration || true)"
  step '5/6' '启动新镜像'
  if ! compose up -d --wait --wait-timeout "$WAIT_SECONDS" service gateway; then
    compose stop --timeout 20 service gateway >/dev/null 2>&1 || true
    set_instance_image "$target_id" failed "$migration" || true
    bad '新镜像启动失败；service/gateway保持停止'
    return 1
  fi
  step '6/6' '核验健康与身份入口'
  if ! wait_ready || ! run_check; then
    compose stop --timeout 20 service gateway >/dev/null 2>&1 || true
    set_instance_image "$target_id" failed "$migration" || true
    bad '新镜像健康检查未通过；service/gateway保持停止'
    return 1
  fi
  set_instance_image "$target_id" healthy "$migration" || { bad '运行健康但升级记录保存失败'; return 1; }
  docker tag "$target_id" "office-instance:$PROJECT"
  ok "升级完成；当前镜像：$target_id"
  ok "升级前备份：$BACKUP_DIR"
  ok '旧镜像和全部持久数据未删除'
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
    IFS=$'\t' read -r PROJECT IMAGE PUBLIC_URL PORT DEMO CURRENT_HOST <<<"$(json_marker "$INSTANCE/installation.json")"
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

issuer_url() {
  if [[ "$DEMO" == 1 ]]; then
    printf 'http://%s:%s/identity/realms/office' "$CURRENT_HOST" "$PORT"
  else
    printf '%s/identity/realms/office' "$PUBLIC_URL"
  fi
}

read_issuer() {
  local url
  url="$(issuer_url)/.well-known/openid-configuration"
  if [[ -f "$INSTANCE/service/ca.pem" ]]; then
    curl -fsS --noproxy '*' --max-time 3 --cacert "$INSTANCE/service/ca.pem" "$url" 2>/dev/null
  else
    curl -fsS --noproxy '*' --max-time 3 "$url" 2>/dev/null
  fi | sed -n 's/.*"issuer"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

issuer_once() {
  [[ "$(read_issuer)" == "$(issuer_url)" ]]
}

wait_ready() {
  local remaining="$WAIT_SECONDS"
  while (( remaining > 0 )); do
    if health_once && issuer_once; then ok '健康与登录入口就绪'; return 0; fi
    sleep 1
    remaining=$((remaining - 1))
  done
  bad "健康或登录入口在${WAIT_SECONDS}秒内未就绪；请查看identity与gateway日志"
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
    wait_ready || return 1
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
  wait_ready || return 1
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
  wait_ready || return 1
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
  note "镜像记录：$MARKER_IMAGE"
  note "运行镜像：$SERVICE_IMAGE"
  [[ "$MARKER_IMAGE" == "$SERVICE_IMAGE" && "$SERVICE_IMAGE" == "$SETUP_IMAGE" ]] \
    || printf '  [注意] 镜像记录或Compose镜像不一致\n'
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
    SELECTED="$CURRENT_HOST"
    if health_once; then health=ok; ok '健康端点可用'; else printf '  [注意] 健康端点不可用\n'; fi
    local binding
    binding="$(docker port "$PROJECT-gateway-1" "$PORT/tcp" 2>/dev/null || true)"
    if [[ "$binding" == "0.0.0.0:$PORT" ]]; then ok "gateway发布：$binding"; else printf '  [注意] gateway发布：%s\n' "${binding:-未运行}"; fi
  fi
  printf '\n'
  [[ "$docker_ready" == 1 && "$health" == ok ]]
}

run_check() {
  local failures=0 binding issuer expected_issuer
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
  if [[ "$MARKER_IMAGE" == "$SERVICE_IMAGE" && "$SERVICE_IMAGE" == "$SETUP_IMAGE" ]]; then
    ok '镜像记录与Compose一致'
  else
    bad '镜像记录与Compose不一致'
    failures=$((failures+1))
  fi
  if docker image inspect "$SERVICE_IMAGE" >/dev/null 2>&1; then
    ok "service镜像存在：$SERVICE_IMAGE"
  else
    bad "service镜像缺失：$SERVICE_IMAGE"
    failures=$((failures+1))
  fi
  local name
  for name in postgres identity gateway service; do
    if compose ps --status running --services | grep -qx "$name"; then ok "容器运行：$name"; else bad "容器未运行：$name"; failures=$((failures+1)); fi
  done
  local db_revs image_revs
  db_revs="$(compose --profile setup run --rm --entrypoint python setup \
    -m alembic -c /opt/office/alembic.ini current 2>/dev/null | migration_revisions)"
  image_revs="$(image_migrations | migration_revisions)"
  if [[ -n "$db_revs" && "$db_revs" == "$image_revs" ]]; then
    ok "迁移版本一致：${db_revs//$'\n'/,}"
  else
    bad "迁移版本不一致：数据库=${db_revs:-未读取} 镜像=${image_revs:-未读取}"
    failures=$((failures+1))
  fi
  binding="$(docker port "$PROJECT-gateway-1" "$PORT/tcp" 2>/dev/null || true)"
  if [[ "$binding" == "0.0.0.0:$PORT" ]]; then ok "端口监听：$binding"; else bad "端口监听异常：${binding:-无}"; failures=$((failures+1)); fi
  SELECTED="$CURRENT_HOST"
  if health_once; then ok '健康端点返回200'; else bad '健康端点不可用'; failures=$((failures+1)); fi
  expected_issuer="$(issuer_url)"
  issuer="$(read_issuer)"
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

show_config() {
  section '管理配置'
  if [[ -f "$MANAGE_CONFIG_FILE" && ! -L "$MANAGE_CONFIG_FILE" ]]; then
    ok "配置文件：$MANAGE_CONFIG_FILE"
  else
    note "配置文件：${MANAGE_CONFIG_FILE}（未使用，采用默认值）"
  fi
  note "项目根目录：$ROOT"
  note "运行目录：$RUNTIME_DIR"
  note "备份目录：$DEFAULT_BACKUP_ROOT"
  note "活动实例文件：$ACTIVE_POINTER"
  note "管理锁目录：$LOCK_DIR"
  note "默认等待：${WAIT_SECONDS}秒"
  note "Docker应用名：$DOCKER_APP_NAME"
  if [[ -n "$INSTANCE" ]]; then note "默认实例：$INSTANCE"; else note '默认实例：自动发现'; fi
  if [[ "$RUNTIME_DIR" != "$ROOT/runtime" ]]; then
    warn '运行目录已改动；安装器目标由deploy/.env的OFFICE_INSTANCE_DIR决定，两者须指向同一位置'
  fi
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
  config)
    show_config
    ;;
  install)
    acquire_lock || exit 2
    ensure_docker || exit 2
    install_environment
    ;;
  build)
    acquire_lock || exit 2
    ensure_docker || exit 2
    build_image
    ;;
  upgrade)
    acquire_lock || exit 2
    ensure_docker || exit 2
    load_instance || exit 2
    require_instance_images || exit 2
    upgrade_instance
    record_instance
    ;;
  start|restart)
    acquire_lock || exit 2
    ensure_docker || exit 2
    load_instance || exit 2
    require_instance_images || exit 2
    if [[ "$COMMAND" == start ]]; then
      start_stack
    else
      restart_stack
    fi
    record_instance
    ;;
  stop)
    acquire_lock || exit 2
    load_instance || exit 2
    stop_stack
    record_instance
    ;;
  plan)
    load_instance || exit 2
    show_plan "$PLAN_TARGET"
    ;;
  recreate)
    acquire_lock || exit 2
    ensure_docker || exit 2
    load_instance || exit 2
    require_instance_images || exit 2
    recreate_stack
    record_instance
    ;;
  down)
    acquire_lock || exit 2
    ensure_docker || exit 2
    load_instance || exit 2
    down_stack
    record_instance
    ;;
  backup)
    acquire_lock || exit 2
    ensure_docker || exit 2
    load_instance || exit 2
    backup_instance
    ;;
  prune)
    acquire_lock || exit 2
    load_instance || exit 2
    prune_backups
    ;;
  status)
    load_instance || exit 2
    show_status
    ;;
  check)
    load_instance || exit 2
    run_check
    ;;
  address)
    load_instance || exit 2
    show_addresses
    ;;
  logs)
    load_instance || exit 2
    show_logs
    ;;
esac
