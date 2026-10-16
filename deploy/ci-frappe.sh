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
docker run --name eb-frappe --network eb-contract --entrypoint bash \
  -e EB_TEST_PASSWORD="$password" -e EB_SYNC_WEBHOOK_SECRET="$webhook_secret" \
  -v "$PWD:/workspace:ro" frappe/erpnext:v15.123.0 /workspace/deploy/ci-frappe-container.sh
mkdir -p artifacts
docker cp eb-frappe:/home/frappe/frappe-bench/apps/enterprise_base/enterprise_base/fixtures/. artifacts/
