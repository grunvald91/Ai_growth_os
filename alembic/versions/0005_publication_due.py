"""Indexed due time and durable send lease, preserving legacy publish intents."""
import json
from datetime import datetime
from uuid import UUID
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("publications")}
    for name in ("scheduled_at", "sending_started_at"):
        if name not in columns:
            op.add_column("publications", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    pub = sa.table("publications", sa.column("id", sa.Uuid()), sa.column("product_id", sa.Uuid()),
                   sa.column("scheduled_at", sa.DateTime(timezone=True)))
    intents = bind.execute(sa.text("SELECT product_id, content FROM artifacts WHERE kind = 'telegram_publish_intent'"))
    for product_id, raw in intents:
        try:
            value = json.loads(raw)
            when = datetime.fromisoformat(value["scheduled_at"])
            if when.tzinfo is None:
                continue
            bind.execute(pub.update().where(pub.c.id == UUID(value["publication_id"]),
                pub.c.product_id == UUID(str(product_id))).values(scheduled_at=when))
        except (ValueError, KeyError, TypeError):
            continue
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("publications")}
    if "ix_publication_due" not in indexes:
        op.create_index("ix_publication_due", "publications", ["channel", "status", "scheduled_at"])


def downgrade():
    op.drop_index("ix_publication_due", table_name="publications")
    op.drop_column("publications", "sending_started_at")
    op.drop_column("publications", "scheduled_at")
