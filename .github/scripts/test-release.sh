#!/bin/sh
# 测试环境自动发布的 SSH 强制命令：只接受一个 40 位 main 提交号，交给 make release。
# 安装方法见 docs/测试环境自动部署.md；脚本要复制到仓库外，避免发布时改到自己。
set -eu
deploy=${1:?需要部署仓库目录}
sha=${SSH_ORIGINAL_COMMAND:-}
case "$sha" in
  "" | *[!0-9a-f]*)
    echo "自动发布:拒绝:参数不是提交号" >&2
    exit 2
    ;;
esac
if [ "${#sha}" -ne 40 ]; then
  echo "自动发布:拒绝:参数不是提交号" >&2
  exit 2
fi
cd "$deploy"
# GitHub 重跑同一个提交时不再发布；make release 会拒绝目标等于当前提交。
if [ "$(git rev-parse HEAD)" = "$sha" ]; then
  echo "自动发布:跳过:已是该提交"
  exit 0
fi
# 不接标准输入：需要人工二次确认的数据库回滚不会在这里发生。
exec make -s -C deploy release REVISION="$sha" </dev/null
