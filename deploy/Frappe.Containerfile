# 在官方 frappe_docker 构建结果中安装本仓库 App，不修改上游源码。
FROM enterprise-base/frappe:15.123.1-hrms15.64.3-wiki3.4.0
COPY --chown=frappe:0 . /home/frappe/frappe-bench/apps/enterprise_base
RUN /home/frappe/frappe-bench/env/bin/pip install --no-deps -e /home/frappe/frappe-bench/apps/enterprise_base
