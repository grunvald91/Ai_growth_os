"""Deterministic launch-readiness (preflight) checks for experiments.

A draft experiment may be incomplete or have an unmeasured baseline. It must not become
launch-ready/running until every measurement field is present and the baseline is a real
observation. This module only reads values; it never mutates state or calls external systems.
"""
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

LAUNCH_STATUSES = frozenset({"active", "running"})

EXPERIMENT_KEY_PATTERN = re.compile(r"^EXP-[A-Z0-9-]+$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")

REQUIRED_TEXT_FIELDS = (
    "experiment_key",
    "audience",
    "action",
    "target_metric",
    "attribution_method",
    "baseline_summary",
    "success_condition",
    "stop_condition",
    "inconclusive_condition",
    "currency",
)
REQUIRED_POSITIVE_INT_FIELDS = ("observation_window_days", "time_budget_minutes")
PREFLIGHT_FIELDS = REQUIRED_TEXT_FIELDS + REQUIRED_POSITIVE_INT_FIELDS + ("budget",)

# Any of these markers means the baseline has not been observed yet. Fail closed.
_PLACEHOLDER_BASELINE = re.compile(
    r"\b(unknown|unmeasured|not\s+(?:yet\s+)?measured|tbd|tbc|todo|placeholder|n/a)\b",
    re.IGNORECASE,
)
_PUNCTUATION_ONLY = re.compile(r"^[\W_]*$")


class ExperimentPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: UUID | None
    experiment_key: str | None
    status: str | None
    ready: bool
    missing_fields: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)


def is_placeholder_baseline(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or bool(_PUNCTUATION_ONLY.match(text)) or bool(_PLACEHOLDER_BASELINE.search(text))


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def evaluate_fields(fields: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return (missing_fields, blocking_reasons) for a mapping of experiment fields."""
    missing: list[str] = []
    reasons: list[str] = []

    for name in REQUIRED_TEXT_FIELDS:
        if _blank(fields.get(name)):
            missing.append(name)
            reasons.append(f"{name} is required")

    for name in REQUIRED_POSITIVE_INT_FIELDS:
        value = fields.get(name)
        if value is None:
            missing.append(name)
            reasons.append(f"{name} is required")
        elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
            reasons.append(f"{name} must be a positive integer")

    budget = fields.get("budget")
    if budget is None:
        missing.append("budget")
        reasons.append("budget is required")
    else:
        try:
            if Decimal(str(budget)) < 0:
                reasons.append("budget cannot be negative")
        except InvalidOperation:
            reasons.append("budget must be a number")

    key = fields.get("experiment_key")
    if not _blank(key) and not EXPERIMENT_KEY_PATTERN.match(str(key).strip()):
        reasons.append("experiment_key must be a stable key matching EXP-[A-Z0-9-]+")

    currency = fields.get("currency")
    if not _blank(currency) and not CURRENCY_PATTERN.match(str(currency).strip()):
        reasons.append("currency must be a 3-letter uppercase ISO code")

    baseline = fields.get("baseline_summary")
    if not _blank(baseline) and is_placeholder_baseline(baseline):
        reasons.append(
            "baseline_summary is unmeasured or a placeholder; record a real baseline observation before launch"
        )

    return missing, reasons


def evaluate_experiment(experiment: Any) -> ExperimentPreflight:
    """Evaluate launch readiness of an Experiment-like object without mutating it."""
    fields = {name: getattr(experiment, name, None) for name in PREFLIGHT_FIELDS}
    missing, reasons = evaluate_fields(fields)
    return ExperimentPreflight(
        experiment_id=getattr(experiment, "id", None),
        experiment_key=fields["experiment_key"],
        status=getattr(experiment, "status", None),
        ready=not reasons,
        missing_fields=missing,
        blocking_reasons=reasons,
    )


class ExperimentNotReadyError(ValueError):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("Experiment is not launch-ready: " + "; ".join(reasons))


def require_launch_ready(fields: Mapping[str, Any]) -> None:
    _, reasons = evaluate_fields(fields)
    if reasons:
        raise ExperimentNotReadyError(reasons)
