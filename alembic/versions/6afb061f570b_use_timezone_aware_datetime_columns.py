"""use timezone-aware datetime columns

Revision ID: 6afb061f570b
Revises: 0cd48a4df822
Create Date: 2026-05-13 11:29:56.077736

SQLite 는 column type 구분이 없어 no-op. MySQL/Postgres 는 DATETIME → TIMESTAMP
(또는 TIMESTAMPTZ) 로 ALTER, UTC 보존을 ORM/DB 모두에서 보장.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6afb061f570b"
down_revision: str | Sequence[str] | None = "0cd48a4df822"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table — SQLite 는 ALTER COLUMN 미지원이라 table recreate 로 우회.
    # MySQL/Postgres 는 in-place ALTER 로 실행.
    with op.batch_alter_table("reports") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("reports") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            existing_nullable=False,
        )
