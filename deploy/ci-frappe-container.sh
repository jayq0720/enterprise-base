#!/usr/bin/env bash
set -euo pipefail
cd /home/frappe/frappe-bench
bench get-app --branch v15.64.3 --skip-assets https://github.com/frappe/hrms
bench get-app --branch v3.4.0 --skip-assets https://github.com/frappe/wiki
cp -r /workspace/frappe_apps/enterprise_base apps/enterprise_base
./env/bin/pip install -e apps/enterprise_base
echo enterprise_base >> sites/apps.txt
bench set-config -g db_host eb-db
bench set-config -g redis_cache redis://eb-redis:6379
bench set-config -g redis_queue redis://eb-redis:6379
bench set-config -g redis_socketio redis://eb-redis:6379
for site in contract.local import.local; do
  bench new-site "$site" --db-root-password "$EB_TEST_PASSWORD" \
    --admin-password "$EB_TEST_PASSWORD" --mariadb-user-host-login-scope '%' --install-app erpnext
  for app in hrms wiki enterprise_base; do bench --site "$site" install-app "$app"; done
  bench --site "$site" set-config allow_tests true
  bench --site "$site" execute frappe.db.set_single_value --args '["System Settings",{"language":"en","time_zone":"Asia/Shanghai","enable_telemetry":0}]'
  bench --site "$site" run-tests --app enterprise_base --skip-test-records
  if test "$site" = contract.local; then
    bench --site "$site" execute enterprise_base.integration.export_configuration
  fi
done
echo '真实 Frappe 测试、配置导出及第二空站点导入完成。'
