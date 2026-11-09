#!/bin/sh
set -eu
cd "$(dirname "$0")"
docker compose version >/dev/null
if [ -f warehouse-shelve-linux-amd64.tar ]; then
    docker load -i warehouse-shelve-linux-amd64.tar
fi
mkdir -p results config
docker compose up -d --wait --wait-timeout 120
printf 'Warehouse ready: http://<server-ip>:%s/whshelve/\n' "${WAREHOUSE_PORT:-8010}"
