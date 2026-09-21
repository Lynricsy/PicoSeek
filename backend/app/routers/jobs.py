"""任务状态查询、取消与 SSE 进度流。

进度用 SSE 而不是轮询：一条问答任务会产生几十条阶段/逐篇事件，轮询既慢又
浪费。事件存在 Redis Stream 里，`id:` 就是 entry id，客户端重连带
`Last-Event-ID` 即可续传；流过期（7 天）后只能从 DB 合成一条终态事件。
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from ..auth import Principal, require, session_principal
from ..deps import get_db, get_redis
from ..errors import ApiError
from ..models import Job as JobRow
from ..schemas.common import Page
from ..schemas.events import EVENT_MODELS, StreamId
from ..schemas.jobs import TERMINAL_JOB_STATUSES, Job, JobKind, JobStatus
from ..services import events
from ..services import jobs as jobs_service

router = APIRouter(tags=["jobs"])

HEARTBEAT_S = 15


def _visible(db: Session, job_id: str, principal: Principal) -> JobRow:
    """别人的任务一律当不存在：404 比 403 更少泄露信息（连是否存在都不透露）。"""
    row = db.get(JobRow, job_id)
    if row is None or not (principal.is_admin or row.user_id == principal.user_id):
        raise ApiError(404, "not_found", f"job {job_id!r} not found")
    return row


@router.get("/jobs", response_model=Page[Job], summary="任务列表")
def list_jobs(kind: JobKind | None = None, status: JobStatus | None = None,
              limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
              db: Session = Depends(get_db),
              principal: Principal = Depends(require())) -> Page[Job]:
    conds = []
    if not principal.is_admin:
        conds.append(JobRow.user_id == principal.user_id)
    if kind:
        conds.append(JobRow.kind == kind)
    if status:
        conds.append(JobRow.status == status)
    total = int(db.scalar(select(func.count(JobRow.id)).where(*conds)) or 0)
    rows = db.scalars(select(JobRow).where(*conds)
                      .order_by(JobRow.created_at.desc(), JobRow.id.desc())
                      .limit(limit).offset(offset)).all()
    return Page[Job](items=[Job.model_validate(r) for r in rows], total=total,
                     limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=Job, summary="任务详情")
def get_job(job_id: str, db: Session = Depends(get_db),
            principal: Principal = Depends(require())) -> Job:
    return Job.model_validate(_visible(db, job_id, principal))


@router.post("/jobs/{job_id}/cancel", status_code=202, summary="请求取消任务",
             response_class=Response,
             responses={202: {"headers": {"Location": {"schema": {"type": "string"},
                                                       "description": "任务状态地址"}}}})
async def cancel_job(job_id: str, db: Session = Depends(get_db), redis=Depends(get_redis),  # noqa: ANN001
                     principal: Principal = Depends(require())) -> Response:
    job = _visible(db, job_id, principal)
    await jobs_service.cancel(job, redis, principal)
    return Response(status_code=202, headers={"Location": f"/v1/jobs/{job_id}"})


def _terminal_payload(job: JobRow) -> dict:
    if job.status == "succeeded":
        if job.kind in ("ask", "interpret"):
            # interpret 的 result 还带 thread_id / kb_job_id，成功事件只暴露 answer_id
            return {"answer_id": (job.result or {}).get("answer_id")}
        if job.kind == "answer_kb":
            result = job.result or {}
            return {"items": result.get("items", 0), "papers": result.get("paper_count", 0)}
        return job.result or {}
    if job.status == "failed":
        return job.error or {"code": "internal_error", "message": "job failed"}
    return {}


@router.get("/jobs/{job_id}/events", summary="任务进度流（SSE）",
            response_class=EventSourceResponse,
            responses={200: {"content": {"text/event-stream": {
                "schema": {"type": "string", "description": "SSE 帧；data 为 JSON，模型见 x-sse-events。"},
                "x-sse-events": {
                    name: {"$ref": f"#/components/schemas/{model.__name__}"}
                    for name, model in EVENT_MODELS.items()
                },
            }}}})
async def job_events(job_id: str, request: Request,
                     last_event_id: Annotated[StreamId, Header(
                         alias="Last-Event-ID",
                         description="上次已处理的 Stream ID：两个无前导零的无符号 64 位整数，以连字符分隔。",
                     )] = "0-0",
                     db: Session = Depends(get_db),
                     redis=Depends(get_redis),  # noqa: ANN001
                     principal: Principal = Depends(require())):
    # 依赖的 session 会活到 SSE 结束，必须先归还连接；轮询另用短事务。
    bind = db.get_bind()
    await run_in_threadpool(_visible, db, job_id, principal)
    await run_in_threadpool(db.close)

    def terminal_snapshot():
        with Session(bind=bind) as snapshot:
            current = session_principal(snapshot, principal.session_hash)
            job = _visible(snapshot, job_id, current)
            if job.status in TERMINAL_JOB_STATUSES:
                return job.status, _terminal_payload(job)
        return None

    async def terminal():
        return await run_in_threadpool(terminal_snapshot)

    async def stream():
        try:
            async for entry_id, kind, data in events.subscribe(redis, job_id, last_event_id, terminal=terminal):
                if await request.is_disconnected():
                    return
                # Redis 回放也可能持续有数据；每次发送前检查撤销和当前权限。
                await terminal()
                yield {"id": entry_id, "event": kind, "data": json.dumps(data, ensure_ascii=False)}
        except ApiError as exc:
            if exc.status not in (401, 403, 404):
                raise

    return EventSourceResponse(stream(), ping=HEARTBEAT_S)
