"""Append-only manual edits of copy fields; structure and publication metadata stay fixed."""
import json
from uuid import UUID
from sqlalchemy import select
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.content.revisions import source_fingerprint
from app.services.artifacts import ArtifactService
from app.content.quality import post_quality_warnings, factual_claim_warnings

EDITABLE_KINDS = {"channel_text", "article", "creative_brief", "video_script"}


def editable_fields(source):
    try:
        value = json.loads(source.content)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, dict):
        return []
    result = []
    def add(path, label):
        current = value
        try:
            for part in path:
                current = current[part]
        except (KeyError, IndexError, TypeError):
            return
        if isinstance(current, str):
            result.append((path, label, current))
    if source.kind == "channel_text":
        add(("body",), "Текст поста")
    elif source.kind == "article":
        for key in ("full_article", "reader_article"):
            if isinstance(value.get(key), str):
                add((key,), "Текст статьи")
                break
    elif source.kind == "creative_brief":
        for key, label in (("caption", "Подпись"), ("voiceover_script", "Озвучка")):
            if key in value:
                add((key,), label)
        for index, frame in enumerate(value.get("frames", [])):
            for key, label in (("on_screen_text", "Текст на экране"), ("spoken_text", "Речь"), ("visual_prompt", "Описание кадра")):
                if key in frame:
                    add(("frames", index, key), f"Кадр {index + 1} · {label}")
    elif source.kind == "video_script":
        root = ("production_brief",) if "production_brief" in value else ()
        brief = value.get("production_brief", value)
        if "voiceover_or_dialogue_script" in brief:
            add(root + ("voiceover_or_dialogue_script",), "Озвучка / диалог")
        for index, shot in enumerate(brief.get("shots", [])):
            if isinstance(shot.get("on_screen_text"), str):
                add(root + ("shots", index, "on_screen_text"), f"Сцена {index + 1} · текст на экране")
    return result


def save_manual_edit(db, product_id: UUID, source_id: UUID, fields):
    db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    source = db.scalar(select(Artifact).where(Artifact.id == source_id,
        Artifact.product_id == product_id).with_for_update().execution_options(populate_existing=True))
    if source is None or source.kind not in EDITABLE_KINDS:
        raise LookupError("Материал не найден")
    if source.status not in {ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED}:
        raise ValueError("Редактируйте текущую версию материала")
    if str(source.version) != fields.get("source_version") or source_fingerprint(source) != fields.get("source_digest"):
        raise ValueError("Материал изменился. Откройте текущую версию перед правкой")
    pending = db.scalar(select(Task.id).where(Task.product_id == product_id,
        Task.assigned_agent == "content_editor", Task.title == "EDIT: " + str(source.id),
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING])))
    if pending:
        raise ValueError("Дождитесь результата доработки ИИ")
    publication = db.scalar(select(Publication.id).where(Publication.artifact_id == source_id,
        Publication.status.in_(["scheduled", "sending", "delivery_unknown"])))
    if publication:
        raise ValueError("Сначала отмените назначенную отправку или проверьте её результат")
    title = fields.get("title", "").strip()
    if not 3 <= len(title) <= 240:
        raise ValueError("Заголовок: от 3 до 240 символов")
    value = json.loads(source.content)
    edits = editable_fields(source)
    if not edits:
        raise ValueError("В этом материале нет текстовых полей для редактирования")
    changed = title != source.title
    for index, (path, label, old) in enumerate(edits):
        key = f"edit_{index}"
        if key not in fields:
            raise ValueError("Форма неполная. Откройте материал заново")
        text = fields[key]
        if len(text) > 60000 or (not text and old):
            raise ValueError(f"Поле «{label}» пустое или слишком длинное")
        changed = changed or text != old
        current = value
        for part in path[:-1]:
            current = current[part]
        current[path[-1]] = text
    if not changed:
        raise ValueError("Внесите правку перед сохранением новой версии")
    if source.kind == "channel_text":
        if value.get("channel") == "telegram":
            from app.content.telegram_publishing import validate_telegram_text
            validate_telegram_text(value["body"])
        value["quality_warnings"] = post_quality_warnings(title, value["body"], value.get("channel", ""))
        fact_notes = factual_claim_warnings(title + " " + value["body"])
        value["quality_warnings"] += fact_notes
        value["fact_check_required"] = bool(fact_notes)
        if value.get("cta") and value["cta"].casefold() not in value["body"].casefold():
            value["quality_warnings"].append("После ручной правки проверьте призыв к действию")
    value.update(title=title, origin="owner_manual_revision", revision_of=str(source.id),
                 revision_root_id=value.get("revision_root_id") or str(source.id),
                 revision_number=int(value.get("revision_number", 1)) + 1)
    if source.kind == "creative_brief":
        from app.content.creative import CreativeDraft, _preview
        draft = CreativeDraft.model_validate({key: value[key] for key in CreativeDraft.model_fields if key in value})
        value["preview_text"] = _preview(draft)
        notes = factual_claim_warnings(value["preview_text"])
        value["quality_warnings"] = notes
        value["fact_check_required"] = bool(notes)
    elif source.kind == "article":
        notes = factual_claim_warnings(" ".join(text for _, _, text in editable_fields_value(source, value)))
        value["quality_warnings"] = notes
        value["fact_check_required"] = bool(notes)
    revised = ArtifactService(db).create(product_id=product_id, task_id=source.task_id,
        kind=source.kind, title=title, content=json.dumps(value, ensure_ascii=False),
        status="awaiting_approval", version=source.version + 1)
    source.status = ArtifactStatus.REJECTED
    db.commit()
    return revised


def editable_fields_value(source, value):
    from types import SimpleNamespace
    return editable_fields(SimpleNamespace(kind=source.kind, content=json.dumps(value)))
