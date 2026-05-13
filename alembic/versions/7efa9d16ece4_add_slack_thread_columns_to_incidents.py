"""add slack thread columns to incidents

Revision ID: 7efa9d16ece4
Revises: 6afb061f570b
Create Date: 2026-05-13 15:59:51.012478

Phase 2 — Slack Bot Token 기반 chat.postMessage + chat.update 를 위해 incident
당 채널 + 메시지 ts 를 영속화한다. dry-run 모드에서는 둘 다 NULL 유지.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7efa9d16ece4"
down_revision: str | Sequence[str] | None = "6afb061f570b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("slack_channel_id", sa.String(length=50), nullable=True))
    op.add_column("incidents", sa.Column("slack_ts", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "slack_ts")
    op.drop_column("incidents", "slack_channel_id")
