"""Durable, tenant-scoped dispatcher workflows with per-step confirmations.

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def stamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade():
    op.create_table(
        "dispatcher_workflows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *stamps(),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("dispatcher_conversations.id"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("account", sa.String(200), nullable=False),
        sa.Column("client_key", sa.String(100)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("conversation_id", "client_key", name="uq_dispatcher_workflow_client_key"),
    )
    op.create_index("ix_dispatcher_workflows_conversation_id", "dispatcher_workflows", ["conversation_id"])
    op.create_index("ix_dispatcher_workflows_product_id", "dispatcher_workflows", ["product_id"])
    op.create_table(
        "dispatcher_workflow_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *stamps(),
        sa.Column("workflow_id", sa.Uuid(), sa.ForeignKey("dispatcher_workflows.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("capability", sa.String(60), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("preview", sa.JSON(), nullable=False),
        sa.Column("result_text", sa.Text(), nullable=False),
        sa.Column("result_blocks", sa.JSON(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workflow_id", "position", name="uq_dispatcher_workflow_step_position"),
    )
    op.create_index("ix_dispatcher_workflow_steps_workflow_id", "dispatcher_workflow_steps", ["workflow_id"])


def downgrade():
    op.drop_index("ix_dispatcher_workflow_steps_workflow_id", table_name="dispatcher_workflow_steps")
    op.drop_table("dispatcher_workflow_steps")
    op.drop_index("ix_dispatcher_workflows_product_id", table_name="dispatcher_workflows")
    op.drop_index("ix_dispatcher_workflows_conversation_id", table_name="dispatcher_workflows")
    op.drop_table("dispatcher_workflows")
