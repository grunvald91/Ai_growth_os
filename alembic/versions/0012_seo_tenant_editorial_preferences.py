"""Per-company SEO editorial settings. Existing rows keep historical defaults.

Revision 0012 only adds nullable overrides; no company is opted into SEO.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("seo_factory_activations", sa.Column("timezone_name", sa.String(64), nullable=True))
    op.add_column("seo_factory_activations", sa.Column("autorun_interval_days", sa.Integer(), nullable=True))
    op.add_column("seo_factory_activations", sa.Column("autorun_horizon_days", sa.Integer(), nullable=True))
    op.add_column("seo_factory_activations", sa.Column("publications_per_day", sa.Integer(), nullable=True))
    op.add_column("seo_factory_activations", sa.Column("articles_per_day", sa.Integer(), nullable=True))


def downgrade():
    for name in ("articles_per_day", "publications_per_day", "autorun_horizon_days",
                 "autorun_interval_days", "timezone_name"):
        op.drop_column("seo_factory_activations", name)
