#!/usr/bin/env bash
# Read-only diagnostics: ClaudeHub chat probes (tiny) and stored 502 response shapes. Codes/counts only.
set -uo pipefail
W=ai-growth-os-worker-1
echo "== saved storyboard error responses: shape only"
docker exec -i $W python - <<'PY'
import json, glob, re, os
from app.core.config import get_settings
root = str(get_settings().products_root)
files = sorted(glob.glob(root + "/**/storyboard-response-*.json", recursive=True), key=os.path.getmtime)[-12:]
print("files:", len(files))
for path in files:
    d = json.load(open(path))
    body = d.get("body") or ""
    kinds = [k for k in ("html","json") if (k == "html" and body.lstrip().startswith("<")) or (k == "json" and body.lstrip().startswith("{"))]
    words = [w for w in ("timeout","timed out","upstream","overload","rate","capacity","token","max_","unavailable","bad gateway","cloudflare","nginx","gateway","limit","context") if w in body.lower()]
    print(os.path.basename(path)[:22], "mtime", int(os.path.getmtime(path)), "status", d.get("http_status"), "len", len(body), kinds, words)
PY
echo "== tiny ClaudeHub chat probes with the storyboard model (status/timing only)"
docker exec -i $W python - <<'PY'
import time, httpx
from app.core.config import get_settings
s = get_settings()
url = s.xai_base_url.rstrip("/") + "/chat/completions"
model = s.whiteboard_storyboard_model or "kimi-2.7"
schema = {"type":"json_schema","json_schema":{"name":"t","strict":True,"schema":{"type":"object","properties":{"ok":{"type":"string"}},"required":["ok"],"additionalProperties":False}}}
msgs = [{"role":"user","content":"Reply with the single word OK."}]
cases = [("ceiling 400", {"max_completion_tokens":400}),
         ("ceiling 16000", {"max_completion_tokens":16000}),
         ("ceiling 16000 + json_schema strict", {"max_completion_tokens":16000, "response_format":schema}),
         ("ceiling 2200 + json_schema strict", {"max_completion_tokens":2200, "response_format":schema})]
for name, extra in cases:
    t = time.time()
    try:
        r = httpx.post(url, headers={"Authorization":"Bearer "+s.xai_api_key}, json={"model":model,"messages":msgs,**extra}, timeout=90)
        info = ""
        if r.status_code == 200:
            c = r.json()["choices"][0]
            info = "finish=" + str(c.get("finish_reason")) + " out_tokens=" + str((r.json().get("usage") or {}).get("completion_tokens"))
        print(name, "-> status", r.status_code, "seconds", round(time.time()-t,1), info)
    except Exception as e:
        print(name, "-> error", type(e).__name__, "seconds", round(time.time()-t,1))
PY
echo DIAG_OK
