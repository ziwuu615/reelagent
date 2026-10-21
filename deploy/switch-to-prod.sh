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

ICP_NUMBER="${1:-}"
DOMAIN="${REEL_DOMAIN:-reelagent.online}"
ACME_EMAIL="${REEL_ACME_EMAIL:-}"

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
sed -i 's/\r$//' .env
grep -E '^REEL_(ENV|BASE_URL|ICP_BEIAN|DOMAIN)=' .env | sed 's/^/  /'

step "6. 校验 compose 合并结果"
# 关键：确认 api 的端口映射被 !reset 清干净了。
# 如果 8000 仍绑在 0.0.0.0 上，等于绕过 TLS 留了个后门。
MERGED=$(docker compose -f docker-compose.yml -f docker-compose.prod.yml config 2>&1)
echo "$MERGED" | grep -q 'published: "8000"' && die "api 仍然对外暴露 8000，!reset 没生效，请检查 Compose 版本（需 >= 2.24）"
echo "$MERGED" | grep -q 'published: "443"' || die "compose 合并后没有 443 映射"
green "  api 不再暴露宿主端口，Caddy 接管 80/443"

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
