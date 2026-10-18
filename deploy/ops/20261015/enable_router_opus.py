"""Enable claude-opus-5-5 (ClaudeHub) for premium roles and turn the router policy on.
Run on the server:  docker exec -i ai-growth-os-api-1 python - < enable_router_opus.py
Rollback: docker exec ai-growth-os-api-1 sh -c 'rm "$PRODUCTS_ROOT/.model-router-config.json"'
          (only if this script created it; it prints whether a file existed before).
No API calls, no secrets printed.
"""
from app.api.team_dashboard import preview_configuration
from app.core.config import get_settings
from app.llm.router_store import (RouterConfig, RouterUpdate, _root, read_state,
                                  save_state, validate_configuration)

s = get_settings(); st = read_state(s)
path = _root(s) / ".model-router-config.json"
print("config file:", path, "existed before:", path.exists(), "revision:", st.revision)
profiles = [dict(p) for p in st.config.profiles]
for p in profiles:
    if p["model"] == "claude-opus-5-5":
        p.pop("enabled", None)
cfg = RouterConfig(enabled=True, profiles=profiles, overrides=dict(st.config.overrides),
                   max_call_usd=st.config.max_call_usd)
validate_configuration(cfg)
issues = preview_configuration(cfg)["issues"]
if issues:
    raise SystemExit("NOT SAVED, unrouteable roles: %s" % issues)
print("ROUTER_SAVED revision", save_state(s, RouterUpdate(expected_revision=st.revision, config=cfg)).revision)
