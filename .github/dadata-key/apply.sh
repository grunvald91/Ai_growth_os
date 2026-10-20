#!/usr/bin/env bash
# Runs on the server as root; DK (the key) is defined above this script. Never prints the key.
set -uo pipefail
python3 - "$DK" <<'PY'
import json, sys, os
sys.path.insert(0, "/opt/ai_growth_os/deploy")
import deploy_growth as d
key = sys.argv[1]
busy = d.busy_jobs()
if busy:
    print(f"ABORT: {busy} task(s) running, try again later"); sys.exit(2)
# 1) Remember the key for every later deploy (root-only file).
cfg = json.loads(d.CONFIG.read_text())
cfg["overlay_env"]["DADATA_API_KEY"] = key
tmp = str(d.CONFIG) + ".new"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(tmp, d.CONFIG); os.chmod(d.CONFIG, 0o600)
# 2) Give the key to the release that is running now, then recreate the services.
prev = d.running_set()
overlays = [f for f in prev["files"] if f.startswith("/opt/servgo-releases/") and f.endswith("overlay.json")]
if not overlays:
    print("ABORT: running set has no overlay file"); sys.exit(3)
path = overlays[-1]
original = open(path).read()
data = json.loads(original)
for service in data["services"].values():
    service.setdefault("environment", {})["DADATA_API_KEY"] = key
open(path, "w").write(json.dumps(data, indent=2)); os.chmod(path, 0o600)
try:
    d.compose_up(prev["files"], prev["cwd"])
    d.wait_healthy()
    print("APPLIED: services recreated and healthy")
except BaseException as exc:
    open(path, "w").write(original)
    d.compose_up(prev["files"], prev["cwd"])
    print("FAILED, restored previous settings:", type(exc).__name__); sys.exit(1)
PY
rc=$?
[ $rc -eq 0 ] || { echo "APPLY_RC=$rc"; exit $rc; }
echo "== registry check from the app (public test INN, booleans only)"
docker exec -i ai-growth-os-worker-1 python - <<'PY'
import asyncio
from app.core.config import get_settings
from app.intake.registry import registry_from_settings
lookup = registry_from_settings(get_settings())
print("key visible to app:", lookup is not None)
if lookup:
    src = asyncio.run(lookup.lookup("7707083893"))
    print("lookup status:", src.status, "text received:", bool(getattr(src, "text", "") or getattr(src, "content", "")))
PY
echo DADATA_DONE
