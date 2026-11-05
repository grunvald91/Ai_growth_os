# CLAUDE.md — ServGo

## Project
ServGo is the production booking platform in this repository.

Primary production service:
- repository: grunvald91/booking_bot
- default branch: main
- production path: /opt/booking_bot
- production systemd service: booking-bot

## Обязательная SaaS-архитектура ServGo (ADR-SERVGO-001)

**Нормативный документ:** [docs/architecture/SERVGO_SAAS_RULE.md](docs/architecture/SERVGO_SAAS_RULE.md).

> ServGo — многопользовательская SaaS-платформа с общей масштабируемой инфраструктурой и изолированными рабочими пространствами бизнесов. Все модули, агенты, очереди, интеграции и оркестрационные процессы проектируются так, чтобы параллельная работа множества компаний не приводила к смешиванию данных, неконтролируемому расходованию ресурсов или блокировке работы других клиентов.

Перед изменениями функций для бизнеса проверяй tenant scope, права, идемпотентность, справедливость общей очереди и учёт ресурсов по релевантности. Правило действует для всех модулей ServGo и не подтверждает готовность текущего кода к конкретному числу компаний.

**Очередность:** сначала завершить текущий редизайн; SaaS-доработки делать по мере затрагивания соответствующих компонентов. Выявленные нарушения межарендного доступа исправлять немедленно. [Поэтапный план](https://github.com/grunvald91/Ai_growth_os/blob/main/docs/architecture/SERVGO_SAAS_ROADMAP.md). Документационные изменения не разрешают деплой.

## Critical production rule
Production source and GitHub were synchronized by the owner. The source audit of 2026-10-23 (workflow "Audit production source against GitHub") found the server backend byte-identical to `main` at the time: `database/models.py`, `web/api/schemas.py`, `web/api/main.py` and the mail routes matched, and there was no server-only source. The remaining differences were repository-only files (docs, dev scripts) that are not deployed.

This is a dated fact, not a permanent guarantee. Re-run the audit before any deployment that depends on it, and never perform or propose a broad "copy whole repository to production" deployment unless a fresh audit proves the relevant production paths are synchronized.

Prefer scoped changes with explicit file lists, validation, health checks, and rollback.

## Development workflow
For every requested change:
1. Read the relevant existing code before designing changes.
2. Reuse existing architecture and behavior where possible.
3. Make the smallest coherent patch.
4. Add/update focused tests.
5. Run relevant tests.
6. Run syntax/static checks applicable to changed files.
7. Inspect git diff.
8. Commit and push before optional cleanup.
9. Summarize changed files, tests, and deployment impact.

Do not edit production directly from Claude Code.

## Existing business rules
- For venue/rental pricing, reuse the existing venue_rental pricing/configuration and current pricing logic.
- Do not create a second pricing engine unless a concrete business rule cannot be represented by the existing system.
- Prepayment is transferred directly by the customer to the business owner outside ServGo.
- The customer may mark payment sent / attach proof.
- The owner manually verifies receipt.
- A booking is only treated as payment-confirmed after owner confirmation.

Do not invent product behavior or silently change these rules.

## Safety
Never commit or print:
- Telegram bot tokens
- database passwords
- SERVER_SSH_PASSWORD
- ClaudeHub/API credentials
- cookies
- OAuth tokens
- private keys
- customer personal data

Never place production secrets in issue comments or PR descriptions.

Do not run destructive database operations unless the issue explicitly requires them and a safe migration/rollback plan is part of the task.

## Production deployment
Existing GitHub Actions already contain scoped/manual production workflows using SERVER_SSH_PASSWORD.

Until source-of-truth reconciliation is complete:
- development may be automated;
- PR creation may be automated;
- production deployment remains separately gated;
- prefer existing guarded deploy scripts/workflows for changed components.

For any production-affecting change, state:
- files that must be deployed;
- service(s) that must restart;
- migration requirements;
- health checks;
- rollback path.

## Testing
Use the repository's existing tests first. Add focused regression tests for every bug fix where practical.

A task is not complete merely because code was written; preserve a committed/pushed working checkpoint.

## GitHub Actions budget policy (mandatory)

Before planning or executing any CI, deployment or GitHub Actions operation, read and follow [AGENTS.md](AGENTS.md). Its cost, safety, diagnostics, limits and reporting rules apply equally to Claude Code and any delegated agent. Do not bypass them through issue comments or other automation triggers.

## Design system
UI work follows the ServGo Air brand book: `docs/brand/servgo-air.md` (tokens in `docs/brand/tokens.json`). On mobile, information goes at the top and controls stay at the bottom within thumb reach.

**Mobile is the primary design target.** Every page is designed for a phone first and the desktop layout is derived from it. A feature that cannot be reached in the mobile view counts as missing: every link, button and setting available on desktop must also be reachable on mobile (bottom navigation, a "More" menu or in-page controls), never only from a desktop sidebar. Before shipping UI, check it at phone width (~390px): no horizontal overflow that hides controls, primary actions within thumb reach at the bottom.
