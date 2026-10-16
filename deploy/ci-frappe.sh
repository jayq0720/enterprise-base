#!/usr/bin/env bash
# 仅用于临时 CI 站点，不接受生产凭据，不使用现有数据卷。
set -euo pipefail
password=$(openssl rand -hex 32)
webhook_secret=$(openssl rand -hex 32)
echo "::add-mask::$password"
echo "::add-mask::$webhook_secret"
docker network create eb-contract
docker run -d --name eb-db --network eb-contract \
  -e MARIADB_ROOT_PASSWORD="$password" mariadb:10.11.14 \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci
docker run -d --name eb-redis --network eb-contract redis:6.2.20-alpine
ready=false
for attempt in {1..60}; do
  if docker exec eb-db healthcheck.sh --connect --innodb_initialized >/dev/null 2>&1; then
    ready=true; break
  fi
  sleep 2
done
test "$ready" = true
if test -f deploy/data/ci-frappe.tar.gz; then
  docker load -i deploy/data/ci-frappe.tar.gz
else
source_dir=$(mktemp -d)
git -C "$source_dir" init
git -C "$source_dir" remote add origin https://github.com/frappe/frappe_docker
git -C "$source_dir" fetch --depth 1 origin 3556154478dffca8f3bcdee47d43b65d04a2c962
git -C "$source_dir" checkout --detach FETCH_HEAD
docker build -f "$source_dir/images/custom/Containerfile" --secret id=apps_json,src=deploy/apps.lock.json \
  --build-arg FRAPPE_BRANCH=v15.123.1 --build-arg PYTHON_VERSION=3.12.12 \
  --build-arg DEBIAN_BASE=bookworm --build-arg NODE_VERSION=22.20.0 \
  --build-arg INSTALL_CHROMIUM=false --build-arg BUILDKIT_SYNTAX=docker/dockerfile:1.19.0 \
  -t eb-frappe-contract:0.1.0 "$source_dir"
mkdir -p deploy/data
docker builder prune -af
docker save eb-frappe-contract:0.1.0 | gzip > deploy/data/ci-frappe.tar.gz
fi
docker run --name eb-frappe --network eb-contract --entrypoint bash \
  -e EB_TEST_PASSWORD="$password" -e EB_SYNC_WEBHOOK_SECRET="$webhook_secret" \
  -v "$PWD:/workspace:ro" eb-frappe-contract:0.1.0 /workspace/deploy/ci-frappe-container.sh
mkdir -p artifacts
docker cp eb-frappe:/home/frappe/frappe-bench/apps/enterprise_base/enterprise_base/fixtures/. artifacts/
docker cp eb-frappe:/home/frappe/frappe-bench/apps/enterprise_base/enterprise_base/setup_fixtures/. artifacts/
