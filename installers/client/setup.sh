#!/usr/bin/env bash
# 所有工具放入项目目录；不执行远程脚本，不修改PATH或全局CLI。
set -euo pipefail
for REQUIRED in curl tar awk; do
  if ! command -v "$REQUIRED" >/dev/null 2>&1; then
    echo "缺少$REQUIRED。Linux示例：sudo apt-get install curl tar ca-certificates；安装后重试。不会自动执行系统安装或修改权限。" >&2
    exit 2
  fi
done
if ! command -v shasum >/dev/null 2>&1 && ! command -v sha256sum >/dev/null 2>&1; then
  echo '缺少SHA256校验工具。Linux安装coreutils或使用系统shasum；不能跳过制品校验。' >&2
  exit 2
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="$ROOT/.office/tools/uv"
if [[ ! -x "$UV" ]]; then
  case "$(uname -s):$(uname -m)" in
    Darwin:arm64) TARGET=aarch64-apple-darwin ;;
    Darwin:x86_64) TARGET=x86_64-apple-darwin ;;
    Linux:aarch64) TARGET=aarch64-unknown-linux-gnu ;;
    Linux:x86_64) TARGET=x86_64-unknown-linux-gnu ;;
    *) echo '不支持的系统架构，请联系管理员' >&2; exit 2 ;;
  esac
  mkdir -p "$ROOT/.office"
  TEMP="$(mktemp -d "$ROOT/.office/download.XXXXXX")"
  trap 'rm -rf "$TEMP"' EXIT
  BASE="https://github.com/astral-sh/uv/releases/download/0.9.18/uv-$TARGET.tar.gz"
  curl --fail --location --connect-timeout 15 --max-time 300 --retry 2 --proto '=https' "$BASE" -o "$TEMP/uv.tar.gz"
  curl --fail --location --connect-timeout 15 --max-time 300 --retry 2 --proto '=https' "$BASE.sha256" -o "$TEMP/uv.sha256"
  EXPECTED="$(awk '{print $1}' "$TEMP/uv.sha256")"
  if command -v shasum >/dev/null; then ACTUAL="$(shasum -a 256 "$TEMP/uv.tar.gz" | awk '{print $1}')"
  else ACTUAL="$(sha256sum "$TEMP/uv.tar.gz" | awk '{print $1}')"; fi
  [[ "$EXPECTED" =~ ^[a-f0-9]{64}$ && "$ACTUAL" == "$EXPECTED" ]] || { echo 'uv摘要校验失败' >&2; exit 2; }
  mkdir "$TEMP/tools"
  tar -xzf "$TEMP/uv.tar.gz" --strip-components=1 -C "$TEMP/tools"
  mkdir -p "$ROOT/.office/tools"
  mv "$TEMP/tools/uv" "$UV"
  rm -rf "$TEMP"
  trap - EXIT
fi
exec "$UV" run --no-project --python 3.13 --no-config "$ROOT/setup_client.py" --uv "$UV"
