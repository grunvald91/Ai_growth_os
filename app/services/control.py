from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.control.contracts import (
    ControlAction,
    ControlExperiment,
    ControlInboxItem,
    ControlStatus,
    ProductControlStatus,
)
from app.models.domain import Approval, Experiment, Product


_ACTIVE_EXPERIMENT_STATUSES = frozenset({"running", "active"})


class ControlService:
    """Read-only human control plane derived from canonical Growth OS records."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def inbox(self, product_id: UUID, *, limit: int = 20) -> list[ControlInboxItem]:
        self._require_product(product_id)
        approvals = list(
            self.db.scalars(
                select(Approval)
                .where(Approval.product_id == product_id, Approval.status == "pending")
                .order_by(Approval.created_at.asc(), Approval.id)
                .limit(limit)
            )
        )
        return [self._approval_item(record) for record in approvals]

    def product_status(self, product_id: UUID) -> ProductControlStatus:
        product = self._require_product(product_id)
        experiments = list(
            self.db.scalars(
                select(Experiment)
                .where(Experiment.product_id == product_id)
                .order_by(Experiment.created_at.desc(), Experiment.id)
            )
        )
        preview = self.inbox(product_id, limit=5)
        active = sum(record.status in _ACTIVE_EXPERIMENT_STATUSES for record in experiments)
        drafts = sum(record.status == "draft" for record in experiments)

        current = [
            ControlExperiment(
                id=record.id,
                experiment_key=record.experiment_key,
                name=record.name,
                status=record.status,
                target_metric=record.target_metric,
                created_at=record.created_at,
            )
            for record in experiments
            if record.status in _ACTIVE_EXPERIMENT_STATUSES or record.status == "draft"
        ][:5]

        return ProductControlStatus(
            product_id=product.id,
            product_name=product.name,
            pending_approvals=len(preview) if len(preview) < 5 else self._pending_count(product_id),
            decisions_waiting=self._pending_count(product_id),
            active_experiments=active,
            draft_experiments=drafts,
            experiments=current,
            inbox_preview=preview,
        )

    def all_status(self) -> ControlStatus:
        products = list(self.db.scalars(select(Product).order_by(Product.created_at.asc(), Product.id)))
        snapshots = [self.product_status(product.id) for product in products]
        return ControlStatus(
            products=snapshots,
            decisions_waiting=sum(item.decisions_waiting for item in snapshots),
            active_experiments=sum(item.active_experiments for item in snapshots),
        )

    def _pending_count(self, product_id: UUID) -> int:
        return len(
            list(
                self.db.scalars(
                    select(Approval.id).where(
                        Approval.product_id == product_id,
                        Approval.status == "pending",
                    )
                )
            )
        )

    def _require_product(self, product_id: UUID) -> Product:
        product = self.db.get(Product, product_id)
        if product is None:
            raise LookupError("Product not found")
        return product

    @staticmethod
    def _approval_item(record: Approval) -> ControlInboxItem:
        summary = record.reason or (
            f"Требуется решение по действию {record.action_type}. "
            "Внешнее действие не должно выполняться до одобрения."
        )
        return ControlInboxItem(
            id=record.id,
            product_id=record.product_id,
            kind="decision_required",
            title=f"Нужно решение: {record.action_type}",
            summary=summary,
            risk_level=record.risk_level,
            action_type=record.action_type,
            created_at=record.created_at,
            actions=(
                [
                    ControlAction(key="reject", label="Отклонить"),
                    ControlAction(key="open", label="Подробнее"),
                ]
                if record.risk_level.lower() == "red"
                else [
                    ControlAction(key="approve", label="Одобрить"),
                    ControlAction(key="reject", label="Отклонить"),
                    ControlAction(key="open", label="Подробнее"),
                ]
            ),
        )
