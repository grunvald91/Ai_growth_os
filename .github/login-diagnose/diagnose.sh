#!/usr/bin/env bash
# Read-only: how would DADATA_API_KEY reach the app on this server? Names and booleans only.
set -uo pipefail
echo "== running container sees DADATA_API_KEY:"
for c in ai-growth-os-api-1 ai-growth-os-worker-1; do
  docker exec $c sh -c 'if [ -n "$DADATA_API_KEY" ]; then echo set; else echo empty; fi' | sed "s/^/$c /"
done
echo "== compose files of the running set mention DADATA_API_KEY:"
docker inspect ai-growth-os-api-1 --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' | tr ',' '\n' | while read -r f; do
  echo "$(grep -c DADATA_API_KEY "$f" 2>/dev/null || echo 0) $f"
done
echo "== /opt/ai_growth_os/.env has DADATA_API_KEY line:"
grep -c '^DADATA_API_KEY=' /opt/ai_growth_os/.env
echo "== deploy.json overlay_env key names:"
python3 -c "import json;print(sorted(json.load(open('/opt/ai_growth_os/deploy.json')).get('overlay_env',{}).keys()))" 2>&1 | head -3
ls /opt/ai_growth_os | head -20
echo DIAG_OK
