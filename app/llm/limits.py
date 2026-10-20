"""How many tokens a model may write in one reply.

Callers pass max_tokens as the size they expect; the model router uses it to estimate
cost. The ceiling actually sent to the provider is never below OUTPUT_TOKEN_FLOOR:
reasoning models spend part of the budget thinking before they answer, and a tight
ceiling cut normal replies off mid-JSON (e.g. a whiteboard storyboard stopped at
2198 of 2200 tokens). Providers bill for the tokens written, not for the ceiling, so a
generous ceiling costs nothing for normal replies. Hitting this ceiling now means a
genuinely runaway reply, which callers treat as a failure.

16 000 stays below the Anthropic SDK's non-streaming limit (~21 000) and within the
output limits of the models we route to.
"""
from __future__ import annotations

OUTPUT_TOKEN_FLOOR = 16_000


def output_ceiling(requested: int) -> int:
    return max(int(requested), OUTPUT_TOKEN_FLOOR)


def rejects_ceiling(status_code: int, body: str) -> bool:
    """A provider refused the ceiling itself (model with a smaller output limit)."""
    text = (body or "").casefold()
    return status_code in {400, 422} and "token" in text and any(
        word in text for word in ("max_tokens", "max_completion_tokens", "maximum", "too large", "exceed"))
