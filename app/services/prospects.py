from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Prospect


_ENRICHMENT_FIELDS = ("website", "email", "whatsapp", "telegram")
_VALIDATION_STATUSES = {"pending", "needs_review", "validated"}
_ENRICHMENT_STATUSES = {"pending", "direct", "website_or_chat", "phone_only", "stale"}
_PREFERRED_CHANNELS = set(_ENRICHMENT_FIELDS)


class ProspectService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def import_cohort(
        self,
        *,
        product_id: UUID,
        experiment_id: UUID,
        leads: list[dict],
    ) -> list[Prospect]:
        imported: list[Prospect] = []
        for row in leads[:100]:
            lead_key = str(row.get("lead_id") or "").strip()
            business_name = str(row.get("business_name") or "").strip()
            if not lead_key or not business_name:
                continue
            prospect = self.db.scalar(
                select(Prospect).where(
                    Prospect.experiment_id == experiment_id,
                    Prospect.lead_key == lead_key,
                )
            )
            self._validate_enrichment_resolution(lead_key, prospect)

            checked_at = self._date(row.get("source_checked_at"))
            if prospect is None:
                prospect = Prospect(
                    product_id=product_id,
                    experiment_id=experiment_id,
                    lead_key=lead_key,
                    business_name=business_name,
                    area=self._text(row.get("area")),
                    category=self._text(row.get("category")),
                    public_phone=self._text(row.get("public_phone")),
                    address=self._text(row.get("address")),
                    source_place_id=self._text(row.get("source_place_id")),
                    source_checked_at=checked_at,
                    fit_status=self._text(row.get("fit_status")) or "needs_validation",
                    validation_status="pending",
                    enrichment_status="pending",
                    outreach_status=self._text(row.get("outreach_status")) or "not_contacted",
                    tracking_url=self._text(row.get("tracking_url")),
                    evidence=[],
                )
                self.db.add(prospect)
                self.db.flush()
            else:
                # Cohort import may refresh public directory facts, but must never
                # overwrite enriched channels or contact history.
                prospect.business_name = business_name
                prospect.area = self._text(row.get("area"))
                prospect.category = self._text(row.get("category"))
                prospect.public_phone = self._text(row.get("public_phone"))
                prospect.address = self._text(row.get("address"))
                prospect.source_place_id = self._text(row.get("source_place_id"))
                prospect.source_checked_at = checked_at
                prospect.fit_status = self._text(row.get("fit_status")) or prospect.fit_status
                prospect.tracking_url = self._text(row.get("tracking_url")) or prospect.tracking_url
                self.db.flush()
            imported.append(prospect)
        return imported

    def apply_enrichment(
        self,
        *,
        experiment_id: UUID,
        rows: list[dict],
    ) -> list[Prospect]:
        updated: list[Prospect] = []
        for row in rows[:100]:
            lead_key = self._text(row.get("lead_id"))
            if not lead_key:
                raise ValueError("Enrichment row requires lead_id")
            prospect = self.db.scalar(
                select(Prospect).where(
                    Prospect.experiment_id == experiment_id,
                    Prospect.lead_key == lead_key,
                )
            )
            if prospect is None:
                raise LookupError(f"Prospect not found: {lead_key}")

            evidence = row.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError(f"{lead_key}: evidence is required")
            normalized_evidence = [
                self._normalize_evidence(lead_key, item)
                for item in evidence
            ]

            validation_status = self._text(row.get("validation_status"))
            if validation_status is not None:
                if validation_status not in _VALIDATION_STATUSES:
                    raise ValueError(
                        f"{lead_key}: unsupported validation_status {validation_status}"
                    )
                if validation_status == "validated" and not any(
                    item["field"] == "identity" for item in normalized_evidence
                ):
                    raise ValueError(
                        f"{lead_key}: validated status requires identity evidence"
                    )
                prospect.validation_status = validation_status

            enrichment_status = self._text(row.get("enrichment_status"))
            if enrichment_status is not None:
                if enrichment_status not in _ENRICHMENT_STATUSES:
                    raise ValueError(
                        f"{lead_key}: unsupported enrichment_status {enrichment_status}"
                    )
                prospect.enrichment_status = enrichment_status

            for field in _ENRICHMENT_FIELDS:
                value = self._text(row.get(field))
                if value is None:
                    continue
                if not any(
                    item["field"] == field and item["value"] == value
                    for item in normalized_evidence
                ):
                    raise ValueError(
                        f"{lead_key}: {field} requires matching evidence"
                    )
                existing = self._text(getattr(prospect, field))
                if existing and existing != value:
                    raise ValueError(
                        f"{lead_key}: refusing to overwrite existing {field}"
                    )
                setattr(prospect, field, value)

            preferred_channel = self._text(row.get("preferred_channel"))
            if preferred_channel is not None:
                if preferred_channel not in _PREFERRED_CHANNELS:
                    raise ValueError(
                        f"{lead_key}: unsupported preferred_channel {preferred_channel}"
                    )
                if not self._text(getattr(prospect, preferred_channel)):
                    raise ValueError(
                        f"{lead_key}: preferred channel has no verified value"
                    )
                prospect.preferred_channel = preferred_channel

            checked_at = self._date(row.get("source_checked_at"))
            if checked_at is not None:
                prospect.source_checked_at = checked_at

            note = self._text(row.get("notes"))
            if note:
                prospect.notes = note

            merged = list(prospect.evidence or [])
            seen = {self._evidence_key(item) for item in merged if isinstance(item, dict)}
            for item in normalized_evidence:
                key = self._evidence_key(item)
                if key not in seen:
                    merged.append(item)
                    seen.add(key)
            prospect.evidence = merged
            self.db.flush()
            updated.append(prospect)
        return updated

    def stats_for_experiment(self, experiment_id: UUID) -> dict[str, int]:
        prospects = self.list_for_experiment(experiment_id)
        return {
            "total": len(prospects),
            "validated": sum(p.validation_status == "validated" for p in prospects),
            "enrichment_resolved": sum(p.enrichment_status != "pending" for p in prospects),
            "enrichment_pending": sum(p.enrichment_status == "pending" for p in prospects),
            "direct": sum(p.enrichment_status == "direct" for p in prospects),
            "website_or_chat": sum(p.enrichment_status == "website_or_chat" for p in prospects),
            "phone_only": sum(p.enrichment_status == "phone_only" for p in prospects),
            "stale": sum(p.enrichment_status == "stale" for p in prospects),
            "phone": sum(bool(p.public_phone) for p in prospects),
            "contact_found": sum(
                p.validation_status == "validated"
                and bool(
                    p.public_phone
                    or p.email
                    or p.whatsapp
                    or p.telegram
                    or p.website
                )
                for p in prospects
            ),
            "needs_review": sum(p.validation_status == "needs_review" for p in prospects),
            "email": sum(bool(p.email) for p in prospects),
            "whatsapp": sum(bool(p.whatsapp) for p in prospects),
            "telegram": sum(bool(p.telegram) for p in prospects),
            "website": sum(bool(p.website) for p in prospects),
            "ready_for_outreach": sum(
                p.validation_status == "validated"
                and p.preferred_channel in {"email", "whatsapp", "telegram"}
                and bool(getattr(p, p.preferred_channel))
                for p in prospects
            ),
            "contacted": sum(p.outreach_status != "not_contacted" for p in prospects),
        }

    def list_for_experiment(self, experiment_id: UUID) -> list[Prospect]:
        return list(
            self.db.scalars(
                select(Prospect)
                .where(Prospect.experiment_id == experiment_id)
                .order_by(Prospect.lead_key.asc(), Prospect.id)
            )
        )

    @staticmethod
    def _validate_enrichment_resolution(lead_key: str, prospect: Prospect) -> None:
        status = prospect.enrichment_status
        if status == "pending":
            return
        direct_available = bool(prospect.email or prospect.whatsapp or prospect.telegram)
        if status == "direct" and not direct_available:
            raise ValueError(f"{lead_key}: direct status requires email/whatsapp/telegram")
        if status == "website_or_chat" and not prospect.website:
            raise ValueError(f"{lead_key}: website_or_chat status requires website")
        if status == "phone_only" and not prospect.public_phone:
            raise ValueError(f"{lead_key}: phone_only status requires public_phone")
        if status == "stale":
            prospect.preferred_channel = None

    @staticmethod
    def _normalize_evidence(lead_key: str, item) -> dict:
        if not isinstance(item, dict):
            raise ValueError(f"{lead_key}: evidence item must be an object")
        field = ProspectService._text(item.get("field"))
        value = ProspectService._text(item.get("value"))
        source_url = ProspectService._text(item.get("source_url"))
        observed_at = ProspectService._text(item.get("observed_at"))
        source_type = ProspectService._text(item.get("source_type"))
        if not all((field, value, source_url, observed_at, source_type)):
            raise ValueError(
                f"{lead_key}: evidence requires field/value/source_url/observed_at/source_type"
            )
        if field != "identity" and field not in _ENRICHMENT_FIELDS:
            raise ValueError(f"{lead_key}: unsupported evidence field {field}")
        return {
            "field": field,
            "value": value,
            "source_url": source_url,
            "observed_at": observed_at,
            "source_type": source_type,
        }

    @staticmethod
    def _evidence_key(item: dict) -> tuple:
        return (
            item.get("field"),
            item.get("value"),
            item.get("source_url"),
            item.get("observed_at"),
            item.get("source_type"),
        )

    @staticmethod
    def _text(value) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _date(value) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None
