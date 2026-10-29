#!/usr/bin/env bash
# 人工服务器配置入口：仅写公开参数，秘密始终由安装器随机生成。
set -euo pipefail
if [[ "${1:-}" == --help ]]; then
  echo '用法：bash deploy/configure.sh [deploy/.env]；仅生成配置，不启动服务。'
  exit 0
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$ROOT/deploy/.env}"
ask() {
  local label="$1" default="$2"
  printf '%s [%s]: ' "$label" "$default" >&2
  IFS= read -r ANSWER || { echo '输入已结束，未写入配置。' >&2; exit 2; }
  ANSWER="${ANSWER:-$default}"
  [[ "$ANSWER" != *'$'* && "$ANSWER" != *'`'* && "$ANSWER" != *$'\r'* ]] || { echo '参数只能填写单行字面量。' >&2; exit 2; }
}
if [[ -e "$DEST" || -L "$DEST" ]]; then
  [[ -f "$DEST" && ! -L "$DEST" ]] || { echo '配置必须是普通文件，不能覆盖软链。' >&2; exit 2; }
  ask '配置已存在，是否覆盖？输入yes确认（不会迁移既有实例）' no
  [[ "$ANSWER" == yes ]] || exit 2
fi
IP=''
if command -v ipconfig >/dev/null 2>&1; then IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
elif command -v hostname >/dev/null 2>&1; then IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"; fi
printf '请在公司服务器上执行。员工电脑不能用自己的IP替代服务器地址。\n正式入口应使用稳定内部域名；监听默认覆盖服务器当前所有内网地址，IP变化不必重新发布端口。\n' >&2
ask_listen() {
  ask '监听范围：lan=局域网可访问，local=仅服务器本机，也可直接填写内网IPv4' lan; LISTEN="$ANSWER"
  if [[ "$LISTEN" != lan && "$LISTEN" != local ]]; then
    [[ "$LISTEN" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || { echo '监听范围须为 lan、local 或内网IPv4。' >&2; exit 2; }
  fi
}
ask '部署模式：1=HTTPS，2=局域网HTTP验收（明文，禁止正式业务）' 1
case "$ANSWER" in
  1)
    MODE=https
    ask '稳定内部域名根地址，例如https://office.company.internal' ''; URL="$ANSWER"
    [[ "$URL" == https://* && "$URL" != *'@'* ]] || { echo 'HTTPS模式必须填写https://根地址。' >&2; exit 2; }
    ask_listen
    ask '现有证书fullchain的绝对路径' ''; CERT="$ANSWER"
    ask '现有证书私钥的绝对路径（只引用文件，不读取内容）' ''; KEY="$ANSWER"
    [[ "$CERT" == /* && -f "$CERT" && "$KEY" == /* && -f "$KEY" ]] || { echo '证书路径不存在；未自动创建或信任企业证书。' >&2; exit 2; }
    ;;
  2)
    MODE=lan-http-demo
    ask '确认HTTP明文验收模式，输入yes继续' no
    [[ "$ANSWER" == yes ]] || exit 2
    ask '员工访问用的稳定内网IPv4（HTTP演示无法跨IP保持登录身份）' "$IP"; PUBLIC_HOST="$ANSWER"
    [[ "$PUBLIC_HOST" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || { echo 'HTTP演示的访问地址须为IPv4。' >&2; exit 2; }
    ask '服务端口' 8800; PORT="$ANSWER"
    [[ "$PORT" =~ ^[0-9]{1,5}$ ]] && ((10#$PORT >= 1 && 10#$PORT <= 65535)) || { echo '端口须为1至65535。' >&2; exit 2; }
    ask_listen
    URL="http://$PUBLIC_HOST:$PORT"
    ;;
  *) echo '请选择1或2。' >&2; exit 2 ;;
esac
ask '服务类型：0=普通数据服务，1=含智能助手运行能力（仍需配置模型）' 0; AGENT="$ANSWER"
[[ "$AGENT" == 0 || "$AGENT" == 1 ]] || { echo '请选择0或1。' >&2; exit 2; }
ask '业务镜像（留空则从交付源码构建）' ''; IMAGE="$ANSWER"
ask '实例目录（相对交付目录；不要与旧实例共用）' runtime/instance; INSTANCE="$ANSWER"
ask '初始管理员登录名' admin; ADMIN="$ANSWER"
ask '初始管理员显示名称' 企业管理员; NAME="$ANSWER"
printf '地址：%s\n实例目录：%s\n管理员：%s（%s）\n密码由程序随机生成，不在配置或终端回显。\n已有数据库请使用.env的OFFICE_DATABASE_*文件引用配置，不会自动接管。\n' "$URL" "$INSTANCE" "$ADMIN" "$NAME" >&2
ask '保存公开配置并继续？输入yes确认' yes
[[ "$ANSWER" == yes ]] || exit 2
mkdir -p "$(dirname "$DEST")"
TEMP="$(mktemp "${DEST}.tmp.XXXXXX")"
trap 'rm -f "$TEMP"' EXIT
chmod 600 "$TEMP"
{
  printf 'OFFICE_AGENT=%s\nOFFICE_IMAGE=%s\n' "$AGENT" "$IMAGE"
  printf 'OFFICE_DEPLOYMENT_MODE=%s\nOFFICE_PUBLIC_URL=%s\nOFFICE_LISTEN_ADDRESS=%s\nOFFICE_INSTANCE_DIR=%s\nOFFICE_ADMIN_USERNAME=%s\nOFFICE_ADMIN_NAME=%s\n' "$MODE" "$URL" "$LISTEN" "$INSTANCE" "$ADMIN" "$NAME"
  if [[ "$MODE" == https ]]; then printf 'OFFICE_CERTIFICATE_FILE=%s\nOFFICE_PRIVATE_KEY_FILE=%s\n' "$CERT" "$KEY"; fi
} > "$TEMP"
mv "$TEMP" "$DEST"
trap - EXIT
printf '配置已保存：%s。安装器会再次严格校验参数；不会删除旧卷或重置旧密码。\n' "$DEST"
