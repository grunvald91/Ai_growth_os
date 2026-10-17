from typing import Any
from uuid import UUID

from app.models import Artifact
from app.models.domain import ArtifactStatus
from app.services.base import CRUDService


class ArtifactService(CRUDService[Artifact]):
    model = Artifact
    update_fields = frozenset({"kind", "title", "content", "status"})

    def create(self, *, product_id: UUID, kind: str, title: str, content: str, **values: Any) -> Artifact:
        if "status" in values:
            values["status"] = self._status(values["status"])
        # Titles come from model output; the column holds 300 characters.
        artifact = Artifact(product_id=product_id, kind=kind, title=title[:300], content=content, **values)
        self.db.add(artifact)
        self.db.flush()
        self.db.refresh(artifact)
        return artifact

    def update(self, entity_id: UUID, **values: Any) -> Artifact:
        if "status" in values:
            values["status"] = self._status(values["status"])
        artifact = self.get(entity_id)
        if artifact is None:
            raise ValueError(f"Artifact {entity_id} was not found")
        unknown = set(values) - self.update_fields
        if unknown:
            raise ValueError(f"Unsupported fields: {', '.join(sorted(unknown))}")
        if artifact.kind in {"comment_reply_approved", "comment_agent_settings"} and any(
                key in values and values[key] != getattr(artifact, key)
                for key in ("kind", "title", "content")):
            raise ValueError("Comment approval/settings content is immutable; create a new version")
        if artifact.kind in {"outreach_package", "outreach_message", "video_script", "channel_text"} and any(
                key in values and values[key] != getattr(artifact, key)
                for key in ("kind", "title", "content")):
            raise ValueError("Outreach package content is immutable; create a new package")
        for key, value in values.items():
            setattr(artifact, key, value)
        artifact.version += 1
        self.db.flush()
        self.db.refresh(artifact)
        return artifact

    @staticmethod
    def _status(status: ArtifactStatus | str) -> ArtifactStatus:
        try:
            return status if isinstance(status, ArtifactStatus) else ArtifactStatus(status)
        except ValueError as exc:
            raise ValueError(f"Invalid artifact status: {status}") from exc
