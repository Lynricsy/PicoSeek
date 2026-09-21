"""任务（一次流水线执行）的读模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .common import JobError, UtcDateTime

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
JobKind = Literal["ask", "codex", "interpret", "kb_reindex", "paper_ingest", "answer_kb"]
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class JobProgress(BaseModel):
    stage: str
    current: int | None = None
    total: int | None = None


class Job(BaseModel):
    id: str
    kind: JobKind
    status: JobStatus
    user_id: str | None = None
    params: dict = {}
    progress: JobProgress | None = None
    error: JobError | None = None
    result: dict | None = None
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None

    model_config = {"from_attributes": True}
