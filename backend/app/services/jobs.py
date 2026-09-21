"""任务入队与取消。

先写 DB 再入队：DB 行是唯一事实来源，入队失败可以把行标成 failed 并如实
报 502；反过来（先入队）会出现「worker 已开跑但表里没有这一行」。
取消不用 arq 的 abort：流水线跑在线程里，无法被 asyncio 取消，只能靠
协作式的取消标记（worker 在阶段边界轮询）。
"""
from __future__ import annotations

import uuid
import logging

from sqlalchemy import update
from sqlalchemy.orm import Session, object_session

from ..config import settings
from ..errors import ApiError
from ..models import Answer, Job, utcnow
from ..schemas.jobs import TERMINAL_JOB_STATUSES
from . import events


def new_id() -> str:
    return uuid.uuid4().hex


async def enqueue(arq, db: Session, *, kind: str, params: dict, user_id: str | None,
                  fn_name: str, job_id: str | None = None, answer: Answer | None = None) -> Job:
    job = Job(id=job_id or new_id(), kind=kind, status="queued", user_id=user_id,
              params=params, progress=None, error=None, result=None)
    db.add(job)
    if answer is not None:
        # 先落父行，再在同一事务中建立关联，worker 只会看到完整记录。
        db.flush()
        answer.job_id = job.id
        db.add(answer)
    db.commit()
    try:
        await arq.enqueue_job(fn_name, job.id, _job_id=job.id)
    except Exception as e:  # noqa: BLE001
        job.status = "failed"
        job.error = {"code": "internal_error", "message": f"enqueue failed: {e}"}
        job.finished_at = utcnow()
        if answer is not None:
            answer.status = "failed"
            answer.error = job.error
            answer.finished_at = job.finished_at
        db.commit()
        raise ApiError(502, "upstream_unavailable", f"redis unavailable: {e}") from e
    return job


def active_job_count(db: Session, user_id: str | None) -> int:
    from sqlalchemy import func, select

    stmt = (select(func.count(Job.id))
            .where(Job.user_id == user_id, Job.kind != "answer_kb",
                   Job.status.in_(("queued", "running"))))
    return int(db.scalar(stmt) or 0)


def ensure_capacity(db: Session, principal, *, n: int = 1) -> None:  # noqa: ANN001
    """并发任务闸门：所有创建任务的入口共用这一处限流。

    `n` 是本次要一次性入队的任务数（文献解读的「综述 + 首问」会占两个名额）：
    必须整批判定，否则第二个任务会在第一个占满配额后被拒，留下半条会话。
    """
    active = active_job_count(db, principal.user_id)
    if active + n > settings.max_active_jobs_per_user:
        raise ApiError(429, "too_many_jobs",
                       f"{active} active job(s) for this user; limit is {settings.max_active_jobs_per_user}")


def may_touch(job: Job, principal) -> bool:  # noqa: ANN001
    return principal.is_admin or job.user_id == principal.user_id


async def cancel(job: Job, redis, principal) -> None:  # noqa: ANN001
    """请求取消；终态任务返回 409（幂等地重复取消没有意义，也会掩盖客户端 bug）。"""
    if not may_touch(job, principal):
        raise ApiError(404, "not_found", f"job {job.id!r} not found")
    if job.status in TERMINAL_JOB_STATUSES:
        raise ApiError(409, "conflict", f"job {job.id} already {job.status}")
    if job.kind == "answer_kb":
        # 后台可能长时间等前台，取消必须持久化，不能仅依赖有 TTL 的 Redis 标记。
        db = object_session(job)
        if db is None:
            raise RuntimeError("background cancellation requires an attached job")
        changed = db.execute(update(Job).where(
            Job.id == job.id, Job.status.in_(("queued", "running")))
            .values(status="cancelled", finished_at=utcnow(), error=None)
            .execution_options(synchronize_session=False)).rowcount
        db.commit()
        if changed != 1:
            raise ApiError(409, "conflict", f"job {job.id} already finished")
        try:
            await events.request_cancel(redis, job.id, settings.events_ttl_s)
        except Exception:
            logging.getLogger(__name__).exception("cancel signal unavailable for %s", job.id)
        return
    await events.request_cancel(redis, job.id, settings.events_ttl_s)
