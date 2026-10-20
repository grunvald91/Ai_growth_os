#!/usr/bin/env bash
# Read-only diagnostics for Studio login and new-company problems (2026-10-19/20).
# Public log: prints only dates, normalized paths, status codes, counters and exception types.
# No IPs, no logins, no passwords, no query strings, no company data. Changes nothing.
set -uo pipefail
echo "== nginx growth.servgo.ru, last 3 days"
python3 - <<'PY'
import glob, gzip, re, collections, datetime
line_re = re.compile(r'^\S+ \S+ (\S+) \[([^\]]+)\] "(\S+) (\S+) [^"]*" (\d{3}) (\d+) "[^"]*" "([^"]*)"')
uuid = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)
msk = datetime.timezone(datetime.timedelta(hours=3))
counts, detail = collections.Counter(), []
for name in sorted(glob.glob('/var/log/nginx/*access*.log*')):
    opener = gzip.open if name.endswith('.gz') else open
    try:
        handle = opener(name, 'rt', errors='replace')
    except OSError:
        continue
    with handle:
        for line in handle:
            m = line_re.match(line)
            if not m:
                continue
            user, when, method, path, status, size, agent = m.groups()
            try:
                stamp = datetime.datetime.strptime(when, '%d/%b/%Y:%H:%M:%S %z')
            except ValueError:
                continue
            if stamp < cutoff:
                continue
            path = uuid.sub(':id', path.split('?', 1)[0])
            if not path.startswith(('/studio', '/products', '/intelligence')) or path.startswith('/studio/assets'):
                continue
            if user == '-':
                shape = 'nouser'
            elif not user.isascii():
                shape = 'login-nonascii'
            elif user != user.strip():
                shape = 'login-spaces'
            elif user != user.lower():
                shape = 'login-uppercase'
            else:
                shape = 'login-lowercase'
            device = 'mobile' if re.search(r'Mobile|Android|iPhone', agent) else 'desktop'
            counts[(method, path, status)] += 1
            if (any(k in path for k in ('/studio/new', '/profile', '/accounts'))
                    or status in {'401', '403', '413', '415', '422', '429', '499', '500', '502', '503', '504'}):
                detail.append(f"{stamp.astimezone(msk):%m-%d %H:%M:%S} {method:4} {status} {int(size):>7} {device:7} {shape:16} {path}")
print("-- counts for 4xx/5xx and company/profile/accounts paths")
for (method, path, status), n in sorted(counts.items(), key=lambda kv: (kv[0][1], kv[0][2])):
    if status[0] in '45' or any(k in path for k in ('/new', '/profile', '/accounts')):
        print(f"{n:5} {method:4} {status} {path}")
print("-- timeline (Moscow time), last 300")
for row in detail[-300:]:
    print(row)
PY
echo
echo "== api container, last 3 days: exception types, app frames, 4xx/5xx"
cat > /tmp/growth_logparse.py <<'PY'
import re, sys, collections
types, frames, access = collections.Counter(), collections.Counter(), collections.Counter()
uuid = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}")
frame_re = re.compile(r'^\s*File "(/app/app/[^"]+|/usr/local/lib/python[^"]+/site-packages/(?:starlette|fastapi|sqlalchemy|httpx|anyio)[^"]*)", line (\d+), in (\w+)')
for line in sys.stdin:
    m = frame_re.match(line)
    if m:
        where = m.group(1).split("site-packages/")[-1]
        frames[f"{where}:{m.group(2)} {m.group(3)}"] += 1
        continue
    m = re.match(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Timeout|Interrupt|NotReady|Denied|Failed))\b", line.strip())
    if m:
        types[m.group(1)] += 1
        continue
    m = re.search(r'"(GET|POST) (/[^ ?"]*)[^"]*" (\d{3})', line)
    if m and m.group(3)[0] in "45" and not m.group(2).startswith("/studio/assets"):
        access[(m.group(1), uuid.sub(":id", m.group(2)), m.group(3))] += 1
    m = re.search(r'studio_origin_rejected .*"reason": "([^"]+)".*"path": "([^"]+)"', line)
    if m:
        access[("ORIGIN", uuid.sub(":id", m.group(2)), m.group(1))] += 1
    m = re.search(r"(company_intake\w*)", line)
    if m:
        types["log:" + m.group(1)] += 1
print("-- exception types / log markers")
for k, n in types.most_common(40):
    print(f"{n:5} {k}")
print("-- frames")
for k, n in frames.most_common(50):
    print(f"{n:5} {k}")
print("-- 4xx/5xx and origin rejections")
for k, n in access.most_common(50):
    print(f"{n:5} {k}")
PY
docker logs --since 72h ai-growth-os-api-1 2>&1 | python3 /tmp/growth_logparse.py
rm -f /tmp/growth_logparse.py
echo
echo "== database, last 3 days (counts only)"
docker exec ai-growth-os-postgres-1 psql -U ai_growth -d ai_growth -At -F ' | ' -c "
select 'products_created', count(*) from products where created_at > now() - interval '3 days';
select 'intake_task', status, count(*) from tasks where assigned_agent='company_intake' and created_at > now() - interval '3 days' group by status;
select 'intake_run', status, coalesce(error,'-'), count(*), max(extract(epoch from (finished_at-started_at)))::int as max_s from agent_runs where agent_name='company_intake' and created_at > now() - interval '3 days' group by status, error;
select 'intake_artifact', kind, status, count(*) from artifacts a join tasks t on t.id=a.task_id where t.assigned_agent='company_intake' and a.created_at > now() - interval '3 days' group by kind, status;
" 2>&1 | sed -E 's/[0-9a-f]{8}-[0-9a-f-]{27}/:id/g'
echo
echo "== studio accounts (shape only, no logins)"
docker exec -i ai-growth-os-api-1 python - <<'PY'
import json
from pathlib import Path
from app.core.config import get_settings
s = get_settings()
path = Path(s.studio_users_file) if s.studio_users_file else Path(s.products_root) / "_studio" / "accounts.json"
print("users_file_configured:", bool(s.studio_users_file), "exists:", path.exists())
print("htpasswd_configured:", bool(s.studio_htpasswd_file), "admin_env_login:", bool(s.studio_admin_username))
if s.studio_htpasswd_file:
    try:
        lines = [l for l in Path(s.studio_htpasswd_file).read_text().splitlines() if l and not l.startswith("#")]
        print("htpasswd_entries:", len(lines), "hash_kinds:", sorted({l.split(":", 1)[1][:6] for l in lines}))
    except OSError as e:
        print("htpasswd_unreadable:", type(e).__name__)
if path.exists():
    data = json.loads(path.read_text())
    for name, acc in data.items():
        print("account", "lowercase" if name == name.lower() else "has-uppercase",
              "role=" + str(acc.get("role")), "companies=" + str(len(acc.get("product_ids", []))),
              "hash=" + acc.get("password_hash", "").split("$")[0], "created=" + acc.get("created_at", "")[:16])
PY
echo
echo "== containers"
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep ai-growth
echo DIAG_OK
