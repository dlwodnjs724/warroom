"""add rejection_reason to incidents

Revision ID: 6d3aecc468a3
Revises: f858338542cd
Create Date: 2026-05-18 23:08:13.768998

Phase 3.5 — Slack reject modal 에서 캡쳐한 반려 사유를 영속화. 추후
재분석 시 컨텍스트로 주입하기 위해 incident 단위로 보관 (report 가 아니라
incident 컬럼인 이유: report 가 NULL 인 케이스 — 분석 전 인 반려 등 —
에도 사유는 남길 수 있어야 함).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6d3aecc468a3"
down_revision: str | Sequence[str] | None = "f858338542cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("rejection_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "rejection_reason")
