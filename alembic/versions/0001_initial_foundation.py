"""Initial foundation schema.

Revision ID: 0001
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep migration generation reproducible from the model metadata.
    from app.db.models import Base
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from app.db.models import Base
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
