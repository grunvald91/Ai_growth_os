"""Ensure one active Instagram account cannot receive webhook events for two companies.

Pre-existing duplicates require explicit operator reconciliation before upgrade;
never silently discard a customer's connection.
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    duplicates = op.get_bind().execute(sa.text("""
        SELECT account_id, COUNT(*) AS cnt
        FROM comment_channels
        WHERE platform = 'instagram' AND status = 'connected'
        GROUP BY account_id HAVING COUNT(*) > 1
    """)).fetchall()
    if duplicates:
        # No account identifiers in migration logs, only a count.
        raise RuntimeError(
            f"Cannot apply Instagram tenant uniqueness: {len(duplicates)} "
            "account(s) belong to multiple active businesses; disconnect duplicates first."
        )
    op.create_index(
        "uq_comment_ig_connected_account", "comment_channels", ["account_id"], unique=True,
        postgresql_where=sa.text("platform = 'instagram' AND status = 'connected'"),
        sqlite_where=sa.text("platform = 'instagram' AND status = 'connected'"),
    )


def downgrade():
    op.drop_index("uq_comment_ig_connected_account", table_name="comment_channels")
