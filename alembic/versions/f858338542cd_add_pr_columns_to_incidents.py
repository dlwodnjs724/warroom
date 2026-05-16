"""add pr columns to incidents

Revision ID: f858338542cd
Revises: 7efa9d16ece4
Create Date: 2026-05-16 12:16:01.724126

Phase 4.5 — 인시던트 반려 시 PR close + branch 삭제를 위해 PR 번호와
브랜치 이름을 영속화한다. dry-run / markdown-only / category != code
케이스는 NULL 유지.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f858338542cd"
down_revision: str | Sequence[str] | None = "7efa9d16ece4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("pr_number", sa.Integer(), nullable=True))
    op.add_column("incidents", sa.Column("pr_branch", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "pr_branch")
    op.drop_column("incidents", "pr_number")
