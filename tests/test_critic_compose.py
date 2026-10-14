"""Production environment must expose the opt-in critic flag to all LLM workers."""
from pathlib import Path


def test_production_compose_passes_critic_flag_to_every_llm_service():
    text = Path("docker-compose.prod.yml").read_text(encoding="utf-8")
    for name in ("api", "telegram", "worker"):
        start = text.index(f"  {name}:\n")
        end = text.find("\n  ", start + 2)
        part = text[start:end if end != -1 else len(text)]
        assert part.count("AGENT_CRITIC_ENABLED:") == 1, name
        assert "AGENT_CRITIC_ENABLED: ${AGENT_CRITIC_ENABLED:-false}" in part
