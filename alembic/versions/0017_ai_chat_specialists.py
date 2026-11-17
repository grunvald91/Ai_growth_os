"""Persist specialist selected for each ServGo AI Chat conversation.

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_chat_conversations", sa.Column("specialist_id", sa.String(40), nullable=True))


def downgrade():
    op.drop_column("ai_chat_conversations", "specialist_id")
