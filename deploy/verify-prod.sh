#!/bin/bash
# ============================================================================
# 生产环境验收：逐项检查「切完 prod 之后该成立的每一件事」
#
# 用法：bash deploy/verify-prod.sh [域名]
#
# 这些检查对应真实踩过的坑，不是为了凑数：
#   - HTTP 不跳转 HTTPS  → 用户能用明文访问，cookie 被中间人截获
#   - cookie 缺 Secure    → HTTPS 页面下 cookie 仍可能走明文
#   - 8000 仍对外可达     → TLS 形同虚设，绕过即可访问
#   - /metrics 对外开放   → 运行状况泄露
#   - 页脚无备案号        → 会被管局责令整改
# ============================================================================
set -uo pipefail

DOMAIN="${1:-reelagent.online}"
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31m✗\033[0m %s\n' "$*"; }
skip() { printf '  \033[33m-\033[0m %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

head_ "1. HTTPS 可用"
for ep in /livez /readyz /health; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "https://${DOMAIN}${ep}" 2>/dev/null || echo 000)
  [ "$CODE" = "200" ] && ok "https${ep} -> 200" || bad "https${ep} -> $CODE"
done

head_ "2. HTTP 自动跳转 HTTPS"
LOC=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -m 10 "http://${DOMAIN}/" 2>/dev/null)
echo "     $_GET $LOC"
case "$LOC" in
  301*|308*) ok "HTTP 跳转到 HTTPS" ;;
  *) bad "HTTP 没有跳转（期望 301/308），当前: $LOC" ;;
esac

head_ "3. 证书有效性"
CERT=$(echo | openssl s_client -connect "${DOMAIN}:443" -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -issuer -subject -dates 2>/dev/null)
if [ -n "$CERT" ]; then
  echo "$CERT" | sed 's/^/     /'
  echo "$CERT" | grep -qi "Let's Encrypt\|R10\|R11\|E5\|E6" && ok "证书由 Let's Encrypt 签发" || skip "签发者不是 Let's Encrypt（可能是其他 CA）"
  END=$(echo "$CERT" | grep notAfter | sed 's/notAfter=//')
  if [ -n "$END" ]; then
    END_TS=$(date -d "$END" +%s 2>/dev/null || echo 0)
    NOW_TS=$(date +%s)
    DAYS=$(( (END_TS - NOW_TS) / 86400 ))
    [ "$DAYS" -gt 20 ] && ok "证书 ${DAYS} 天后到期" || bad "证书仅剩 ${DAYS} 天，续期可能异常"
  fi
else
  bad "读不到证书"
fi

head_ "4. 安全响应头"
HDR=$(curl -sI -m 10 "https://${DOMAIN}/" 2>/dev/null)
echo "$HDR" | grep -qi 'strict-transport-security' && ok "有 HSTS" || bad "缺 HSTS"
echo "$HDR" | grep -qi 'x-frame-options'          && ok "有 X-Frame-Options" || bad "缺 X-Frame-Options"
echo "$HDR" | grep -qi 'x-content-type-options'   && ok "有 X-Content-Type-Options" || bad "缺 X-Content-Type-Options"
echo "$HDR" | grep -qi '^server: caddy'           && skip "Server 头显示 caddy（配置里要求隐藏）" || ok "未暴露 Server 版本"

head_ "5. Cookie 是否带 Secure"
COOKIE=$(curl -sI -m 10 "https://${DOMAIN}/login" 2>/dev/null | grep -i '^set-cookie')
if [ -n "$COOKIE" ]; then
  echo "$COOKIE" | sed 's/^/     /'
  echo "$COOKIE" | grep -qi 'secure'   && ok "cookie 带 Secure"   || bad "cookie 缺 Secure 标志"
  echo "$COOKIE" | grep -qi 'httponly' && ok "cookie 带 HttpOnly" || bad "cookie 缺 HttpOnly"
  echo "$COOKIE" | grep -qi 'samesite' && ok "cookie 带 SameSite" || skip "cookie 缺 SameSite"
else
  skip "该页未下发 cookie，无法检查（可试 /signup）"
fi

head_ "6. 端口暴露面"
for p in 8000 5432 6379; do
  if timeout 5 bash -c "cat < /dev/null > /dev/tcp/${DOMAIN}/${p}" 2>/dev/null; then
    bad "端口 $p 从公网可达 —— 应当只在内网"
  else
    ok "端口 $p 未对外暴露"
  fi
done

head_ "7. /metrics 不应公开"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "https://${DOMAIN}/metrics" 2>/dev/null || echo 000)
[ "$CODE" = "404" ] && ok "/metrics 已隐藏（404）" || bad "/metrics 返回 $CODE，应当被 Caddy 拦掉"

head_ "8. 页脚备案号"
FOOT=$(curl -s -m 10 "https://${DOMAIN}/" 2>/dev/null)
echo "$FOOT" | grep -q 'beian.miit.gov.cn' && ok "页脚有备案号并链接工信部" || bad "页脚没有备案号（会被责令整改）"
if echo "$FOOT" | grep -q 'beian.miit.gov.cn'; then
  echo "$FOOT" | grep -oE '[^>]*ICP备[^<]*' | head -2 | sed 's/^/     /'
fi

head_ "9. 应用自检"
docker exec reelagent-api python -c "import urllib.request;print('     /livez ->', urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=3).status)" 2>/dev/null \
  && ok "容器内自检通过" || bad "容器内自检失败"
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps --format 'table {{.Name}}\t{{.Status}}' 2>/dev/null | sed 's/^/     /'

head_ "结果"
printf '  通过 \033[32m%d\033[0m 项，失败 \033[31m%d\033[0m 项\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && { printf '\n  \033[32m全部通过\033[0m\n'; exit 0; } || { printf '\n  \033[31m有失败项，按上面逐条修\033[0m\n'; exit 1; }
