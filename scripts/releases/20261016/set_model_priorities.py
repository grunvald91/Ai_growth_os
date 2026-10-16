"""Order models by owner priority (1 = first inside a tier) instead of pinned routes.
Keeps the explicit content critic order (kimi-k3 -> claude-sonnet-5-5 -> gpt-6-luna) and
drops the temporary per-role main/critic pins added by set_content_critic.py.
Run: docker exec -i -e PYTHONPATH=/app ai-growth-os-api-1 python - < set_model_priorities.py
Rollback: the previous config is copied to .model-router-config.before-priorities.json.
No API calls, no secrets printed.
"""
import shutil
from app.api.team_dashboard import _decision, preview_configuration
from app.core.config import get_settings
from app.llm.router_store import RouterConfig, RouterUpdate, _root, read_state, save_state, validate_configuration

PRIORITIES = {"deepseek-v4.1-flash": 1, "kimi-2.7": 2,
              "grok-4.7": 1, "kimi-k3": 2, "claude-sonnet-5-5": 3, "gpt-6-luna": 4,
              "claude-opus-5-5": 1}
CONTENT_CRITIC = ["kimi-k3", "claude-sonnet-5-5", "gpt-6-luna"]
s = get_settings(); st = read_state(s)
path = _root(s) / ".model-router-config.json"
if path.exists():
    shutil.copy2(path, path.with_name(".model-router-config.before-priorities.json"))
profiles = [dict(p) for p in st.config.profiles]
for p in profiles:
    if p["model"] in PRIORITIES:
        p["priority"] = PRIORITIES[p["model"]]
overrides = {k: v for k, v in st.config.overrides.items() if k.endswith(":critic") and v == CONTENT_CRITIC}
# Quick posts, post rewrites and creative strategy are reader-facing content too.
for role in ("post_strategist", "post_writer_editor", "post_rewriter", "creative_strategist"):
    overrides[role + ":critic"] = CONTENT_CRITIC
cfg = RouterConfig(enabled=True, profiles=profiles, overrides=overrides, max_call_usd=st.config.max_call_usd)
policy = validate_configuration(cfg)
issues = preview_configuration(cfg)["issues"]
if issues:
    raise SystemExit("NOT SAVED: %s" % issues)
saved = save_state(s, RouterUpdate(expected_revision=st.revision, config=cfg))
print("ROUTER_SAVED revision", saved.revision, "content critic overrides", len(overrides))
roles = [r["id"] for r in preview_configuration(cfg)["roles"]] + [
    "post_strategist", "post_writer_editor", "post_rewriter", "creative_strategist", "editorial_planner", "general"]
for role in roles:
    print(role, "| main:", " -> ".join(_decision(policy, role, "main").get("models") or []),
          "| critic:", " -> ".join(_decision(policy, role, "critic").get("models") or []))
