#!/usr/bin/env bash
# Read-only: why does the whiteboard storyboard stage get HTTP 5xx? Codes, counts, booleans only.
set -uo pipefail
W=ai-growth-os-worker-1
echo "== proxy settings seen by the worker (booleans / host names only)"
docker exec $W python - <<'PY'
import os
from urllib.parse import urlsplit
from app.core.config import get_settings
s = get_settings()
for name in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","http_proxy","https_proxy","all_proxy"):
    print(name, "set" if os.environ.get(name) else "empty")
print("WHITEBOARD_PROXY_URL", "set" if s.whiteboard_proxy_url else "empty")
print("XAI_BASE_URL host:", urlsplit(s.xai_base_url).hostname)
print("storyboard_model:", s.whiteboard_storyboard_model or "(default)")
PY
echo "== ClaudeHub reachability from the worker (GET /models, status only)"
docker exec $W python - <<'PY'
import time, httpx
from app.core.config import get_settings
s = get_settings()
for attempt in range(3):
    t = time.time()
    try:
        r = httpx.get(s.xai_base_url.rstrip("/") + "/models", headers={"Authorization": "Bearer " + s.xai_api_key}, timeout=30)
        ids = [m.get("id") for m in r.json().get("data", [])] if r.status_code == 200 else []
        print("attempt", attempt + 1, "status", r.status_code, "seconds", round(time.time() - t, 1),
              "kimi-2.7 listed:", "kimi-2.7" in ids, "models:", len(ids))
    except Exception as exc:
        print("attempt", attempt + 1, "error", type(exc).__name__, "seconds", round(time.time() - t, 1))
PY
echo "== latest whiteboard jobs: state and error history (codes only)"
docker exec $W python - <<'PY'
import json
from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import Artifact
with SessionLocal() as db:
    rows = db.scalars(select(Artifact).where(Artifact.kind == "whiteboard_job_state").order_by(Artifact.updated_at.desc()).limit(3)).all()
    for a in rows:
        v = json.loads(a.content)
        print("job updated", a.updated_at, "stage", v.get("stage"), "error", v.get("error"), "last_error", v.get("last_error"),
              "calls", v.get("calls"), "auto_resumes", v.get("auto_resumes"), "retry_at", v.get("retry_at"))
        for e in (v.get("errors") or [])[-8:]:
            print("   ", {k: e.get(k) for k in ("at","stage","code","calls","http_status","exception_type","reason","auto_resume")})
PY
echo "== storyboard responses and failures in logs, last 24h"
for c in $W ai-growth-os-api-1; do
  docker logs --since 24h "$c" 2>&1 | grep -o 'whiteboard_storyboard_response {[^}]*}' | sed "s/^/$c /" | tail -10
done
docker logs --since 24h $W 2>&1 | grep -oE 'whiteboard_failed \{[^}]*\}' | sed -E 's/"task_id": "[^"]*", //' | tail -10
echo DIAG_OK
