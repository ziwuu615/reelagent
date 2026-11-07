#!/bin/bash
# ============================================================================
# 备案通过后：把站点切到「HTTPS + prod 模式」
#
# 用法（在服务器上执行）：
#     bash deploy/switch-to-prod.sh 沪ICP备XXXXXXXX号
#
# 前置条件（脚本会逐条检查，不满足就退出）：
#   1. DNS 的 A 记录已指向本机公网 IP（两条：@ 和 www）
#   2. 80 端口从公网可达（Let's Encrypt 的 HTTP-01 验证需要）
#   3. ICP 备案已通过（否则阿里云会拦截 80/443，Caddy 拿不到证书）
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1

# 从 .env 读取单个键的值。
#
# 为什么不直接 `source .env`：.env 里有这类值
#     REEL_SECURITY__ALLOWED_HOSTS=["reelagent.online","www.reelagent.online"]
# source 会做引号移除，把 ["a","b"] 变成 [a,b]，值就被改坏了。
# 这份脚本只拿几个简单的键（域名、邮箱），用 grep+cut 取等号右边最安全：
# 不做解析、不 eval、不展开变量。
env_get() {
  local key="$1"
  [ -f .env ] || return 0
  grep -m1 "^${key}=" .env 2>/dev/null | cut -d= -f2- | tr -d '\r'
}

ICP_NUMBER="${1:-}"
# 优先级：环境变量 > .env > 默认值。
#
# 之前这里只读环境变量，于是出现「.env 里明明填了邮箱，脚本仍报缺少」——
# bash 不像 docker compose 那样自动加载 .env，必须显式读。
DOMAIN="${REEL_DOMAIN:-$(env_get REEL_DOMAIN)}"
DOMAIN="${DOMAIN:-reelagent.online}"
ACME_EMAIL="${REEL_ACME_EMAIL:-$(env_get REEL_ACME_EMAIL)}"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
step()  { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

die() { red "  ✗ $*"; exit 1; }

# 修改 .env 里的某个键：存在则替换，不存在则追加
set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    # 用 | 作分隔符，值里可能含 / 和 :（URL、JSON）
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

step "1. 参数检查"
[ -n "$ICP_NUMBER" ] || die "缺少 ICP 备案号。用法：bash deploy/switch-to-prod.sh 沪ICP备XXXXXXXX号"
[ -n "$ACME_EMAIL" ] || die "缺少 REEL_ACME_EMAIL（在 .env 里设置，Let's Encrypt 要求联系人邮箱）"
echo "  域名:   $DOMAIN"
echo "  备案号: $ICP_NUMBER"
echo "  ACME:   $ACME_EMAIL"

step "2. DNS 必须已指向本机"
MY_IP=$(curl -s -m 10 -4 ifconfig.me)
echo "  本机公网 IP: $MY_IP"
for name in "$DOMAIN" "www.$DOMAIN"; do
  RESOLVED=$(getent ahostsv4 "$name" 2>/dev/null | awk 'NR==1{print $1}')
  echo "  $name -> ${RESOLVED:-（无解析）}"
  if [ "$RESOLVED" != "$MY_IP" ]; then
    die "$name 没有解析到 $MY_IP。请先在云解析 DNS 里把 A 记录改成本机 IP 并启用。"
  fi
done
green "  DNS 正确"

step "3. 操作系统层面 80 端口是否在监听/可绑"
if ss -lntp 2>/dev/null | grep -q ':80 '; then
  echo "  ⚠ 80 端口已被占用（可能是宿主机的 nginx/apache），Caddy 将无法绑定"
  ss -lntp 2>/dev/null | grep ':80 ' | sed 's/^/    /'
  die "请先停掉占用 80 端口的服务"
fi
green "  80 端口空闲"

step "4. 备份当前 .env"
cp .env ".env.bak.$(date +%Y%m%d_%H%M%S)"
echo "  已备份"

step "5. 写入生产配置"
set_env REEL_ENV "prod"
set_env REEL_BASE_URL "https://${DOMAIN}"
set_env REEL_SECURITY__HTTPS_ONLY "true"
# localhost / 127.0.0.1 必须保留：容器健康检查打的是 127.0.0.1/livez。
# 只写域名会让健康检查收到 400，容器被判为 unhealthy —— 而业务本身正常。
set_env REEL_SECURITY__ALLOWED_HOSTS "[\"${DOMAIN}\",\"www.${DOMAIN}\",\"localhost\",\"127.0.0.1\",\"${MY_IP}\"]"
set_env REEL_ICP_BEIAN "$ICP_NUMBER"
set_env REEL_DOMAIN "$DOMAIN"
set_env REEL_ACME_EMAIL "$ACME_EMAIL"
set_env API_CPUS "1.0"
set_env API_MEMORY "640m"
set_env WORKER_CPUS "1.5"
set_env WORKER_MEMORY "1024m"
set_env CADDY_CPUS "0.5"
set_env CADDY_MEMORY "128m"

# ---- 合规与留存：这三项不写，api 会在 step 7 直接起不来 ----
#
# 生产校验器把它们列为硬门槛（见 reelagent_cloud/config.py 的
# _validate_environment）：缺任何一项都会抛 ValidationError，容器反复重启，
# 而 caddy 的 depends_on: api healthy 永远不满足 —— 表现是「整站 502」，
# 但真正的原因藏在 api 容器日志里，很容易误判成证书问题。
#
# 图像审核必须是语义级：启发式只能发现纯色/过小，对涉黄涉暴涉政无感，
# 拿它当合规审核等于备案材料造假。
set_env REEL_COMPLIANCE__IMAGE_MODERATION "dashscope"

# 日志留存：网络安全法要求不低于六个月。
# SINK=both 让应用自己落盘轮转，这样「留了 180 天」不需要依赖外部平台自证。
# 文件名由 docker-compose.prod.yml 按服务分别注入（api.log / worker.log），
# 因为两个进程共享 artifacts 卷，写同一个文件会互相截断。
set_env REEL_OBSERVABILITY__LOG_SINK "both"
set_env REEL_OBSERVABILITY__LOG_RETENTION_DAYS "180"

sed -i 's/\r$//' .env
grep -E '^REEL_(ENV|BASE_URL|ICP_BEIAN|DOMAIN|COMPLIANCE|OBSERVABILITY__LOG)' .env | sed 's/^/  /'

step "6. 校验 compose 合并结果"
# !reset 需要 Compose >= 2.24。低版本会静默忽略它，
# 于是 8000 继续对外暴露而脚本浑然不觉 —— 所以先验版本，再看结果。
COMPOSE_RAW=$(docker compose version --short 2>/dev/null | tr -d 'v\n ')
if [ -z "$COMPOSE_RAW" ]; then
  die "无法读取 docker compose 版本，请确认已安装 compose v2 插件"
fi
# sort -V 做版本比较：2.24 以下视为不安全
if [ "$(printf '%s\n2.24\n' "$COMPOSE_RAW" | sort -V | head -1)" != "2.24" ]; then
  die "docker compose 版本 $COMPOSE_RAW 过低（需 >= 2.24，!reset 从该版本起可用）。请升级后重跑。"
fi
echo "  compose 版本: $COMPOSE_RAW"

# 关键：确认 api 的端口映射被 !reset 清干净了。
# 如果 api 仍 published 端口，等于绕过 TLS 留了个后门。
MERGED=$(docker compose -f docker-compose.yml -f docker-compose.prod.yml config 2>&1)
echo "$MERGED" | grep -q 'published: "8000"' && die "api 仍然对外暴露 8000，!reset 没生效，请检查 .env 里的 REEL_BIND_ADDR"
echo "$MERGED" | grep -q 'published: "443"' || die "compose 合并后没有 443 映射"
# 两个服务各自写日志文件，否则轮转会互相截断
echo "$MERGED" | grep -q 'logs/api.log' || die "api 缺少按服务区分的 LOG_FILE"
echo "$MERGED" | grep -q 'logs/worker.log' || die "worker 缺少按服务区分的 LOG_FILE"
green "  api 不再暴露宿主端口，Caddy 接管 80/443，日志按服务分文件"

step "7. 重启为生产编排"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate 2>&1 | tail -12

step "8. 等待证书签发（最长 180 秒）"
# 首次签发要完成 HTTP-01 挑战，通常 10–40 秒。
OK=0
for i in $(seq 1 36); do
  sleep 5
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 8 "https://${DOMAIN}/livez" 2>/dev/null || echo "000")
  if [ "$CODE" = "200" ]; then OK=1; echo "  第 $((i*5)) 秒：HTTPS 已可用"; break; fi
  [ $((i % 3)) -eq 0 ] && echo "  第 $((i*5)) 秒：HTTP $CODE ……"
done

if [ "$OK" -ne 1 ]; then
  red "  ✗ 180 秒内未能拿到有效证书。排查顺序："
  echo "    1) docker compose logs caddy --tail 50   （看 ACME 报错）"
  echo "    2) 备案是否真的通过了？未备案时阿里云会拦截 80/443"
  echo "    3) 安全组 80/443 是否放行"
  exit 1
fi

green ""
green "================ 切换完成 ================"
echo "  正式地址: https://${DOMAIN}"
bash deploy/verify-prod.sh "$DOMAIN" || true
