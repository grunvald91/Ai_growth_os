#!/usr/bin/env bash
# Read-only: current model-router state on the server (model IDs, flags, role names only).
set -uo pipefail
docker exec -i ai-growth-os-worker-1 python - <<'PY'
import json
from app.core.config import get_settings
from app.llm.router_store import read_state, effective_router_settings
s = get_settings()
e = effective_router_settings(s)
st = read_state(s)
print("revision:", st.revision, "enabled_in_effect:", e.model_router_enabled, "llm_provider:", s.llm_provider, "llm_model:", s.llm_model)
print("profiles:", [(p["model"], p["tier"], p.get("enabled", True)) for p in st.config.profiles])
print("overrides:", st.config.overrides)
print("max_call_usd:", st.config.max_call_usd, "critic_enabled:", s.agent_critic_enabled)
PY
echo DIAG_OK
