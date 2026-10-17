"""Kimi K3 as the content critic (fallbacks: Sonnet 5.5, then GPT-6 Luna). Main routes must not change.
Run on the server:
  docker exec -i -e PYTHONPATH=/app ai-growth-os-api-1 python - < set_content_critic.py
Rollback: the previous config is copied to .model-router-config.before-critic.json next to the live file.
No API calls, no secrets printed.
"""
import json, shutil
from app.api.team_dashboard import preview_configuration
from app.core.config import get_settings
from app.llm.router_store import RouterConfig, RouterUpdate, _root, read_state, save_state, validate_configuration

CRITIC = ["kimi-k3", "claude-sonnet-5-5", "gpt-6-luna"]
ROLES = ["content_strategist", "seo_editor", "article_writer", "conversion_editor", "human_editor",
         "marketing_editor", "repurposing_editor", "video_director", "story_director", "visual_designer",
         "platform_writer", "youtube_shorts_director", "instagram_reels_director", "scriptwriter",
         "creative_director", "content_seo"]
s = get_settings(); st = read_state(s)
path = _root(s) / ".model-router-config.json"
if path.exists():
    shutil.copy2(path, path.with_name(".model-router-config.before-critic.json"))
mains = lambda cfg: {r["id"]: r["main"].get("models") for r in preview_configuration(cfg)["roles"]}
critics = lambda cfg: {r["id"]: r["critic"].get("models") for r in preview_configuration(cfg)["roles"]}
before = mains(st.config)
before_critics = critics(st.config)
profiles = [dict(p) for p in st.config.profiles]
for p in profiles:
    if p["model"] in ("kimi-k3", "claude-sonnet-5-5"):
        p.pop("enabled", None)
if not any(p["model"] == "gpt-6-luna" for p in profiles):
    profiles.append({"model": "gpt-6-luna", "tier": "balanced"})
overrides = dict(st.config.overrides)
roles = set(before)
# Enabling more balanced models would re-sort automatic routing: pin today's routes explicitly.
for role in roles:
    overrides.setdefault(role, before[role])
    if role not in ROLES:
        overrides.setdefault(role + ":critic", before_critics[role])
for role in ROLES:
    if role in roles:
        overrides[role + ":critic"] = CRITIC
cfg = RouterConfig(enabled=True, profiles=profiles, overrides=overrides, max_call_usd=st.config.max_call_usd)
validate_configuration(cfg)
preview = preview_configuration(cfg)
if preview["issues"]:
    raise SystemExit("NOT SAVED, unrouteable roles: %s" % preview["issues"])
after = mains(cfg)
changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
after_critics = critics(cfg)
changed.update({k + ":critic": (before_critics[k], after_critics[k]) for k in before_critics
                if k not in ROLES and before_critics[k] != after_critics[k]})
if changed:
    raise SystemExit("NOT SAVED, main routes would change: %s" % json.dumps(changed))
saved = save_state(s, RouterUpdate(expected_revision=st.revision, config=cfg))
print("ROUTER_SAVED revision", saved.revision)
for r in preview["roles"]:
    if r["id"] in ROLES:
        print(r["id"], "critic:", r["critic"].get("models"))
