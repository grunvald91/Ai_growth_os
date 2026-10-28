#!/bin/bash
# Runs on the server. Usage: server_nginx_blog.sh inspect|apply
# Prints only structure (server_name, listen, location, proxy_pass); never certificates, keys or secrets.
set -u
MODE="${1:-inspect}"
BACKUP=/opt/ai_growth_os/origin-fixes
mkdir -p "$BACKUP"

structure() {  # nginx -T reduced to the lines that describe routing
  nginx -T 2>/dev/null | grep -E "^# configuration file |^\s*(server_name|listen|location|proxy_pass|return|root|try_files|include) " \
    | grep -vE "ssl_certificate|ssl_certificate_key|password|token"
}

if [ "$MODE" = "inspect" ]; then
  echo "== nginx -v"; nginx -v 2>&1
  echo "== enabled sites"; ls -l /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null
  echo "== routing structure"; structure
  echo "== local probes"
  for u in "servgo.ru /blog" "servgo.ru /llms.txt" "promo.servgo.ru /blog" "growth.servgo.ru /public/blog/servgo/articles"; do
    set -- $u
    printf '%s%s -> ' "$1" "$2"
    curl -ksS -o /dev/null -w '%{http_code} %{content_type}\n' --max-time 10 --resolve "$1:443:127.0.0.1" "https://$1$2"
  done
  echo "== booking_bot web service"; systemctl is-active booking-bot; ss -ltnp 2>/dev/null | grep -E ":(8080|8000|8081|3000) " | sed 's/users:.*//'
  exit 0
fi

# ---- apply ----
GROWTH=$(grep -lE "server_name\s+[^;]*growth\.servgo\.ru" /etc/nginx/sites-enabled/* /etc/nginx/sites-available/* /etc/nginx/conf.d/* 2>/dev/null | head -1)
[ -n "$GROWTH" ] || { echo "growth.servgo.ru site config not found"; exit 1; }
GROWTH=$(readlink -f "$GROWTH")
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a "$GROWTH" "$BACKUP/$(basename "$GROWTH").before-public-blog-$STAMP"
echo "backup: $BACKUP/$(basename "$GROWTH").before-public-blog-$STAMP"

python3 /tmp/nginx_public_blog.py "$GROWTH" || { cp -a "$BACKUP/$(basename "$GROWTH").before-public-blog-$STAMP" "$GROWTH"; exit 1; }

if ! nginx -t 2>&1; then
  cp -a "$BACKUP/$(basename "$GROWTH").before-public-blog-$STAMP" "$GROWTH"; echo "nginx -t failed, restored"; exit 1
fi
systemctl reload nginx
code=$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 --resolve growth.servgo.ru:443:127.0.0.1 https://growth.servgo.ru/public/blog/servgo/articles)
echo "growth feed status: $code"
[ "$code" = "200" ] || [ "$code" = "404" ] || { cp -a "$BACKUP/$(basename "$GROWTH").before-public-blog-$STAMP" "$GROWTH"; nginx -t && systemctl reload nginx; echo "restored"; exit 1; }
echo "applied step 1 (feed). Step 2 (servgo.ru/blog) is applied only after inspect shows how servgo.ru is routed."
