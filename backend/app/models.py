"""ORM 模型：用户、会话、jobs（执行）与 answers（结果）。

两张表分开是因为生命周期不同：job 是一次执行（可重试、可取消、7 天后事件流
过期），answer 是长期可浏览的结果。worker 在同一个事务里同时更新两者，状态映射
queued→queued / running→running / succeeded→ready / failed→failed / cancelled→cancelled。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="CASCADE"))
    auth_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_user_sessions_user", "user_id"),
        Index("ix_user_sessions_expires", "expires_at"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.id"), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_user_status", "user_id", "status"),
    )


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("jobs.id"), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.id"), nullable=True)
    # 追问链：parent_id 指向上一轮，thread_id 是 codex 会话 id 或 interpret 的 docset id（ask 引擎恒为空）
    parent_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("answers.id", ondelete="SET NULL"), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    question: Mapped[str] = mapped_column(Text)
    question_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    queries: Mapped[list] = mapped_column(JSON, default=list)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    filters_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    n_papers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_fulltext: Mapped[int | None] = mapped_column(Integer, nullable=True)
    papers: Mapped[list] = mapped_column(JSON, default=list)
    body_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    kb_hits: Mapped[list] = mapped_column(JSON, default=list)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_answers_status", "status"),
        Index("ix_answers_created", "created_at"),
        Index("ix_answers_user_created", "user_id", "created_at"),
        Index("ix_answers_thread", "thread_id"),
    )

    @property
    def engine(self) -> str:
        """引擎不单独建列：它就是创建入参的一部分，历史行（options 为空）一律是 ask。"""
        return (self.options or {}).get("engine", "ask")


# job.status -> answer.status
JOB_TO_ANSWER_STATUS = {
    "queued": "queued",
    "running": "running",
    "succeeded": "ready",
    "failed": "failed",
    "cancelled": "cancelled",
}
