# 后台入口实测（Docker 环境执行）

本文件用于验收本机初始化和公网来源限制。当前 GPT 环境没有 Docker Engine，以下步骤尚未执行，不能用原生 Caddy 探测替代。所有命令在 `deploy/` 执行。

## 1. 本机初始化

服务器 `secrets/.env` 保留 `EB_BIND=127.0.0.1`、`EB_ADMIN_ALLOW_CIDRS=127.0.0.1/32`，执行 `make up` 后：

```sh
curl --noproxy '*' --resolve dify.localhost:80:127.0.0.1 -s -o /dev/null -w '%{http_code}\n' http://dify.localhost/install
curl --noproxy '*' --resolve hr.localhost:80:127.0.0.1 -s -o /dev/null -w '%{http_code}\n' http://hr.localhost/login
```

预期 200 或正常的 3xx，不能是 403；浏览器完成两个管理员初始化。使用另一台机器访问服务器 80/443，回环模式应无法连接，不应收到后台页面。Docker Engine 必须 ≥28，保持默认 bridge/NAT、不开容器直路由，也不把回环入口转发至公网。

## 2. 公网来源与允许/拒绝

填写客户域名、HTTPS、对外绑定地址和管理员真实出口 CIDR（IPv4/IPv6 分别填写），执行 `make up`。模板中的白名单导入必须恢复，网桥网关不能作为管理员来源。

为核对 Caddy 看到的地址，先 `make prepare`，临时将下列块放进生成的 `data/Caddyfile` 的 Dify 和 HR 站点内、`route` 外。不改仓库模板。它删除请求头、URI（含查询串）、TLS 信息、响应头和用户标识，只在私密文件记录诊断信息，不记录请求体。

```caddyfile
log {
    output file /data/admin-source-check.jsonl {
        mode 0600
        roll_size 1MiB
        roll_keep 1
        roll_keep_for 1h
    }
    format filter {
        wrap json
        fields {
            request>headers delete
            request>uri delete
            request>tls delete
            resp_headers delete
            user_id delete
        }
    }
}
```

此处只为加载刚生成并加诊断的文件，执行：

```sh
docker compose --env-file secrets/.env -f docker-compose.yml up -d --force-recreate caddy
```

从两台独立外部机器测试两个后台：一台出口在白名单内，另一台不在。使用匿名 GET，不携带 Key、Cookie 或真实用户数据，分别对实际域名运行 `curl -4` 和 `curl -6`（同本机示例只输出状态码）。白名单内应为 200/正常 3xx，白名单外必须是 403；Dify 的 `/v1/chat-messages` 在白名单外也应为 403。不能只在服务器本机测试。

在服务器仅查看所需字段：

```sh
docker compose --env-file secrets/.env -f docker-compose.yml exec -T caddy cat /data/admin-source-check.jsonl |
uv run --python 3.12 python -c 'import json,sys; [print(json.dumps({"remote_ip":d.get("request",{}).get("remote_ip"),"status":d.get("status")})) for line in sys.stdin if (d:=json.loads(line))]'
```

将 `remote_ip` 与外部机器真实出口逐一比较，不能用转发头里的值作证据。如果都变成网关/共享代理地址，先收回 `EB_BIND=127.0.0.1` 并 `make up`，不要给网关放行。在维护窗口由运维评估将 Docker daemon 的 `userland-proxy` 设为 `false`，合并既有配置后重启 Docker，再逐项复测；这会影响该主机其它容器。仍不能保留真实来源时再评审 Caddy host 网络方案（需同时调整上游寻址），不能直接切换当前 Compose 网络模式。

没有 IPv6 连通条件则标为“IPv6 待测”，不能推断通过。核对结束后 `make up` 会重新生成配置、移除临时诊断块；清理私密诊断文件：

```sh
docker compose --env-file secrets/.env -f docker-compose.yml exec -T caddy sh -c 'rm -f /data/admin-source-check*.jsonl*'
```

交回证据只需：绑定模式、IPv4/IPv6、允许/拒绝状态码、来源是否与实际出口一致、测试时间；不贴原始日志、真实 IP、凭据或用户信息。日志格式依据 [Caddy 官方文档](https://caddyserver.com/docs/caddyfile/directives/log)。
