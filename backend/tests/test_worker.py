"""worker 状态机：成功 / 业务失败 / 排队期间被取消，以及事件流的终态。"""
from __future__ import annotations

import asyncio
import json

import pytest

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
import ask
import ingest
import knowledge_store as ks
from app.db import SessionLocal
from app.models import Answer, Job
from app.services import events
from app.services import codex as codex_engine
import interpret
from app.worker import (
    run_ask_job,
    run_codex_job,
    run_interpret_job,
    run_kb_reindex_job,
    run_paper_ingest_job,
)

from .conftest import make_job
from .test_events import finish_sse, live_sse, streaming_app


def _result(answer_id: str, papers_dir: str) -> ask.AskResult:
    paper = {"n": 1, "pmid": "39133485", "doi": "10.1/x", "pmcid": "PMC1", "title": "T", "year": "2024",
             "journal": "JAMA Netw Open", "issn": "2574-3805", "authors": "Chuang MH", "quartile": "Q1",
             "rank": None, "source": "pmc", "relevance": 2,
             "paras": [{"id": 1, "sec": "Abstract", "page": None, "text": "x"}],
             "fulltext_md": "x",
             "cites": [{"pid": 1, "verified": True}, {"pid": 2, "verified": False}]}
    return ask.AskResult(run_id=answer_id, question="测试问题", question_en="test question",
                         queries=["q1"], filters_label="无", papers=[paper], used_n=[1],
                         body_md="结论 [1¶1]", answer_md="# Q\n\n结论 [1¶1](x.md#p1)",
                         citations=[{"n": 1, "pmid": "39133485", "pid": 1, "sec": "Abstract", "page": None,
                                     "text": "x", "quotes": ["x"], "from_marker": True}],
                         kb_hits=[], out_path="/tmp/x.md", papers_dir=papers_dir, n_fulltext=1)


def _stream(sync_redis, job_id: str) -> list[tuple[str, dict]]:
    entries = sync_redis.xrange(events.stream_key(job_id))
    out = []
    for _id, fields in entries:
        kind = fields[b"type"].decode()
        out.append((kind, json.loads(fields[b"data"].decode())))
    return out


async def test_successful_job_persists_answer_and_emits_succeeded(worker_ctx, sync_redis, monkeypatch,
                                                                tmp_path):
    job_id, answer_id = make_job()
    monkeypatch.setattr(ask, "run_ask", lambda opts, **kw: _result(answer_id, str(tmp_path)))

    await run_ask_job(worker_ctx, job_id)

    with SessionLocal() as db:
        job, answer = db.get(Job, job_id), db.get(Answer, answer_id)
        assert job.status == "succeeded"
        assert job.result == {"answer_id": answer_id}
        assert job.finished_at is not None
        assert answer.status == "ready"
        assert answer.n_papers == 1 and answer.n_fulltext == 1
        assert answer.papers[0]["n"] == 1
        assert answer.papers[0]["rank_label"] == "分区未知"
        assert answer.papers[0]["n_citations"] == 2
        assert answer.papers[0]["n_citations_verified"] == 1
        assert answer.citations[0]["pid"] == 1
        assert answer.body_md == "结论 [1¶1]"

    kind, data = _stream(sync_redis, job_id)[-1]
    assert kind == "succeeded"
    assert data == {"answer_id": answer_id}


async def test_codex_job_persists_answer_trace_and_thread_id(worker_ctx, sync_redis, monkeypatch):
    """codex 引擎没有结构化 papers，答案、轨迹与会话 id 是它唯一的产出，必须落库。"""
    job_id, answer_id = make_job(kind="codex")
    prompts: list[str] = []
    call = {"call_id": "c1", "server": "semantic_scholar", "tool": "search_papers",
            "status": "completed", "args": {"query": "sglt2"}, "duration_ms": 120, "error": None}

    def fake_run(prompt, **kw):
        prompts.append(prompt)
        kw["emit"]({"type": "tool", **call})
        return codex_engine.CodexResult(thread_id="thread-1", answer_md="# 结论\n\n证据 [1]",
                                        trace=[call])

    monkeypatch.setattr(codex_engine, "run_codex", fake_run)

    await run_codex_job(worker_ctx, job_id)

    assert prompts[0].startswith("测试问题")
    with SessionLocal() as db:
        job, answer = db.get(Job, job_id), db.get(Answer, answer_id)
        assert job.status == "succeeded"
        assert job.result == {"answer_id": answer_id, "thread_id": "thread-1"}
        assert answer.status == "ready"
        assert answer.answer_md == "# 结论\n\n证据 [1]"
        assert answer.trace == [call]
        assert answer.thread_id == "thread-1"
        assert answer.filters_label == "无"      # 与 ask 同源的筛选描述，不再是工具计数
        assert answer.body_md is None      # 没有分节结构，前端整篇渲染

    kinds = [kind for kind, _ in _stream(sync_redis, job_id)]
    assert kinds[-1] == "succeeded" and "tool" in kinds


async def test_codex_followup_resumes_thread_without_repeating_filter_rules(worker_ctx, sync_redis,
                                                                            monkeypatch):
    """追问续接同一 thread：会话里已有检索要求，重复附加只会稀释新问题。"""
    job_id, answer_id = make_job(kind="codex")
    seen: dict = {}

    def fake_run(prompt, **kw):
        seen["prompt"], seen["thread_id"] = prompt, kw.get("thread_id")
        return codex_engine.CodexResult(thread_id="thread-1", answer_md="# 结论")

    monkeypatch.setattr(codex_engine, "run_codex", fake_run)
    with SessionLocal() as db:
        db.get(Answer, answer_id).thread_id = "thread-1"
        db.commit()

    await run_codex_job(worker_ctx, job_id)

    assert seen["thread_id"] == "thread-1"
    assert seen["prompt"] == "测试问题"
    assert "检索要求" not in seen["prompt"]


async def test_codex_failure_keeps_its_error_code(worker_ctx, sync_redis, monkeypatch):
    job_id, answer_id = make_job(kind="codex")

    def boom(prompt, **kw):
        raise codex_engine.CodexFailed("mcp startup timeout")

    monkeypatch.setattr(codex_engine, "run_codex", boom)

    await run_codex_job(worker_ctx, job_id)

    with SessionLocal() as db:
        assert db.get(Answer, answer_id).error["code"] == "codex_failed"
    kind, data = _stream(sync_redis, job_id)[-1]
    assert kind == "failed" and data["code"] == "codex_failed"


async def test_pipeline_error_marks_job_failed_with_code(worker_ctx, sync_redis, monkeypatch):
    job_id, answer_id = make_job()

    def boom(opts, **kw):
        raise ask.NoPapers("no papers pass the filters")

    monkeypatch.setattr(ask, "run_ask", boom)
    await run_ask_job(worker_ctx, job_id)

    with SessionLocal() as db:
        job, answer = db.get(Job, job_id), db.get(Answer, answer_id)
        assert job.status == "failed"
        assert job.error["code"] == "no_papers"
        assert answer.status == "failed"
        assert answer.error["code"] == "no_papers"

    kind, data = _stream(sync_redis, job_id)[-1]
    assert kind == "failed"
    assert data["code"] == "no_papers"


async def test_cancel_requested_before_start_skips_pipeline(worker_ctx, sync_redis, monkeypatch):
    job_id, answer_id = make_job()
    sync_redis.set(events.cancel_key(job_id), "1")
    called = False

    def never(opts, **kw):
        nonlocal called
        called = True
        raise AssertionError("pipeline must not start for a cancelled job")

    monkeypatch.setattr(ask, "run_ask", never)
    await run_ask_job(worker_ctx, job_id)

    assert called is False
    with SessionLocal() as db:
        assert db.get(Job, job_id).status == "cancelled"
        assert db.get(Answer, answer_id).status == "cancelled"
    assert _stream(sync_redis, job_id)[-1][0] == "cancelled"


async def test_progress_events_land_in_job_row(worker_ctx, sync_redis, monkeypatch, tmp_path):
    job_id, answer_id = make_job()

    def with_progress(opts, *, run_id, emit, should_cancel, paper_urls, defer_kb):
        emit({"type": "progress", "stage": "read", "current": 2, "total": 3, "pmid": "1", "title": "t"})
        return _result(run_id, str(tmp_path))

    monkeypatch.setattr(ask, "run_ask", with_progress)
    await run_ask_job(worker_ctx, job_id)

    kinds = [k for k, _ in _stream(sync_redis, job_id)]
    assert kinds == ["progress", "succeeded"]


async def test_reindex_job_succeeds_with_counts(worker_ctx, sync_redis, monkeypatch):
    job_id, _ = make_job(kind="kb_reindex", answer=False)
    monkeypatch.setattr(ks, "reindex", lambda emit, should_cancel: (12, 3))

    await run_kb_reindex_job(worker_ctx, job_id)

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == "succeeded"
        assert job.result == {"items": 12, "papers": 3}
    assert _stream(sync_redis, job_id)[-1] == ("succeeded", {"items": 12, "papers": 3})


async def test_reindex_job_cancelled_midway_is_terminal(worker_ctx, sync_redis, monkeypatch):
    """异步取消被接受后，worker 必须将协作式中断收敛为 cancelled。"""
    job_id, _ = make_job(kind="kb_reindex", answer=False)

    def cancelled(emit, should_cancel):
        raise ks.ReindexCancelled("cancelled after 1/3 papers")

    monkeypatch.setattr(ks, "reindex", cancelled)
    await run_kb_reindex_job(worker_ctx, job_id)

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == "cancelled"
        assert job.finished_at is not None
    assert _stream(sync_redis, job_id)[-1][0] == "cancelled"


def _ingest_job() -> str:
    job_id, _ = make_job(kind="paper_ingest", answer=False, params={
        "source": "upload", "pdf_path": "/tmp/x.pdf", "doi": "",
        "meta": {"title": "T", "types": []}})
    return job_id


async def test_ingest_job_succeeds_with_key(worker_ctx, sync_redis, monkeypatch):
    job_id = _ingest_job()
    monkeypatch.setattr(ingest, "run_ingest",
                        lambda src, **kw: ingest.IngestResult("T", 3, 2, 5))

    await run_paper_ingest_job(worker_ctx, job_id)

    expected = {"key": "T", "n_paragraphs": 3, "n_facts": 2, "items": 5}
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == "succeeded"
        assert job.result == expected
    assert _stream(sync_redis, job_id)[-1] == ("succeeded", expected)


async def test_ingest_job_maps_pipeline_error(worker_ctx, sync_redis, monkeypatch):
    job_id = _ingest_job()

    def boom(src, **kw):
        raise ingest.PdfUnreadable("scan")

    monkeypatch.setattr(ingest, "run_ingest", boom)

    await run_paper_ingest_job(worker_ctx, job_id)

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert job.error["code"] == "pdf_unreadable"
    kind, data = _stream(sync_redis, job_id)[-1]
    assert kind == "failed"
    assert data["code"] == "pdf_unreadable"


async def test_ingest_job_passes_params_through_to_pipeline(worker_ctx, monkeypatch):
    job_id, _ = make_job(kind="paper_ingest", answer=False, params={
        "source": "doi", "pdf_path": "/tmp/j.pdf", "doi": "10.1/x",
        "meta": {"title": "From DOI", "journal": "Lancet", "types": ["Journal Article"]}})
    seen: list[ingest.IngestSource] = []

    def capture(src, **kw):
        seen.append(src)
        return ingest.IngestResult("From_DOI", 1, 0, 0)

    monkeypatch.setattr(ingest, "run_ingest", capture)

    await run_paper_ingest_job(worker_ctx, job_id)

    assert seen[0].kind == "doi"
    assert seen[0].pdf_path == "/tmp/j.pdf"
    assert seen[0].doi == "10.1/x"
    assert seen[0].meta["journal"] == "Lancet"


async def test_reindex_job_timeout_does_not_stay_running(worker_ctx, sync_redis, monkeypatch):
    """CancelledError 是 BaseException：不显式处理会让任务永远停在 running。"""
    job_id, _ = make_job(kind="kb_reindex", answer=False)

    def timed_out(emit, should_cancel):
        raise asyncio.CancelledError()

    monkeypatch.setattr(ks, "reindex", timed_out)
    with pytest.raises(asyncio.CancelledError):
        await run_kb_reindex_job(worker_ctx, job_id)

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert job.error["code"] == "timeout"
    assert _stream(sync_redis, job_id)[-1][0] == "failed"
    assert sync_redis.exists(events.cancel_key(job_id)), "超时后要置取消标记，让还在跑的线程自己退出"


@pytest.mark.parametrize("options, expected", [
    ({"question": "q", "year_from": 2020, "year_to": 2024}, "2020-2024"),
    ({"question": "q", "year_from": 2021}, "2021-2021"),
    ({"question": "q", "years": 3}, ""),
])
def test_year_options_map_to_cli_range(options, expected):
    from app.services.answers import to_ask_options

    assert to_ask_options(options).year == expected


def test_quartiles_and_journals_map_to_cli_strings():
    from app.services.answers import to_ask_options

    opts = to_ask_options({"question": "q", "quartiles": [2, 1, 1], "journals": ["Lancet", "JAMA"]})
    assert opts.quartile == "Q1,Q2"
    assert opts.journal == "Lancet,JAMA"


@pytest.mark.parametrize("status", ["succeeded", "failed"])
async def test_terminal_xadd_failure_converges_after_stage_replay(
        worker_ctx, sync_redis, monkeypatch, status, tmp_path):
    job_id, answer_id = make_job(user_id="reader")
    with SessionLocal() as db:
        answer = db.get(Answer, answer_id)
        answer.options = {**answer.options, "use_kb": True}
        db.commit()

    def run(opts, *, emit, **kwargs):
        emit({"type": "stage", "stage": "search", "status": "started"})
        if status == "failed":
            raise ask.NoPapers("nothing found")
        return _result(answer_id, str(tmp_path))

    xadd = sync_redis.xadd

    def fail_terminal(name, fields, *args, **kwargs):
        if fields.get("type") in events.TERMINAL:
            raise RedisConnectionError("terminal XADD unavailable")
        return xadd(name, fields, *args, **kwargs)

    monkeypatch.setattr(ask, "run_ask", run)
    monkeypatch.setattr(sync_redis, "xadd", fail_terminal)
    with pytest.raises(RedisConnectionError, match="terminal XADD"):
        await run_ask_job(worker_ctx, job_id)
    if status == "succeeded":
        with SessionLocal() as db:
            assert db.get(Job, job_id).result["kb_job_id"]

    # 保留真实 stage 流而不是删流；终态已经由真实 worker 事务提交。
    redis = aioredis.from_url(settings.redis_url)
    try:
        async with live_sse(streaming_app(redis), f"/v1/jobs/{job_id}/events") as output:
            got = await finish_sse(output)
        assert [e["event"] for e in got] == ["stage", status]
        assert got[0]["data"]["stage"] == "search"
        if status == "succeeded":
            assert got[1]["data"] == {"answer_id": answer_id}
        else:
            assert got[1]["data"] == {"code": "no_papers", "message": "nothing found"}
    finally:
        await redis.aclose()
        await worker_ctx["redis_async"].aclose()


def _interpret_result(papers_dir: str) -> interpret.InterpretResult:
    paper = {"n": 1, "pmid": "", "doi": "10.1/x", "pmcid": "", "title": "上传文献", "year": "",
             "journal": "", "issn": "", "authors": "", "quartile": "", "rank": None,
             "source": "upload", "relevance": None, "filename": "a.pdf", "md_file": "1.md",
             "paras": [{"id": 1, "sec": "p.1", "page": 1, "text": "x"}],
             "fulltext_md": "# 上传文献", "text": "[¶1] x", "cites": []}
    return interpret.InterpretResult(
        papers=[paper], used_n=[1], body_md="结论 [1¶1]",
        answer_md="# Q\n\n结论 [1¶1](/v1/answers/x/papers/1/markdown#p1)",
        citations=[{"n": 1, "pmid": "", "pid": 1, "sec": "p.1", "page": 1, "text": "x",
                    "quotes": [], "from_marker": True}],
        papers_dir=papers_dir)


def _interpret_job(*, mode: str = "overview", use_kb: bool = True) -> tuple[str, str]:
    job_id, answer_id = make_job(kind="interpret",
                                 params={"question": "梳理这组文献的组织关系与差异"})
    with SessionLocal() as db:
        row = db.get(Answer, answer_id)
        row.thread_id = "docset-1"
        row.options = {"engine": "interpret", "mode": mode, "n_files": 1, "use_kb": use_kb,
                       "question": "梳理这组文献的组织关系与差异"}
        db.commit()
    return job_id, answer_id


async def test_interpret_job_persists_answer_and_queues_kb(worker_ctx, sync_redis, monkeypatch,
                                                           tmp_path):
    job_id, answer_id = _interpret_job()
    seen: dict = {}

    def fake_run(opts, **kw):
        seen["opts"], seen["urls"] = opts, kw["paper_urls"]
        return _interpret_result(str(tmp_path))

    monkeypatch.setattr(interpret, "run_interpret", fake_run)
    monkeypatch.setattr(interpret, "save_kb_snapshot", lambda res: str(tmp_path / "kb-input.json"))

    await run_interpret_job(worker_ctx, job_id)

    assert seen["opts"].mode == "overview"
    assert seen["opts"].docset_dir.endswith("/docsets/docset-1")
    assert seen["urls"] == {1: f"/v1/answers/{answer_id}/papers/1/markdown"}
    with SessionLocal() as db:
        job, answer = db.get(Job, job_id), db.get(Answer, answer_id)
        assert job.status == "succeeded"
        assert job.result["answer_id"] == answer_id
        assert job.result["thread_id"] == "docset-1"
        kb = db.get(Job, job.result["kb_job_id"])
        assert kb.kind == "answer_kb" and kb.params["snapshot_path"].endswith("kb-input.json")
        assert answer.status == "ready"
        assert answer.n_papers == 1 and answer.n_fulltext == 1
        assert answer.papers[0]["source"] == "upload"
        assert answer.papers[0]["relevance"] is None
        assert answer.body_md == "结论 [1¶1]"
        assert answer.citations[0]["pid"] == 1

    kind, data = _stream(sync_redis, job_id)[-1]
    assert kind == "succeeded" and data == {"answer_id": answer_id}


async def test_interpret_followup_carries_history_and_skips_kb(worker_ctx, monkeypatch, tmp_path):
    """追问要看得到上一轮的问答，且不再重复写库。"""
    job_id, answer_id = _interpret_job(mode="qa", use_kb=False)
    with SessionLocal() as db:
        db.add(Answer(id="prev", user_id="writer", status="ready", thread_id="docset-1",
                      question="上一问", body_md="上一答", queries=[], options={}, papers=[],
                      citations=[], kb_hits=[]))
        db.commit()
    seen: dict = {}

    def fake_run(opts, **kw):
        seen["history"] = opts.history
        return _interpret_result(str(tmp_path))

    monkeypatch.setattr(interpret, "run_interpret", fake_run)
    monkeypatch.setattr(interpret, "save_kb_snapshot",
                        lambda res: pytest.fail("追问不该再写一次知识库"))

    await run_interpret_job(worker_ctx, job_id)

    assert seen["history"] == (("上一问", "上一答"),)
    with SessionLocal() as db:
        assert "kb_job_id" not in db.get(Job, job_id).result


async def test_interpret_job_maps_pdf_unreadable(worker_ctx, sync_redis, monkeypatch):
    job_id, answer_id = _interpret_job()

    def boom(opts, **kw):
        raise ingest.PdfUnreadable("a.pdf: only 12 chars extracted")

    monkeypatch.setattr(interpret, "run_interpret", boom)

    await run_interpret_job(worker_ctx, job_id)

    with SessionLocal() as db:
        assert db.get(Answer, answer_id).error["code"] == "pdf_unreadable"
    kind, data = _stream(sync_redis, job_id)[-1]
    assert kind == "failed" and data["code"] == "pdf_unreadable"
    assert "a.pdf" in data["message"]
