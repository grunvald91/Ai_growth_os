"""Reusable, progressively enhanced read-only task/material card disclosure.

The primary link and its URL are owned by existing server-side Studio views.
No publication, approval or mutation is authorized from this widget.
"""
from __future__ import annotations

from html import escape


def resource_widget(*, kind: str, primary_html: str, description: str,
                    detail_label: str = "Подробнее", note: str = "",
                    filter_kind: str | None = None, search_text: str = "") -> str:
    """Wrap existing server-rendered card markup; text from records is escaped.

    The <details> is a fully functional fallback until/without UI SDK.
    Primary markup must be created by the trusted Studio renderer, not an LLM.
    """
    if kind not in {"task", "material", "workflow"}:
        raise ValueError("Unsupported resource kind")
    details = f'<p class="sg-resource-description">{escape(description)}</p>'
    if note:
        details += f'<p class="sg-resource-note">{escape(note)}</p>'
    filter_data = (
        f' data-material data-kind="{escape(filter_kind, quote=True)}"'
        f' data-search="{escape(search_text, quote=True)}"'
        if filter_kind is not None else ""
    )
    return (
        f'<article class="sg-resource-widget" data-sg-resource-widget="{kind}"{filter_data}>'
        + primary_html
        + '<details class="sg-resource-details" data-sg-resource-details>'
        + f'<summary>{escape(detail_label)}</summary>'
        + f'<div class="sg-resource-details-body">{details}</div>'
        + '</details></article>'
    )
