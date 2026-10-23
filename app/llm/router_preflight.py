"""Safe, entirely offline AI Model Router readiness check.

Run: python -m app.llm.router_preflight
Zero HTTP requests, zero model completions, zero secrets printed.
"""
from __future__ import annotations

import json

from app.agents import AgentRole
from app.core.config import Settings, get_settings
from app.llm.model_router import _BALANCED_ROLES, _ECONOMY_ROLES, _PREMIUM_ROLES
from app.llm.router_store import RouterStoreError, read_state


_EXTRA_STAGES = frozenset({
    "repurposing_editor", "platform_writer",
    "youtube_shorts_director", "instagram_reels_director",
    "competitive_intelligence", "editorial_planner", "post_strategist", "post_writer_editor",
    "post_rewriter", "creative_strategist", "whiteboard_storyboard", "dispatcher", "mail_agent",
})
_HANDLED_ROLES = frozenset(_BALANCED_ROLES | _ECONOMY_ROLES | _PREMIUM_ROLES)


def offline_router_report(settings: Settings) -> dict:
    """Return a bounded, credential-free configuration and coverage summary."""
    from app.api.team_dashboard import preview_configuration, role_descriptors

    known_roles = {role.value for role in AgentRole} | _EXTRA_STAGES
    ui_roles = [role["id"] for role in role_descriptors()]
    discrepancies = sorted((known_roles - set(ui_roles)) | (set(ui_roles) - known_roles))
    unclassified = sorted(known_roles - _HANDLED_ROLES)
    try:
        state = read_state(settings)
        preview = preview_configuration(state.config)
        issues = list(preview["issues"])
        if state.config.enabled and preview["issues"]:
            issues.insert(0, "Enabled policy has unrouteable work: disable or complete its models.")
        issues.extend("Dashboard role mismatch: " + name for name in discrepancies)
        issues.extend("Unclassified role: " + name for name in unclassified)
        return {
            "status": "ready" if not issues else "needs_attention",
            "enabled": state.config.enabled,
            "revision": state.revision,
            "role_count": len(known_roles),
            "route_count": len(preview["roles"]),
            "main_ready": sum(x["main"]["status"] == "ready" for x in preview["roles"]),
            "critics_ready": sum(x["critic"]["status"] == "ready" for x in preview["roles"]),
            "issues": issues[:100],
            "search_adapter_status": "not_implemented",
            "critic_workflow_status": "not_implemented",
            "pricing_status": "operator_estimates_only",
            "paid_calls": 0,
            "network_calls": 0,
        }
    except (ValueError, RouterStoreError) as exc:
        return {
            "status": "invalid_configuration",
            "role_count": len(known_roles),
            "issues": [type(exc).__name__],
            "paid_calls": 0,
            "network_calls": 0,
        }


def main() -> int:
    report = offline_router_report(get_settings())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # When disabled, missing premium roles are expected and safe.
    return 1 if report["status"] == "invalid_configuration" or (
        report.get("enabled") and report["status"] != "ready"
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
