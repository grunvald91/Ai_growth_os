import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Approval, Artifact, ArtifactStatus, Experiment, Prospect
from app.services.approvals import ApprovalService
from app.services.artifacts import ArtifactService


_DIRECT_CHANNELS = {"email", "whatsapp", "telegram"}


@dataclass(frozen=True)
class OutreachPackageResult:
    artifact: Artifact
    approval: Approval
    message_count: int


class OutreachPackageService:
    """Prepare an immutable, reviewable outreach batch. Never sends messages."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def prepare(
        self,
        *,
        experiment_id: UUID,
        limit: int = 10,
    ) -> OutreachPackageResult:
        experiment = self.db.get(Experiment, experiment_id)
        if experiment is None:
            raise LookupError("Experiment not found")
        if experiment.status != "running":
            raise ValueError("Outreach packages require a running experiment")

        prospects = list(
            self.db.scalars(
                select(Prospect)
                .where(
                    Prospect.experiment_id == experiment.id,
                    Prospect.validation_status == "validated",
                    Prospect.outreach_status == "not_contacted",
                    Prospect.preferred_channel.in_(sorted(_DIRECT_CHANNELS)),
                )
                .order_by(Prospect.lead_key.asc(), Prospect.id)
                .limit(limit)
            )
        )
        messages = [self._draft(prospect) for prospect in prospects]
        if not messages:
            raise ValueError("No validated prospects with direct channels are ready")

        payload = {
            "experiment_id": str(experiment.id),
            "experiment_key": experiment.experiment_key,
            "status": "awaiting_human_approval",
            "send_policy": {
                "send_only_after_explicit_approval": True,
                "max_one_first_contact_per_prospect": True,
                "no_cross_channel_duplicate_contact": True,
                "no_automatic_followup": True,
            },
            "messages": messages,
        }
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        title = f"{experiment.experiment_key or experiment.id}: outreach batch 1"

        artifact = self.db.scalar(
            select(Artifact).where(
                Artifact.product_id == experiment.product_id,
                Artifact.kind == "outreach_package",
                Artifact.title == title,
                Artifact.content == content,
            )
        )
        if artifact is None:
            artifact = ArtifactService(self.db).create(
                product_id=experiment.product_id,
                kind="outreach_package",
                title=title,
                content=content,
                status=ArtifactStatus.AWAITING_APPROVAL,
            )

        approval = self.db.scalar(
            select(Approval)
            .where(
                Approval.product_id == experiment.product_id,
                Approval.artifact_id == artifact.id,
                Approval.action_type == "send_outreach_batch",
            )
            .order_by(Approval.created_at.desc(), Approval.id)
            .limit(1)
        )
        if approval is None:
            channel_counts: dict[str, int] = {}
            for message in messages:
                channel_counts[message["channel"]] = channel_counts.get(message["channel"], 0) + 1
            counts = ", ".join(
                f"{name}: {count}" for name, count in sorted(channel_counts.items())
            )
            approval = ApprovalService(self.db).create(
                product_id=experiment.product_id,
                artifact_id=artifact.id,
                action_type="send_outreach_batch",
                risk_level="YELLOW",
                status="pending",
                requested_by="growth_os:outreach_preparer",
                reason=(
                    f"{len(messages)} exact first-contact drafts are ready ({counts}). "
                    "Approve only if these messages may be sent to these exact prospects. "
                    "Approval does not itself send anything until Communication Gateway execution is connected."
                ),
            )

        self.db.commit()
        return OutreachPackageResult(
            artifact=artifact,
            approval=approval,
            message_count=len(messages),
        )

    @staticmethod
    def _draft(prospect: Prospect) -> dict:
        channel = prospect.preferred_channel
        if channel not in _DIRECT_CHANNELS:
            raise ValueError(f"{prospect.lead_key}: direct preferred channel required")
        target = getattr(prospect, channel)
        if not target:
            raise ValueError(f"{prospect.lead_key}: target for {channel} is missing")
        if not prospect.tracking_url:
            raise ValueError(f"{prospect.lead_key}: tracking URL is missing")

        fact = (prospect.notes or "").strip()
        if not fact:
            fact = f"{prospect.business_name} работает в сфере {prospect.category or 'услуг'}."

        body = (
            "Здравствуйте! Меня зовут Артур, GrunvaldLab. "
            f"Увидел, что {fact} "
            "Я делаю ServGo — онлайн-запись для сервисного бизнеса: клиент выбирает "
            "услугу, специалиста, дату и время, а бизнес получает структурированную "
            "запись и напоминания. "
            f"Если это актуально, могу показать короткое демо под ваш формат: {prospect.tracking_url} "
            "Если неактуально — всё нормально, больше писать не буду."
        )
        subject = (
            f"ServGo для {prospect.business_name}: короткое демо онлайн-записи"
            if channel == "email"
            else None
        )
        return {
            "lead_id": prospect.lead_key,
            "prospect_id": str(prospect.id),
            "business_name": prospect.business_name,
            "channel": channel,
            "target": target,
            "subject": subject,
            "body": body,
            "tracking_url": prospect.tracking_url,
            "evidence_count": len(prospect.evidence or []),
        }
