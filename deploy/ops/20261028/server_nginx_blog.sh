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
# Inspect showed: servgo.ru/blog and the blog assets already reach BookingBot (127.0.0.1:8080); /llms.txt does not.
GROWTH=$(readlink -f /etc/nginx/sites-enabled/growth-servgo)
SERVGO=$(readlink -f /etc/nginx/sites-enabled/webapp)
[ -f "$GROWTH" ] && [ -f "$SERVGO" ] || { echo "site config not found"; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a "$GROWTH" "$BACKUP/growth-servgo.before-blog-$STAMP"
cp -a "$SERVGO" "$BACKUP/webapp.before-blog-$STAMP"
echo "backups: $BACKUP/*-$STAMP"

restore() {
  cp -a "$BACKUP/growth-servgo.before-blog-$STAMP" "$GROWTH"
  cp -a "$BACKUP/webapp.before-blog-$STAMP" "$SERVGO"
  nginx -t && systemctl reload nginx
  echo "RESTORED both files"
}

# 1. read-only feed on growth.servgo.ru
python3 /tmp/nginx_public_blog.py "$GROWTH" || { restore; exit 1; }

# 2. /llms.txt of servgo.ru goes to the BookingBot blog, next to the existing /blog routes
python3 - "$SERVGO" <<'PY' || { restore; exit 1; }
import sys
p = sys.argv[1]; s = open(p).read()
if "location = /llms.txt" in s:
    print("llms.txt already routed"); sys.exit(0)
anchor = "    location = /blog { proxy_pass http://127.0.0.1:8080;"
assert s.count(anchor) == 1, "blog anchor not found exactly once"
line = ("    location = /llms.txt { proxy_pass http://127.0.0.1:8080; proxy_set_header Host $host; "
        "proxy_set_header X-Real-IP $remote_addr; }\n")
open(p, "w").write(s.replace(anchor, line + anchor))
print("llms.txt routed")
PY

nginx -t 2>&1 || { restore; exit 1; }
systemctl reload nginx

feed=$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 --resolve growth.servgo.ru:443:127.0.0.1 https://growth.servgo.ru/public/blog/servgo/articles)
blog=$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 --resolve servgo.ru:443:127.0.0.1 https://servgo.ru/blog)
home=$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 --resolve servgo.ru:443:127.0.0.1 https://servgo.ru/)
studio=$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 --resolve growth.servgo.ru:443:127.0.0.1 https://growth.servgo.ru/studio)
echo "feed=$feed (200 once the platform is deployed, 404 before) blog=$blog home=$home studio=$studio"
if { [ "$feed" != "200" ] && [ "$feed" != "404" ]; } || [ "$blog" != "200" ] || [ "$home" != "200" ]; then restore; exit 1; fi
echo "== servgo.ru robots.txt (read-only)"; head -c 600 /opt/booking_bot/web/frontend/dist/robots.txt 2>/dev/null
