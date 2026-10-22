"""Owner dispatcher: conversations and messages."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def stamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade():
    op.create_table("dispatcher_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True), *stamps(),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("account", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False))
    op.create_index("ix_dispatcher_conversations_product_id", "dispatcher_conversations", ["product_id"])
    op.create_table("dispatcher_messages",
        sa.Column("id", sa.Uuid(), primary_key=True), *stamps(),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("dispatcher_conversations.id"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("input_mode", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("spoken", sa.Text()),
        sa.Column("blocks", sa.JSON(), nullable=False),
        sa.Column("intent", sa.String(20)),
        sa.Column("capability", sa.String(60)),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("model", sa.String(200)),
        sa.Column("client_key", sa.String(100)),
        sa.Column("confirmation_id", sa.String(64)),
        sa.Column("confirmation_state", sa.String(20)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_dispatcher_message_seq"),
        sa.UniqueConstraint("conversation_id", "client_key", name="uq_dispatcher_message_key"))
    for column in ("conversation_id", "product_id", "task_id", "confirmation_id"):
        op.create_index(f"ix_dispatcher_messages_{column}", "dispatcher_messages", [column])


def downgrade():
    op.drop_table("dispatcher_messages")
    op.drop_table("dispatcher_conversations")
