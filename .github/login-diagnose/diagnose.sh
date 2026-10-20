#!/usr/bin/env bash
# Read-only: which text models ClaudeHub offers and whether they answer a tiny request (status only).
set -uo pipefail
W=ai-growth-os-worker-1
docker exec -i $W python - <<'PY'
import re, time, httpx
from app.core.config import get_settings
s = get_settings()
base = s.xai_base_url.rstrip("/")
auth = {"Authorization": "Bearer " + s.xai_api_key}
ids = [m.get("id") for m in httpx.get(base + "/models", headers=auth, timeout=30).json().get("data", [])]
print("catalog text-like ids:", [i for i in ids if re.search(r"kimi|k3|grok|sonnet|opus|haiku|claude|deepseek|gemini-.*(flash|pro)", i or "", re.I) and "image" not in (i or "")])
schema = {"type":"json_schema","json_schema":{"name":"t","strict":True,"schema":{"type":"object","properties":{"ok":{"type":"string"}},"required":["ok"],"additionalProperties":False}}}
for model in ["kimi-2.7", "kimi-k3", "kimi-3", "grok-4.7", "claude-sonnet-5-5", "claude-sonnet-5.5", "deepseek-v4.1-flash"]:
    for label, extra in (("plain", {}), ("json_schema", {"response_format": schema})):
        t = time.time()
        try:
            r = httpx.post(base + "/chat/completions", headers=auth, timeout=60,
                           json={"model": model, "messages": [{"role":"user","content":"Reply with the word OK as JSON field ok."}], "max_completion_tokens": 400, **extra})
            print(model, label, "-> status", r.status_code, "in catalog:", model in ids, "seconds", round(time.time()-t, 1))
        except Exception as e:
            print(model, label, "-> error", type(e).__name__, "seconds", round(time.time()-t, 1))
PY
echo DIAG_OK
