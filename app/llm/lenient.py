"""Forgiving preparation of model output before strict validation.

Models sometimes add keys the schema does not know or return a few more list items than
the contract allows. Neither is a reason to fail a whole job: unknown keys are dropped and
over-long lists are trimmed. Missing or wrong-typed required data still fails validation.
"""
from __future__ import annotations

import types
from typing import Any, Union, get_args, get_origin

from annotated_types import MaxLen
from pydantic import BaseModel


def _models_in(annotation) -> list[type[BaseModel]]:
    """Pydantic models referenced by a field annotation (Optional, list, Annotated unwrapped)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    found = []
    for arg in get_args(annotation):
        found.extend(_models_in(arg))
    return found


def _is_list(annotation) -> bool:
    origin = get_origin(annotation)
    if origin in (list, tuple):
        return True
    if origin in (Union, types.UnionType):
        return any(_is_list(arg) for arg in get_args(annotation))
    return False


def relax(schema: type[BaseModel], data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    fields = schema.model_fields
    known = set(fields) | {f.alias for f in fields.values() if f.alias}
    result = {}
    for key, value in data.items():
        if key not in known:
            continue
        field = fields.get(key) or next(f for f in fields.values() if f.alias == key)
        models = _models_in(field.annotation)
        if isinstance(value, list):
            limit = next((m.max_length for m in field.metadata if isinstance(m, MaxLen)), None)
            if limit is not None and _is_list(field.annotation) and len(value) > limit:
                value = value[:limit]
            if models:
                value = [relax(models[0], item) for item in value]
        elif isinstance(value, dict) and models:
            value = relax(models[0], value)
        result[key] = value
    return result
