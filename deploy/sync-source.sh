#!/usr/bin/env bash
#
# 全量源码同步：把仓库的跟踪文件树整体覆盖到构建机。
#
# ============================================================================
# 为什么必须全量，而不是「只传这次改过的文件」
# ============================================================================
#
# 2026-10-22 发现生产镜像落后本地仓库 12 个文件、9 个文件从未上传，包括：
#   - reelagent/agents/director.py        prompt 保真度确定性补齐
#   - reelagent_cloud/api/templating.py   模板配置覆盖
#   - web/templates/partials/footer.html  ICP 备案号页脚
#   - reelagent/render/composer.py        音频对齐
#
# 表现极具欺骗性：**本地测试全绿，生产行为不同**。
# 当时排查「文本保真度为什么是 0.33」，我一度误判成「指标过严」——
# 实际是补齐代码压根没进镜像。核对部署版本应该是第一步。
#
# 根因是「只 scp 本轮改过的文件」这个习惯：跨多轮会话后，
# 只要有任意一轮漏传，偏差就会一直留在生产里，而且没有任何机制会报警。
#
# 现在的做法：git archive 导出整棵跟踪文件树 -> 整体解包覆盖。
# 代价是每次构建都全量，但构建本身有 Docker 层缓存，实测 484 秒可接受。
#
# ============================================================================
# 用法
# ============================================================================
#
#   1. 本地（仓库根目录）：
#        git archive --format=tar.gz -o /tmp/src.tar.gz HEAD
#        scp -i <key> /tmp/src.tar.gz root@<BUILD_HOST>:/root/src.tar.gz
#        scp -i <key> deploy/sync-source.sh root@<BUILD_HOST>:/root/
#
#   2. 构建机上：
#        bash /root/sync-source.sh
#
#   本脚本在**构建机**上运行（不是本地）。
#   需要构建机上已有：/opt/reelagent（含 .env）、/root/src.tar.gz、/root/xfer（推送上海用的私钥）
#
set -uo pipefail

REPO_DIR="${REPO_DIR:-/opt/reelagent}"
SRC_TARBALL="${SRC_TARBALL:-/root/src.tar.gz}"
DEPLOY_HOST="${DEPLOY_HOST:-8.153.158.193}"
SSH_KEY="${SSH_KEY:-/root/xfer}"

cd "$REPO_DIR" || { echo "✗ 找不到 $REPO_DIR"; exit 1; }

SSHOPT="-i $SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=15"

echo "=========== 1. 备份 .env（绝不能被源码覆盖）==========="
if [ ! -f .env ]; then
  echo "  ✗ 没有 .env，中止"; exit 1
fi
cp .env /root/env.backup
echo "  已备份到 /root/env.backup（$(wc -l < .env) 行）"

echo
echo "=========== 2. 解包全量源码 ==========="
[ -f "$SRC_TARBALL" ] || { echo "  ✗ 找不到 $SRC_TARBALL"; exit 1; }
tar -xzf "$SRC_TARBALL" -C "$REPO_DIR" || { echo "  ✗ 解包失败"; exit 1; }
echo "  解包完成（$(tar -tzf "$SRC_TARBALL" | wc -l) 个条目）"

echo
echo "  关键修复核对（任一为 0 说明同步有问题）："
check() {
  local f="$1" k="$2"
  local n
  n=$(grep -c "$k" "$f" 2>/dev/null || echo 0)
  printf "    %-56s %-30s %s\n" "$f" "$k" "$n"
}
check "reelagent/agents/director.py" "_enforce_clue_fidelity"
check "reelagent/eval/consistency.py" "base_image_coverage"
check "reelagent/pipeline/runner.py" "_repair_until_passing"
check "reelagent_cloud/api/templating.py" "set_template_settings"
check "reelagent_cloud/config.py" "icp_beian"

echo
echo "=========== 3. 确认 .env 未被改动 ==========="
if diff -q .env /root/env.backup > /dev/null; then
  echo "  .env 未变 ✓"
else
  echo "  ✗ .env 被改动了，中止"; exit 1
fi

echo
echo "=========== 4. 构建 ==========="
START=$(date +%s)
docker compose build \
  --build-arg APT_MIRROR=mirrors.aliyun.com \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ > /tmp/build.log 2>&1
RC=$?
echo "  退出码 $RC   耗时 $(( $(date +%s) - START )) 秒"
if [ "$RC" -ne 0 ]; then tail -20 /tmp/build.log; exit 1; fi

echo
echo "=========== 5. 镜像内核对（证明改动真的进了镜像，而不只是源码目录）==========="
for pair in \
  "reelagent/agents/director.py:_enforce_clue_fidelity" \
  "reelagent/eval/consistency.py:base_image_coverage" \
  "reelagent_cloud/api/templating.py:set_template_settings"; do
  f="${pair%%:*}"; k="${pair##*:}"
  n=$(docker run --rm --entrypoint sh reelagent:local -c "grep -c $k /app/$f" 2>/dev/null)
  printf "    %-56s 镜像内 × %s\n" "$f" "$n"
done

echo
echo "=========== 6. 传输到运行机并重启 ==========="
docker save reelagent:local | gzip -1 | ssh $SSHOPT "$DEPLOY_HOST" 'gunzip | docker load'
echo "  传输退出码 $?"
ssh $SSHOPT "$DEPLOY_HOST" \
  "cd $REPO_DIR && docker compose up -d --force-recreate api worker 2>&1 | tail -3 && sleep 30 && docker compose ps --format 'table {{.Name}}\t{{.Status}}'"

echo
echo "完成。建议随后做一次真实验证（真实供应商出片），确认行为符合预期。"
