"""SQLAlchemy 2.0 모델.

스키마는 운영 대상이 MySQL 임을 가정해 VARCHAR 길이를 명시한다.
변동 길이 본문(triage_summary, patch 등)은 Text 로 둔다.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"

    incident_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    dupe_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Slack 스레드 영속화 — chat.update / thread reply 시 재사용
    slack_channel_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    slack_ts: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # GitHub PR 영속화 — 거절 시 close + branch 삭제 위해 필요 (Phase 4.5)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)

    report: Mapped["Report | None"] = relationship(
        back_populates="incident",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Report(Base):
    __tablename__ = "reports"

    incident_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("incidents.incident_id", ondelete="CASCADE"),
        primary_key=True,
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="code")
    triage_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    patch_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_mortem_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    incident: Mapped[Incident] = relationship(back_populates="report")
