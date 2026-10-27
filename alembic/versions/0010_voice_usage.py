"""Voice module: dictation usage and cost alerts (counts and timings only, no audio, no text)."""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def stamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade():
    op.create_table("voice_usage",
        sa.Column("id", sa.Uuid(), primary_key=True), *stamps(),
        sa.Column("account", sa.String(200), nullable=False),
        sa.Column("project", sa.String(64)),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("detail", sa.String(60)),
        sa.Column("audio_seconds", sa.Float()),
        sa.Column("stt_model", sa.String(100)),
        sa.Column("llm_model", sa.String(100)),
        sa.Column("llm_input_tokens", sa.Integer()),
        sa.Column("llm_output_tokens", sa.Integer()),
        sa.Column("stt_ms", sa.Integer()),
        sa.Column("llm_ms", sa.Integer()),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("incurred_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_voice_usage_incurred_at", "voice_usage", ["incurred_at"])
    op.create_table("voice_cost_alerts",
        sa.Column("id", sa.Uuid(), primary_key=True), *stamps(),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("day_cost_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("usual_cost_usd", sa.Numeric(18, 6)))
    op.create_index("ix_voice_cost_alerts_day", "voice_cost_alerts", ["day"], unique=True)


def downgrade():
    op.drop_index("ix_voice_cost_alerts_day", table_name="voice_cost_alerts")
    op.drop_table("voice_cost_alerts")
    op.drop_index("ix_voice_usage_incurred_at", table_name="voice_usage")
    op.drop_table("voice_usage")
