#!/usr/bin/env bash
# 源码目录可能以noexec挂载；复制公共初始化脚本后由官方entrypoint source。
set -euo pipefail
cp /opt/office/init.sh /docker-entrypoint-initdb.d/10-office.sh
chmod 0644 /docker-entrypoint-initdb.d/10-office.sh
exec /usr/local/bin/docker-entrypoint.sh postgres
