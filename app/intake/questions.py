"""Ask for missing blockers in ordinary language, never demand the schema."""
QUESTIONS = [
    ("identity.name", "Как называется компания или продукт?"),
    ("identity.one_liner", "Что вы предлагаете клиентам? Можно описать своими словами."),
    ("customers.primary_segments", "Кому вы в первую очередь хотите продавать?"),
    ("identity.languages|identity.service_geography", "Где вы работаете и на каких языках обслуживаете клиентов?"),
    ("offer.products_services", "Какие основные услуги или продукты вы предлагаете?"),
    ("offer.primary_conversion_action", "Какой следующий шаг должен сделать заинтересованный клиент?"),
    ("brand.prohibited_claims", "Что команда не должна обещать или утверждать о вашем бизнесе? Можно ответить «ограничений нет»."),
    ("constraints.privacy", "Какие данные нельзя использовать в материалах и передавать ИИ?"),
    ("constraints.legal_compliance", "Есть ли обязательные юридические ограничения для вашей рекламы?"),
    ("constraints.operational", "Какие рабочие ограничения важно учитывать: сроки, объём заказов, доступность?"),
    ("constraints.approval_rules", "Какие действия команда обязательно должна согласовать с вами?"),
    ("brand.tone", "Каким тоном говорить с клиентами: деловым, дружелюбным или другим?"),
    ("proof.approved_facts|proof.approved_numbers|proof.no_proof_yet", "Какие подтверждённые факты или результаты можно использовать? Если доказательств пока нет, так и напишите."),
    ("outreach.icp_criteria", "По каким признакам выбирать компании для персонального обращения?"),
    ("outreach.allowed_channels", "В каких каналах можно готовить обращения: email, Telegram, WhatsApp?"),
    ("outreach.sender_identity", "От чьего имени готовить обращения?"),
    ("outreach.approval_required", "Нужно ли согласовать каждое обращение до отправки?"),
    ("outreach.do_not_contact", "Кому нельзя писать? Если таких ограничений нет, укажите это явно."),
    ("measurement.funnel_stages", "Какие шаги проходит клиент от первого интереса до покупки?"),
    ("measurement.primary_conversion_event", "Какое событие считать главным результатом маркетинга?"),
    ("measurement.attribution_rules", "Как определять, откуда пришёл клиент?"),
]


def missing_questions(readiness: dict, skipped: list[str]) -> list[dict]:
    missing = set()
    for gate in ("strategy", "content", "outreach", "measurement"):
        missing.update(readiness[gate]["missing_fields"])
    questions = []
    for field, text in QUESTIONS:
        if field in missing and field not in skipped:
            blocks = [gate for gate, state in readiness.items() if field in state["missing_fields"]]
            questions.append({"field_path": field, "text": text, "blocks": blocks,
                              "options": ["Отвечу своими словами", "Не знаю"]})
        if len(questions) == 3:
            break
    return questions
