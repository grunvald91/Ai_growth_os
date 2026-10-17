"""Home page quick line: one topic field for a Telegram post and topic suggestions under it.

Suggestions come only from data the company already has (the content plan and the latest
niche research), so opening the home page never calls a model. A suggestion only fills the
field; the owner still presses the button to start a job.
"""
from __future__ import annotations

import html
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact

MAX_SUGGESTIONS = 6
_MIN_LEN, _MAX_LEN = 8, 160
_DONE_STATES = {"ready_for_review", "completed"}


def _clean(text) -> str:
    return " ".join(str(text or "").split()).strip(" .;")


def topic_suggestions(db: Session, product_id, plan: dict | None) -> list[str]:
    """Plan topics not produced yet, then research search seeds and content angles."""
    candidates: list[str] = []
    for slot in (plan or {}).get("slots") or []:
        if slot.get("state") not in _DONE_STATES:
            candidates.append(slot.get("topic"))
    research = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == "viral_content_research",
    ).order_by(Artifact.created_at.desc()).limit(1))
    if research is not None:
        try:
            data = json.loads(research.content)
        except (TypeError, ValueError):
            data = {}
        if isinstance(data, dict) and data.get("source_status") == "evidence_available":
            candidates += list(data.get("seo_topic_seeds") or [])
            candidates += list(data.get("content_angles") or [])
    result, seen = [], set()
    for raw in candidates:
        topic = _clean(raw)
        key = topic.casefold()
        if not (_MIN_LEN <= len(topic) <= _MAX_LEN) or key in seen:
            continue
        seen.add(key)
        result.append(topic[0].upper() + topic[1:])
        if len(result) == MAX_SUGGESTIONS:
            break
    return result


def quick_topic_html(product_id, suggestions: list[str]) -> str:
    chips = "".join(
        f'<button type="button" class="sg-topic-chip" data-topic-suggestion="{html.escape(t, quote=True)}">'
        f'{html.escape(t)}</button>' for t in suggestions
    )
    hint = (f'<div class="sg-topic-suggestions" aria-label="Идеи для темы"><span>Идеи:</span>{chips}</div>'
            if chips else '<p class="sg-topic-empty">Идеи появятся после контент-плана или исследования ниши.</p>')
    return f"""
      <form class="sg-quick-topic" method="post" action="/studio/products/{product_id}/create">
        <input type="hidden" name="format" value="post">
        <input type="hidden" name="channel" value="telegram">
        <label class="sr-only" for="sg-quick-topic-input">О чём пост в Telegram</label>
        <div class="sg-quick-topic-row">
          <input id="sg-quick-topic-input" name="topic" minlength="3" maxlength="300" required
            autocomplete="off" placeholder="О чём пост в Telegram? Например: как вернуть клиентов, которые пропали">
          <button class="btn primary" type="submit">Создать пост →</button>
        </div>
        {hint}
      </form>"""


CSS = """
.sg-quick-topic{grid-column:1/-1;display:flex;flex-direction:column;gap:10px;margin-top:4px}
.sg-quick-topic-row{display:flex;gap:10px}
.sg-quick-topic-row input{flex:1;min-width:0;min-height:44px;padding:10px 14px;border:1px solid #CBD6EE;border-radius:12px;font:inherit;background:#fff}
.sg-quick-topic-row input:focus{outline:2px solid #2F6BFF;outline-offset:1px}
.sg-topic-suggestions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.sg-topic-suggestions>span{color:#526176;font-size:13px}
.sg-topic-chip{min-height:36px;padding:6px 12px;border:1px solid #DFE7FA;border-radius:999px;background:#fff;color:#0D1B2A;font:inherit;font-size:13px;line-height:1.3;text-align:left;cursor:pointer}
.sg-topic-chip:hover,.sg-topic-chip:focus-visible{border-color:#2F6BFF;color:#2F6BFF}
.sg-topic-empty{color:#526176;font-size:13px;margin:0}
@media (max-width:640px){.sg-quick-topic-row{flex-direction:column}.sg-quick-topic-row .btn{width:100%}}
"""

JS = r"""
(function(){
  document.querySelectorAll('[data-topic-suggestion]').forEach(function(chip){
    chip.addEventListener('click', function(){
      var form = chip.closest('form'), input = form && form.querySelector('input[name="topic"]');
      if (!input) return;
      input.value = chip.dataset.topicSuggestion;
      input.focus();
    });
  });
})();
"""
