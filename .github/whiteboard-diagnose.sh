#!/usr/bin/env bash
# Read-only. One GET of the ClaudeHub model catalogue; prints only model ids and flags, never keys.
set -u
API=$(docker ps --filter label=com.docker.compose.project=ai-growth-os --filter label=com.docker.compose.service=api --format '{{.Names}}' | head -1)
docker exec -i "$API" python - <<'PY'
import json, httpx
from app.core.config import get_settings
s = get_settings()
r = httpx.get(s.xai_base_url.rstrip('/') + '/models', headers={'Authorization': 'Bearer ' + s.xai_api_key}, timeout=20, trust_env=False)
print('status', r.status_code)
data = r.json().get('data', [])
print('count', len(data))
for m in data:
    extra = {k: v for k, v in m.items() if k not in ('id', 'object', 'created', 'owned_by')}
    print(m.get('id'), json.dumps(extra, ensure_ascii=False)[:160] if extra else '')
print('role overrides storyboard model:', s.whiteboard_storyboard_model)
PY
