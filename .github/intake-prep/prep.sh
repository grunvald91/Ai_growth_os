#!/usr/bin/env bash
# Runs on the server. Prepares production for PR #141 (company intake with files):
#  1. a base image that has python-multipart (built on the current base, nothing else changes);
#  2. nginx upload limit 26m for growth.servgo.ru.
# Every change is backed up first and reverted on failure. Prints no secrets.
set -euo pipefail
B=/opt/servgo-releases/20261018-intake
mkdir -p "$B"
CONF=/opt/ai_growth_os/deploy/deploy.json
OLD_BASE=$(python3 -c "import json;print(json.load(open('$CONF'))['base_image'])")
echo "current base image: $OLD_BASE"

has_multipart() {
  docker run --rm --network none --entrypoint python "$1" -c \
    "import importlib.metadata as m;print(m.version('python-multipart'))" 2>/dev/null
}

if V=$(has_multipart "$OLD_BASE"); then
  echo "python-multipart already in base image ($V)"
else
  NEW_BASE="servgo-growth-release:20261018-intake"
  printf 'FROM %s\nRUN pip install --no-cache-dir "python-multipart>=0.0.18,<1"\n' "$OLD_BASE" \
    | docker build -q -t "$NEW_BASE" - >/dev/null
  V=$(has_multipart "$NEW_BASE") || { echo "PREP_FAILED: new base image lacks python-multipart"; exit 1; }
  echo "built $NEW_BASE with python-multipart $V"
  cp -a "$CONF" "$B/deploy.json.before"
  python3 - "$CONF" "$NEW_BASE" <<'PY'
import json, sys
path, image = sys.argv[1], sys.argv[2]
data = json.load(open(path))
data['base_image'] = image
open(path, 'w').write(json.dumps(data, indent=2) + '\n')
PY
  echo "deploy.json base_image -> $NEW_BASE (overlay flags unchanged: $(python3 -c "import json;print(sorted(json.load(open('$CONF'))['overlay_env']))"))"
fi

F=/etc/nginx/sites-available/growth-servgo
if grep -q 'client_max_body_size' "$F"; then
  echo "nginx: client_max_body_size already set: $(grep -o 'client_max_body_size [^;]*' "$F" | sort -u | tr '\n' ' ')"
else
  cp -a "$F" "$B/growth-servgo.before"
  python3 - "$F" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path).read()
new = re.sub(r'(server_name\s+growth\.servgo\.ru;)', r'\1\n    client_max_body_size 26m;', text)
open(path, 'w').write(new)
PY
  if nginx -t 2>/dev/null; then
    systemctl reload nginx
    echo "nginx: client_max_body_size 26m added ($(grep -c 'client_max_body_size 26m' "$F") server blocks)"
  else
    cp -a "$B/growth-servgo.before" "$F"; nginx -t && systemctl reload nginx
    echo "PREP_FAILED: nginx rejected the change, reverted"; exit 1
  fi
fi
echo "growth health: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8091/health)"
echo PREP_OK
