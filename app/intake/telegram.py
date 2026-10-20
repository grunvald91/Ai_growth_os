"""Owner-only conversational UI, invoked after the control bot's identity check."""
import json
import re
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.company_profile import load_company_profile, profile_snapshot
from app.intake.contracts import IntakeError
from app.intake.registry import registry_from_settings
from app.intake.service import CompanyIntakeService
from app.intake.sources import make_source, MAX_FILE_BYTES, clean_text, FILE_HELP, parse_file_async
from app.models.domain import Artifact, Product, Task, TaskStatus

COMPANY_BUTTONS = {"🏢 Компания", "➕ Добавить данные", "🌐 Сайт", "🏷 ИНН / название",
                   "📎 Файл / прайс", "✍️ Написать своими словами", "❓ Чего не хватает",
                   "👀 Проверить профиль"}
LABELS = {
    "identity.name": "Название", "identity.legal_name": "Юридическое название",
    "identity.one_liner": "Что предлагает компания", "identity.languages": "Языки",
    "identity.service_geography": "Где работает компания", "identity.website": "Сайт",
    "offer.products_services": "Продукты и услуги", "offer.primary_conversion_action": "Следующий шаг клиента",
    "customers.primary_segments": "Основные клиенты", "customers.pains": "Проблемы клиентов",
    "customers.jobs_to_be_done": "Задачи клиентов", "customers.exclusions": "Исключения",
    "brand.tone": "Тон общения", "brand.prohibited_claims": "Запрещённые обещания",
    "proof.approved_facts": "Подтверждённые сведения", "proof.approved_numbers": "Числа и цены",
    "proof.no_proof_yet": "Подтверждений пока нет", "outreach.sender_identity": "Отправитель",
    "outreach.allowed_channels": "Разрешённые каналы", "outreach.icp_criteria": "Критерии отбора компаний",
    "outreach.approval_required": "Согласование обращений", "outreach.do_not_contact": "Кому нельзя писать",
    "constraints.privacy": "Защита данных", "constraints.legal_compliance": "Юридические ограничения",
    "constraints.operational": "Рабочие ограничения", "constraints.approval_rules": "Правила согласования",
    "measurement.funnel_stages": "Шаги клиента", "measurement.primary_conversion_event": "Главный результат",
    "measurement.attribution_rules": "Учёт источников клиентов",
}
GATES = {"strategy": "Стратегия", "content": "Контент", "outreach": "Обращения", "measurement": "Измерение"}
CONTROL_BUTTONS = {"📊 Главное", "📊 Статус", "✅ На согласование", "📨 Решения",
                   "🧪 Тесты", "🧪 Эксперименты", "🔄 Обновить", "📝 Контент", "📇 Контакты", "📧 Почта"}


def value_text(value):
    if value is None:
        return "не задано"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, list):
        return "; ".join(value_text(item) for item in value) or "явно указано: нет ограничений / значений"
    if isinstance(value, dict):
        return "; ".join(value_text(item) for key, item in value.items()
                         if key not in {"fact_type", "record_type", "evidence_ref"}) or "нет"
    return str(value)


def render_review(review):
    lines = ["🏢 Проверка данных компании", "", "✅ Что найдено и подкреплено источниками"]
    for row in review["supported"]:
        lines.append(f"• {LABELS.get(row['field_path'], 'Дополнительные сведения')}: {value_text(row['proposed_value'])}")
        lines.extend("  Источник: " + item["source_reference"] for item in row["evidence"])
    if not review["supported"]:
        lines.append("Новых сведений для подтверждения пока нет.")
    lines += ["", "⚠️ Изменения и противоречия — требуют вашего подтверждения"]
    for change in review["changes"]:
        lines.append(f"• {LABELS.get(change['field_path'], 'Сведения')}: {value_text(change['old'])} → {value_text(change['new'])}")
    for conflict in review["conflicts"]:
        lines.append("• Противоречие: " + LABELS.get(conflict["field_path"], "Сведения"))
        for alternative in conflict["alternatives"]:
            references = ", ".join(item["source_reference"] for item in alternative["evidence"])
            lines.append(f"  {value_text(alternative['proposed_value'])} — {references}")
        lines.append("  Предлагается подтвердить: " + value_text(conflict["proposed_value"]))
    dropped = review.get("rejected_claims") or []
    if dropped:
        lines.append(f"• Отброшено сведений без точной цитаты в источнике: {len(dropped)}. "
                     "Если чего-то не хватает, добавьте это своими словами.")
    lines += ["", "❓ Неизвестное и гипотезы"]
    lines.extend("• " + item["question"] for item in review["unknowns"])
    lines.extend("• Гипотеза: " + item["statement"] for item in review["profile_preview"].get("hypotheses", []))
    lines += ["", "🚫 Ограничения и правила"]
    for section in ("constraints", "brand", "outreach"):
        for key, value in review["constraints"].get(section, {}).items():
            if section == "constraints" or key in {"prohibited_claims", "prohibited_phrases", "approval_required", "do_not_contact"}:
                lines.append("• " + LABELS.get(f"{section}.{key}", "Правило") + ": " + value_text(value))
    lines += ["", "Готовность"]
    for gate, state in review["readiness"].items():
        lines.append(f"{GATES[gate]}: " + ("готово" if state["ready"] else "не хватает данных"))
    for source in review["sources"]:
        if source["status"] != "read":
            lines.append("📎 " + (source.get("filename") or source["reference"]) + ": " +
                         (source["note"] or "Материал прочитан частично."))
    lines += ["", "Подтверждение сохранит именно этот черновик и все показанные изменения.",
              "Сообщения клиентам при этом не отправляются."]
    return "\n".join(lines)


class TelegramCompanyIntake:
    def __init__(self, bot, session_factory, settings_factory, llm_builder=None):
        self.bot, self.session_factory, self.settings_factory = bot, session_factory, settings_factory
        self.llm_builder = llm_builder
        self.selected = None
        self.mode = None

    @property
    def actor(self):
        return f"telegram:{self.bot.owner_chat_id}"

    def service(self, db):
        settings = self.settings_factory()
        return CompanyIntakeService(db, products_root=settings.products_root,
                                    model=settings.llm_model, provider_name=settings.llm_provider,
                                    registry_lookup=registry_from_settings(settings))

    def active(self, db):
        if self.selected:
            task = db.get(Task, self.selected)
            if task and task.status in {TaskStatus.QUEUED, TaskStatus.WAITING_APPROVAL}:
                try:
                    self.service(db).get(task.product_id, task.id, actor=self.actor)
                    return task
                except LookupError:
                    pass
        tasks = db.scalars(select(Task).where(Task.assigned_agent == "company_intake",
            Task.status.in_([TaskStatus.QUEUED, TaskStatus.WAITING_APPROVAL])
        ).order_by(Task.created_at.desc(), Task.id.desc()).limit(20))
        for task in tasks:
            try:
                self.service(db).get(task.product_id, task.id, actor=self.actor)
                self.selected = task.id
                return task
            except LookupError:
                continue
        return None

    async def menu(self, chat_id):
        with self.session_factory() as db:
            products = list(db.scalars(select(Product).order_by(Product.name).limit(20)))
            buttons = [[{"text": p.name[:40], "callback_data": f"company:open:{p.id}"}] for p in products]
        buttons.append([{"text": "➕ Новая компания", "callback_data": "company:new"}])
        await self.bot._send(chat_id, "🏢 Выбери компанию или добавь новую.",
                             reply_markup={"inline_keyboard": buttons})

    async def _help(self, chat_id, task_id):
        await self.bot._send(chat_id,
            "Пришли то, что уже есть: описание, ссылку, название / ИНН или прайс. "
            "Можно отправить несколько сообщений и файлов. "
            + FILE_HELP + " Когда закончишь, нажми «Проверить профиль».",
            reply_markup={"keyboard": [
                [{"text": "🌐 Сайт"}, {"text": "🏷 ИНН / название"}],
                [{"text": "📎 Файл / прайс"}, {"text": "✍️ Написать своими словами"}],
                [{"text": "👀 Проверить профиль"}, {"text": "❓ Чего не хватает"}],
                [{"text": "📊 Главное"}, {"text": "🏢 Компания"}],
            ], "resize_keyboard": True})

    async def message(self, message):
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        if text in {"🏢 Компания", "/company"}:
            await self.menu(chat_id)
            return True
        if text in CONTROL_BUTTONS or text.startswith("/"):
            return False
        if self.mode == "new" and text and text not in COMPANY_BUTTONS and not text.startswith("/"):
            with self.session_factory() as db:
                product = Product(name=clean_text(text)[:200], slug="company-" + uuid4().hex[:12])
                db.add(product)
                db.commit()
                session = self.service(db).start(product.id, actor=self.actor)
                self.selected = UUID(session["session_id"])
                self.service(db).add_text(product.id, self.selected, text, actor=self.actor,
                                          reference=f"telegram:{message.get('message_id')}")
            self.mode = None
            await self._help(chat_id, self.selected)
            return True
        with self.session_factory() as db:
            task = self.active(db)
            if task is None:
                if text in COMPANY_BUTTONS:
                    await self.menu(chat_id)
                    return True
                return False
            product_id, session_id = task.product_id, task.id
        if text in {"👀 Проверить профиль", "❓ Чего не хватает"}:
            await self.review(chat_id, product_id, session_id, questions_only=text.startswith("❓"))
            return True
        if text in {"🌐 Сайт", "🏷 ИНН / название", "📎 Файл / прайс", "✍️ Написать своими словами", "➕ Добавить данные"}:
            self.mode = "identifier" if text.startswith("🏷") else ("link" if text.startswith("🌐") else None)
            await self.bot._send(chat_id, "Пришли данные обычным сообщением или файлом. " + FILE_HELP)
            return True
        if text.startswith("/") or text in {"📊 Главное", "✅ На согласование", "🧪 Тесты", "🔄 Обновить"}:
            return False
        try:
            with self.session_factory() as db:
                service = self.service(db)
                reference = f"telegram:{chat_id}:{message.get('message_id')}"
                if message.get("document") or message.get("photo"):
                    document = message.get("document") or max(message["photo"], key=lambda p: p.get("width", 0) * p.get("height", 0))
                    filename = document.get("file_name") or ("photo.jpg" if message.get("photo") else "unknown")
                    file_id = document.get("file_id", "")
                    if document.get("file_size", 0) > MAX_FILE_BYTES:
                        source = make_source("owner_file", f"telegram-file:{file_id}",
                            filename=filename, status="unsupported", note="Файл слишком большой; предел — 20 МБ.")
                        result = service.add_source(product_id, session_id, source, actor=self.actor)
                    else:
                        try:
                            raw = await self.bot.api.download_file(file_id, MAX_FILE_BYTES)
                            parsed = await parse_file_async(filename, raw, f"telegram-file:{file_id}")
                            result = service.add_source(product_id, session_id, parsed, actor=self.actor)
                        except (OSError, ValueError, RuntimeError, httpx.HTTPError):
                            result = service.add_source(product_id, session_id,
                                make_source("owner_file", f"telegram-file:{file_id}", filename=filename,
                                    status="unavailable", note="Файл не удалось скачать. Можно прислать текст."),
                                actor=self.actor)
                    caption = message.get("caption")
                    if caption:
                        result = service.add_text(product_id, session_id, caption, actor=self.actor, reference=reference)
                    source = next((s for s in result["sources"] if s["reference"] == f"telegram-file:{file_id}"), None)
                    notice = ((source["note"] or "Материал прочитан частично.") if source and source["status"] != "read"
                              else "Файл прочитан и добавлен." + (" " + source["note"] if source else ""))
                elif text.casefold() == "не знаю":
                    state = service.get(product_id, session_id, actor=self.actor)
                    if not state["review_current"] or not state["review"]["questions"]:
                        raise IntakeError("Сначала откройте «Чего не хватает».")
                    field = state["review"]["questions"][0]["field_path"]
                    service.answer_unknown(product_id, session_id, field, actor=self.actor)
                    notice = "Первый из показанных вопросов оставлен неизвестным. Данные не придуманы."
                elif text:
                    if self.mode == "identifier":
                        state = await service.add_identifier(product_id, session_id, text, actor=self.actor)
                        registry = [s for s in state["sources"] if s["source_type"] == "official_registry"]
                        if registry and registry[-1]["status"] == "read":
                            notice = "Компания найдена в реестре, сведения добавлены."
                        elif registry:
                            notice = "Идентификатор принят. " + (registry[-1]["note"] or "Реестр не подтвердил данные.")
                        else:
                            notice = "Идентификатор принят. Проверка по реестру пока недоступна."
                    else:
                        service.add_text(product_id, session_id, text, actor=self.actor, reference=reference)
                        links = re.findall(r"https?://[^\s,]+", text)
                        if self.mode == "link" and not links:
                            links = ["https://" + text.strip()]
                        if not links:
                            links = ["https://" + domain for domain in re.findall(
                                r"\b(?:[a-zA-Z0-9-]+\.)+(?:ru|com|net|org|site)\b", text)]
                        issues, before = [], None
                        for link in links[:3]:
                            link = link.rstrip(".;)")
                            if before is None:
                                before = {s["id"] for s in service.get(product_id, session_id, actor=self.actor)["sources"]}
                            state = await service.add_link(product_id, session_id, link, actor=self.actor)
                            issues.extend(s["note"] for s in state["sources"]
                                          if s["source_type"] == "owned_website" and s["id"] not in before
                                          and s["status"] != "read" and s["note"])
                        pages = [s for s in (state["sources"] if before is not None else [])
                                 if s["source_type"] == "owned_website" and s["id"] not in before and s["status"] != "unavailable"]
                        notice = "Материалы добавлены. Можно прислать ещё или проверить профиль."
                        if len(pages) > 1:
                            notice = f"Прочитано страниц сайта: {len(pages)}. " + notice
                        if issues:
                            notice += "\n" + "\n".join(dict.fromkeys(issues))
                elif message.get("voice") or message.get("audio"):
                    service.add_source(product_id, session_id,
                        make_source("owner_file", reference, status="unsupported",
                                    note="Распознавание аудио пока не поддерживается."), actor=self.actor)
                    notice = "Аудио пока не распознаю. Пришлите текст или поддерживаемый документ."
                else:
                    return False
            self.mode = None
            await self.bot._send(chat_id, notice, reply_markup={"inline_keyboard": [
                [{"text": "👀 Проверить профиль", "callback_data": f"company:review:{session_id}"}],
                [{"text": "📎 Добавить данные", "callback_data": f"company:add:{session_id}"}],
            ]})
        except (IntakeError, LookupError) as exc:
            await self.bot._send(chat_id, str(exc))
        return True

    async def review(self, chat_id, product_id, session_id, *, questions_only=False):
        try:
            with self.session_factory() as db:
                service = self.service(db)
                state = service.get(product_id, session_id, actor=self.actor)
                # Empty/unchanged material needs no model call.
                if state["review_current"]:
                    review = state["review"]
                else:
                    if any(s["text"] and s["source_type"] != "existing_profile" for s in state["sources"]):
                        if self.llm_builder is None:
                            from app.core.composition import build_intake
                            service = build_intake(db, self.settings_factory())
                        else:
                            service = self.llm_builder(db, self.settings_factory())
                    review = await service.build(product_id, session_id, actor=self.actor)
            if not questions_only:
                body = render_review(review)
                # Never truncate changed values; send the full review in readable chunks.
                for start in range(0, len(body), 3500):
                    await self.bot._send(chat_id, body[start:start + 3500])
            questions = review["questions"]
            question_body = "❓ Уточнения\n\n" + "\n\n".join(
                f"{index + 1}. {item['text']}" for index, item in enumerate(questions))
            if not questions:
                question_body = "Уточнений для этой проверки больше нет. Неизвестные данные сохранены как неизвестные."
            await self.bot._send(chat_id, question_body + "\n\nМожно отвечать своими словами. «Не знаю» относится к первому показанному вопросу.",
                reply_markup={"inline_keyboard": [
                    ([{"text": "👀 Проверить профиль", "callback_data": f"company:review:{session_id}"}]
                     if questions_only else
                     [{"text": "✅ Подтвердить всё", "callback_data": f"company:approve:{review['review_id']}"}]),
                    [{"text": "✏️ Исправить", "callback_data": f"company:add:{session_id}"},
                     {"text": "❓ Ответить на вопросы", "callback_data": f"company:questions:{session_id}"}],
                    [{"text": "📎 Добавить данные", "callback_data": f"company:add:{session_id}"},
                     {"text": "🚫 Отменить", "callback_data": f"company:cancel:{session_id}"}],
                ]})
        except (ValueError, LookupError) as exc:
            # Provider credential errors must not leak environment values to chat.
            text = str(exc) if isinstance(exc, IntakeError) else "Не удалось подготовить проверку. Материалы сохранены."
            await self.bot._send(chat_id, text)

    async def callback(self, chat_id, data):
        if not data.startswith("company:"):
            return False
        try:
            if data == "company:new":
                self.mode = "new"
                await self.bot._send(chat_id, "Как называется новая компания? Напиши название.")
                return True
            _, action, raw_id = data.split(":", 2)
            record_id = UUID(raw_id)
            with self.session_factory() as db:
                service = self.service(db)
                if action == "open":
                    self.mode = None
                    session = service.start(record_id, actor=self.actor)
                    self.selected = UUID(session["session_id"])
                    await self._help(chat_id, self.selected)
                    return True
                if action == "approve":
                    artifact = db.get(Artifact, record_id)
                    if artifact is None or artifact.kind != "company_profile_review":
                        raise LookupError("Проверка не найдена.")
                    service.approve(artifact.product_id, artifact.task_id, artifact.id,
                                    actor=self.actor, confirmed=True)
                    await self.bot._send(chat_id, "✅ Профиль подтверждён и сохранён. Предыдущая версия доступна в истории. Обращения клиентам не отправлялись.")
                    return True
                task = db.get(Task, record_id)
                if task is None:
                    raise LookupError("Сбор данных не найден.")
                service.get(task.product_id, task.id, actor=self.actor)
                self.selected = task.id
                product_id, session_id = task.product_id, task.id
                if action == "cancel":
                    self.mode = None
                    service.cancel(product_id, session_id, actor=self.actor)
                    await self.bot._send(chat_id, "Сбор данных отменён. Профиль не изменён.")
                    return True
                if action == "add":
                    await self._help(chat_id, session_id)
                    return True
            if action in {"review", "questions"}:
                await self.review(chat_id, product_id, session_id, questions_only=action == "questions")
            return True
        except (ValueError, LookupError):
            await self.bot._send(chat_id, "Команда устарела или данные недоступны. Откройте раздел «Компания» заново.")
            return True
