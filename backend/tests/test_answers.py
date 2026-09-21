"""answers 端点契约：入参校验、202 语义、并发上限、未就绪、删除/取消、legacy 导入。"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Answer, Job
from app.services import events
from app.services.answers import import_legacy_answers

from .conftest import ADMIN_TOKEN, USER_TOKEN, OTHER_TOKEN, PASSWORD, auth

VALID = {"question": "SGLT2 抑制剂对 HFpEF 有什么获益？", "papers": 2, "years": 3, "use_kb": False}


def _create(client, **overrides):
    return client.post("/v1/answers", json={**VALID, **overrides}, headers=auth(OTHER_TOKEN))


def test_create_returns_202_with_location_and_enqueues(client, arq):
    r = _create(client)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["question"] == VALID["question"]
    assert body["job_id"]
    assert r.headers["location"] == f"/v1/answers/{body['id']}"
    assert body["created_at"].endswith("Z")

    with SessionLocal() as db:
        job = db.get(Job, body["job_id"])
        answer = db.get(Answer, body["id"])
        assert job.status == "queued" and job.kind == "ask"
        assert job.params["answer_id"] == body["id"]
        assert answer.status == "queued" and answer.user_id == "writer"


def test_codex_engine_routes_to_its_own_job(client, arq):
    """engine 决定 worker 入口：走错入口会让 codex 提问被当成 ask 流水线跑。"""
    body = _create(client, engine="codex").json()
    assert body["engine"] == "codex"
    assert [call[0] for call in arq.calls] == ["run_codex_job"]
    with SessionLocal() as db:
        assert db.get(Job, body["job_id"]).kind == "codex"


def test_default_engine_is_ask(client, arq):
    body = _create(client).json()
    assert body["engine"] == "ask"
    assert [call[0] for call in arq.calls] == ["run_ask_job"]


def _ready_codex(client, *, question=VALID["question"], thread_id="t1"):
    """建一轮已完成的 codex 答案：追问的前提是 thread 里有 ready 回合。"""
    body = _create(client, engine="codex", question=question).json()
    with SessionLocal() as db:
        answer = db.get(Answer, body["id"])
        answer.status, answer.thread_id, answer.answer_md = "ready", thread_id, "# 结论"
        db.get(Job, body["job_id"]).status = "succeeded"
        db.commit()
    return body["id"]


def test_followup_starts_next_turn_on_the_same_thread(client, arq):
    first = _ready_codex(client)

    r = client.post(f"/v1/answers/{first}/followup", json={"question": "再总结一句"},
                    headers=auth(OTHER_TOKEN))

    assert r.status_code == 202
    body = r.json()
    assert r.headers["location"] == f"/v1/answers/{body['id']}"
    assert body["parent_id"] == first
    assert body["question"] == "再总结一句"
    assert body["engine"] == "codex"
    with SessionLocal() as db:
        row = db.get(Answer, body["id"])
        assert row.thread_id == "t1"
        job = db.get(Job, body["job_id"])
        assert job.kind == "codex"
        assert job.params["question"] == "再总结一句"
        assert job.params["engine"] == "codex"
        assert job.params["papers"] == VALID["papers"]   # 其余选项沿用上一轮
    assert arq.calls[-1][0] == "run_codex_job"


def test_followup_on_ask_answer_is_conflict(client, arq):
    body = _create(client).json()
    with SessionLocal() as db:
        db.get(Answer, body["id"]).status = "ready"
        db.get(Job, body["job_id"]).status = "succeeded"
        db.commit()

    r = client.post(f"/v1/answers/{body['id']}/followup", json={"question": "再说说"},
                    headers=auth(OTHER_TOKEN))

    assert r.status_code == 409
    assert r.json()["code"] == "conflict"


def test_followup_needs_a_finished_turn(client, arq):
    """thread 还在跑就追问，会在同一个会话上开两轮；必须挡住。"""
    body = _create(client, engine="codex").json()
    with SessionLocal() as db:
        db.get(Answer, body["id"]).thread_id = "t1"
        db.commit()

    r = client.post(f"/v1/answers/{body['id']}/followup", json={"question": "再说说"},
                    headers=auth(OTHER_TOKEN))

    assert r.status_code == 409
    assert r.json()["code"] == "thread_busy"


def test_followup_is_hidden_from_others_and_refused_for_admin(client, arq):
    first = _ready_codex(client)

    hidden = client.post(f"/v1/answers/{first}/followup", json={"question": "再说说"},
                         headers=auth(USER_TOKEN))
    assert hidden.status_code == 404

    admin = client.post(f"/v1/answers/{first}/followup", json={"question": "再说说"},
                        headers=auth(ADMIN_TOKEN))
    assert admin.status_code == 403
    assert admin.json()["code"] == "forbidden"


def test_thread_and_list_collapse_a_conversation_into_one_row(client, arq):
    first = _ready_codex(client, question="替西帕肽与死亡率")
    second = client.post(f"/v1/answers/{first}/followup", json={"question": "再总结一句"},
                         headers=auth(OTHER_TOKEN)).json()["id"]

    thread = client.get(f"/v1/answers/{first}/thread", headers=auth(OTHER_TOKEN)).json()
    assert [t["id"] for t in thread] == [first, second]

    listing = client.get("/v1/answers", headers=auth(OTHER_TOKEN)).json()
    assert [item["id"] for item in listing["items"]] == [second]
    assert listing["total"] == 1
    assert listing["items"][0]["n_turns"] == 2
    assert listing["items"][0]["root_question"] == "替西帕肽与死亡率"

    # 搜索根问题也要能找到这条会话，尽管列表里显示的是最后一问
    found = client.get("/v1/answers", params={"q": "替西帕肽"}, headers=auth(OTHER_TOKEN)).json()
    assert [item["id"] for item in found["items"]] == [second]


@pytest.mark.parametrize("payload, field", [
    ({"years": 3, "year_from": 2020}, "years"),
    ({"year_to": 2024}, "year_to"),
    ({"year_from": 2024, "year_to": 2020}, "year_to"),
    ({"quartiles": [5]}, "quartiles"),
    ({"question": "   "}, "question"),
    ({"papers": 99}, "papers"),
])
def test_invalid_options_are_rejected(client, payload, field):
    r = _create(client, **payload)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert r.headers["content-type"].startswith("application/problem+json")
    assert body["errors"], "422 必须带上逐字段的 errors"


def test_active_job_limit_is_shared_by_sessions_of_one_user(client):
    login = client.post("/v1/auth/login", json={"username": "writer", "password": PASSWORD})
    assert login.status_code == 200
    second_token = login.json()["access_token"]
    assert _create(client).status_code == 202
    assert client.post("/v1/answers", json=VALID, headers=auth(second_token)).status_code == 202
    response = _create(client)
    assert response.status_code == 429
    assert response.json()["code"] == "too_many_jobs"
    assert client.post("/v1/answers", json=VALID, headers=auth(USER_TOKEN)).status_code == 202


def test_markdown_of_unfinished_answer_is_conflict(client):
    answer_id = _create(client).json()["id"]
    r = client.get(f"/v1/answers/{answer_id}/markdown", headers=auth(OTHER_TOKEN))
    assert r.status_code == 409
    assert r.json()["code"] == "not_ready"


def test_paper_detail_of_answer_without_papers_is_404(client):
    answer_id = _create(client).json()["id"]
    r = client.get(f"/v1/answers/{answer_id}/papers/1", headers=auth(OTHER_TOKEN))
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_unknown_answer_is_404(client):
    r = client.get("/v1/answers/nope", headers=auth(OTHER_TOKEN))
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_delete_active_answer_is_conflict_without_requesting_cancel(client, sync_redis):
    body = _create(client).json()
    r = client.delete(f"/v1/answers/{body['id']}", headers=auth(OTHER_TOKEN))
    assert r.status_code == 409
    assert not sync_redis.exists(events.cancel_key(body["job_id"]))
    # 活动态删除不改变任务状态；调用者必须显式取消关联 job。
    assert client.get(f"/v1/answers/{body['id']}", headers=auth(OTHER_TOKEN)).status_code == 200


def test_delete_terminal_answer_removes_row_and_files(client, data_root: Path):
    body = _create(client).json()
    answer_id = body["id"]
    md = data_root / "answers" / f"{answer_id}.md"
    papers_dir = data_root / "answers" / f"{answer_id}_papers"
    md.write_text("# Q: x", encoding="utf-8")
    papers_dir.mkdir(parents=True, exist_ok=True)
    (papers_dir / "1_notes.md").write_text("notes", encoding="utf-8")
    with SessionLocal() as db:
        row = db.get(Answer, answer_id)
        row.status = "ready"
        job = db.get(Job, row.job_id)
        job.status = "succeeded"
        db.commit()

    assert client.delete(f"/v1/answers/{answer_id}", headers=auth(OTHER_TOKEN)).status_code == 204
    assert client.get(f"/v1/answers/{answer_id}", headers=auth(OTHER_TOKEN)).status_code == 404
    assert not md.exists()
    assert not papers_dir.exists()


def test_delete_other_users_answer_is_hidden_but_admin_may(client):
    answer_id = _create(client).json()["id"]
    with SessionLocal() as db:
        row = db.get(Answer, answer_id)
        row.user_id = "reader"
        row.status = "ready"
        db.commit()
    assert client.delete(f"/v1/answers/{answer_id}", headers=auth(OTHER_TOKEN)).status_code == 404
    assert client.delete(f"/v1/answers/{answer_id}", headers=auth(ADMIN_TOKEN)).status_code == 204


def test_list_filters_by_status_and_question(client):
    first = _create(client, question="替西帕肽与死亡率").json()["id"]
    _create(client, question="SGLT2 与心衰住院")

    r = client.get("/v1/answers", params={"q": "替西帕肽"}, headers=auth(OTHER_TOKEN))
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == first
    assert "body_md" not in page["items"][0], "列表用 summary，不带正文"

    assert client.get("/v1/answers", params={"status": "ready"},
                      headers=auth(OTHER_TOKEN)).json()["total"] == 0


def test_import_legacy_answers_is_idempotent(client, data_root: Path):
    (data_root / "answers" / "20260101_000000.md").write_text("# Q: 测试\n\n正文", encoding="utf-8")
    with SessionLocal() as db:
        assert import_legacy_answers(db) >= 1
        assert import_legacy_answers(db) == 0

    r = client.get("/v1/answers/20260101_000000", headers=auth(ADMIN_TOKEN))
    assert r.status_code == 200
    body = r.json()
    assert body["question"] == "测试"
    assert body["status"] == "ready"
    assert body["created_at"] == "2026-01-01T00:00:00Z"

    md = client.get("/v1/answers/20260101_000000/markdown", headers=auth(ADMIN_TOKEN))
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert "正文" in md.text


def test_answer_paper_detail_reads_run_artifacts(client, data_root: Path):
    """/papers/{n} 要能从 answers/<id>_papers/ 自包含地拼出详情。"""
    answer_id = "run-with-files"
    papers_dir = data_root / "answers" / f"{answer_id}_papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    (papers_dir / "39133485_notes.md").write_text("### Relevance\n2", encoding="utf-8")
    (papers_dir / "39133485.md").write_text('<a id="p1"></a>**¶1** text', encoding="utf-8")
    (papers_dir / "39133485_paragraphs.json").write_text(
        json.dumps([{"id": 1, "sec": "Abstract", "page": None, "text": "text"}]), encoding="utf-8")
    (papers_dir / "39133485_citations.json").write_text(
        json.dumps([{"claimed_pid": 1, "pid": 1, "quote": "text", "score": 1.0, "verified": True}]),
        encoding="utf-8")
    with SessionLocal() as db:
        db.add(Answer(id=answer_id, job_id=None, user_id="writer", status="ready",
                      question="q", queries=[], options={}, papers=[{"n": 1, "pmid": "39133485",
                                                                     "title": "T", "source": "pmc"}],
                      citations=[], kb_hits=[], answer_md="# Q: q",
                      created_at=dt.datetime.now(dt.timezone.utc)))
        db.commit()

    r = client.get(f"/v1/answers/{answer_id}/papers/1", headers=auth(OTHER_TOKEN))
    assert r.status_code == 200
    body = r.json()
    assert body["n"] == 1 and body["pmid"] == "39133485"
    assert body["notes_md"].startswith("### Relevance")
    assert body["paragraphs"][0]["text"] == "text"
    assert body["citations"][0]["verified"] is True
    assert body["facts"] == []
    assert '<a id="p1">' in body["fulltext_md"]

    assert client.get(f"/v1/answers/{answer_id}/papers/2",
                      headers=auth(OTHER_TOKEN)).status_code == 404


def test_enqueue_failure_persists_linked_failures_and_allows_delete(client, arq, monkeypatch):
    async def unavailable(fn_name, job_id, **kwargs):
        # 入队边界从独立连接读取，验证 worker 启动前完整事务已经可见。
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            answer = db.get(Answer, job.params["answer_id"])
            assert answer.job_id == job_id
            assert answer.status == job.status == "queued"
        raise RedisConnectionError("queue unavailable")

    monkeypatch.setattr(arq, "enqueue_job", unavailable)
    response = _create(client)
    assert response.status_code == 502
    assert response.json()["code"] == "upstream_unavailable"
    with SessionLocal() as db:
        answer = db.scalars(select(Answer)).one()
        job = db.get(Job, answer.job_id)
        answer_id, job_id = answer.id, job.id
        assert answer.status == job.status == "failed"
        assert answer.error == job.error
        assert job.error["code"] == "internal_error"
        assert answer.finished_at is not None and job.finished_at is not None
    response = client.get(f"/v1/answers/{answer_id}", headers=auth(OTHER_TOKEN))
    assert response.json()["status"] == "failed"
    assert response.json()["job_id"] == job_id
    assert client.delete(f"/v1/answers/{answer_id}", headers=auth(OTHER_TOKEN)).status_code == 204
    assert client.get(f"/v1/answers/{answer_id}", headers=auth(OTHER_TOKEN)).status_code == 404


def test_worker_completion_during_enqueue_is_not_overwritten(client, arq, monkeypatch):
    async def finish_immediately(fn_name, job_id, **kwargs):
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            answer = db.get(Answer, job.params["answer_id"])
            assert answer.job_id == job_id
            job.status = "succeeded"
            job.result = {"answer_id": answer.id}
            answer.status = "ready"
            db.commit()

    monkeypatch.setattr(arq, "enqueue_job", finish_immediately)
    response = _create(client)
    assert response.status_code == 202
    assert response.json()["status"] == "ready"
    job = client.get(f"/v1/jobs/{response.json()['job_id']}", headers=auth(OTHER_TOKEN))
    assert job.json()["status"] == "succeeded"


@pytest.fixture
def snapshot_root(tmp_path, monkeypatch):
    from app.services import answers as service

    root = tmp_path / "answers"
    root.mkdir()
    monkeypatch.setattr(service, "ANSWERS_DIR", str(root))
    return root


@pytest.mark.parametrize("storage", ["persisted", "legacy", "file-only", "file-url", "http"])
def test_markdown_links_follow_run_snapshot(client, data_root: Path, snapshot_root, storage):
    from ask import cited_passages, location_appendix, ref_line, resolve_markers

    answer_id = "snapshot-links"
    directory = snapshot_root / f"{answer_id}_papers"
    directory.mkdir()
    snapshot = '<a id="p1"></a>\n**¶1** Original run text\n'
    (directory / "39133485.md").write_text(snapshot, encoding="utf-8")
    paper = {"n": 1, "pmid": "39133485", "md_file": "39133485.md", "title": "Study",
             "authors": "Author", "year": "2026", "journal": "Journal", "source": "pmc",
             "paras": [{"id": 1, "sec": "Results", "text": "Original run text"}], "cites": []}
    target = ({1: f"/v1/answers/{answer_id}/papers/1/markdown"} if storage == "http"
              else f"{answer_id}_papers")
    body, used = resolve_markers("Evidence [1¶1].", {1: paper}, target)
    text = "\n\n".join(("# Q: q", body, ref_line(paper, target),
                        location_appendix(cited_passages(used, {1: paper}), {1: paper}, target)))
    if storage == "file-url":
        text = text.replace(f"{answer_id}_papers/", directory.as_uri() + "/")
    (snapshot_root / f"{answer_id}.md").write_text(text, encoding="utf-8")
    # 同 PMID 的共享文献与本次快照不同，引用必须使用本次快照。
    assert "Original run text" not in (data_root / "library" / "39133485" / "fulltext.md").read_text(encoding="utf-8")
    if storage == "legacy":
        with SessionLocal() as db:
            import_legacy_answers(db)
    else:
        with SessionLocal() as db:
            db.add(Answer(id=answer_id, status="ready", question="q", queries=[], options={},
                          papers=[{"n": 1, "pmid": "39133485"}], citations=[], kb_hits=[],
                          answer_md=None if storage == "file-only" else text))
            db.commit()

    response = client.get(f"/v1/answers/{answer_id}/markdown", headers=auth(ADMIN_TOKEN))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", response.text)
    assert len(links) == 3
    assert all(link.startswith(f"/v1/answers/{answer_id}/papers/1/markdown") for link in links)
    for link in links:
        parsed = urlsplit(link)
        result = client.get(parsed.path, headers=auth(ADMIN_TOKEN))
        assert result.status_code == 200
        assert result.headers["content-type"].startswith("text/markdown")
        assert result.text == snapshot
        if parsed.fragment:
            assert f'id="{parsed.fragment}"' in result.text
    assert client.get(urlsplit(links[0]).path).status_code == 401
    assert client.get(f"/v1/answers/{answer_id}/papers/2/markdown",
                      headers=auth(ADMIN_TOKEN)).status_code == 404
    # HTTP 读取不回写 CLI 原稿。
    assert (snapshot_root / f"{answer_id}.md").read_text(encoding="utf-8") == text


@pytest.mark.parametrize("escape", ["metadata", "file-symlink", "directory-symlink"])
def test_paper_markdown_rejects_cross_answer_artifacts(client, snapshot_root, escape):
    root = snapshot_root
    other = root / "other_papers"
    other.mkdir()
    (other / "123.md").write_text("Private other answer", encoding="utf-8")
    directory = root / "safe_papers"
    if escape == "directory-symlink":
        directory.symlink_to(other, target_is_directory=True)
    else:
        directory.mkdir()
        if escape == "file-symlink":
            (directory / "123.md").symlink_to(other / "123.md")
    with SessionLocal() as db:
        db.add(Answer(id="safe", user_id="writer", status="ready", question="q", queries=[], options={},
                      papers=[{"n": 1, "pmid": "../other_papers/123" if escape == "metadata" else "123"}],
                      citations=[], kb_hits=[]))
        db.commit()
    result = client.get("/v1/answers/safe/papers/1/markdown", headers=auth(ADMIN_TOKEN))
    assert result.status_code == 404
    assert "Private other answer" not in result.text


def test_cli_citation_rendering_keeps_local_links():
    from ask import cited_passages, location_appendix, ref_line, resolve_markers

    paper = {"n": 1, "pmid": "123", "md_file": "123.md", "title": "Study", "authors": "Author",
             "year": "2026", "journal": "Journal", "source": "pmc",
             "paras": [{"id": 1, "sec": "Results", "text": "Evidence"}], "cites": []}
    body, used = resolve_markers("Valid [1¶1], invalid [1¶2].", {1: paper}, "run_papers")
    assert body == "Valid [1¶1](run_papers/123.md#p1), invalid [1]."
    assert "[原文](run_papers/123.md)" in ref_line(paper, "run_papers")
    appendix = location_appendix(cited_passages(used, {1: paper}), {1: paper}, "run_papers")
    assert "[¶1](run_papers/123.md#p1)" in appendix
    assert "#p2" not in appendix


def test_marker_groups_normalize_ranges_and_keep_prose():
    """模型写出 [1¶1-¶3, 1¶2] 这类区间/组合标记时，仍要落成可点的逐段标记。"""
    from ask import resolve_markers

    paper = {"n": 1, "pmid": "123", "md_file": "123.md", "paras": [{"id": i, "sec": "Results", "text": "x"}
                                                                   for i in (1, 2, 3)]}
    body, used = resolve_markers(
        "A [1¶1-¶3, 1¶2] B [1¶2-3] C [1¶1-¶12] D [见 1¶1] E [2024] F [1¶9]", {1: paper}, None)
    assert body == "A [1¶1] [1¶2] [1¶3] B [1¶2] [1¶3] C [1] D [见 1¶1] E [2024] F [1]"
    assert used == [(1, 1), (1, 2), (1, 3)]


def test_prose_marker_group_leaves_body_and_citation_list_untouched():
    """保留原样的括号不能进引用清单，否则附录会把没引过的段落标成正文引用。"""
    from ask import resolve_markers

    paper = {"n": 1, "pmid": "123", "md_file": "123.md", "paras": [{"id": 1, "sec": "Results", "text": "x"}]}
    body, used = resolve_markers("见 [1¶1 说明]。", {1: paper}, None)
    assert body == "见 [1¶1 说明]。"
    assert used == []


def test_legacy_markdown_cannot_map_other_answer_files(client, snapshot_root):
    root = snapshot_root
    other = root / "other_papers"
    other.mkdir()
    (other / "123.md").write_text("Other answer snapshot", encoding="utf-8")
    (root / "legacy.md").write_text(
        "# Q: q\n\n[1] Study [原文](other_papers/123.md)\n"
        "[2] Study [原文](legacy_papers/../other_papers/123.md)\n", encoding="utf-8")
    with SessionLocal() as db:
        import_legacy_answers(db)
    for n in (1, 2):
        response = client.get(f"/v1/answers/legacy/papers/{n}/markdown", headers=auth(ADMIN_TOKEN))
        assert response.status_code == 404


def test_missing_snapshot_never_falls_back_to_library(client, data_root: Path, snapshot_root):
    assert (data_root / "library" / "39133485" / "fulltext.md").is_file()
    with SessionLocal() as db:
        db.add(Answer(id="missing", user_id="writer", status="ready", question="q", papers=[{"n": 1, "pmid": "39133485"}]))
        db.commit()
    response = client.get("/v1/answers/missing/papers/1/markdown", headers=auth(OTHER_TOKEN))
    assert response.status_code == 404


@pytest.mark.parametrize("owner", ["writer", None])
def test_answer_and_all_run_materials_are_private(client, snapshot_root, owner):
    answer_id = "private-answer"
    directory = snapshot_root / f"{answer_id}_papers"
    directory.mkdir()
    (directory / "123.md").write_text("Private fulltext", encoding="utf-8")
    (directory / "123_notes.md").write_text("Private notes", encoding="utf-8")
    with SessionLocal() as db:
        db.add(Answer(id=answer_id, user_id=owner, status="ready", question="Private question",
                      queries=[], options={}, papers=[{"n": 1, "pmid": "123", "title": "Private paper"}],
                      citations=[], kb_hits=[], answer_md="Private answer"))
        db.commit()
    paths = [f"/v1/answers/{answer_id}{suffix}" for suffix in
             ("", "/markdown", "/papers/1", "/papers/1/markdown")]
    for path in paths:
        assert client.get(path, headers=auth(USER_TOKEN)).status_code == 404
        assert client.get(path, headers=auth(ADMIN_TOKEN)).status_code == 200
        expected = 200 if owner == "writer" else 404
        assert client.get(path, headers=auth(OTHER_TOKEN)).status_code == expected
    assert client.get("/v1/answers", headers=auth(USER_TOKEN)).json()["total"] == 0
    assert client.get("/v1/answers", headers=auth(OTHER_TOKEN)).json()["total"] == (1 if owner else 0)
    assert client.get("/v1/answers", headers=auth(ADMIN_TOKEN)).json()["total"] == 1
    assert client.delete(paths[0], headers=auth(USER_TOKEN)).status_code == 404
    assert directory.exists()
    assert client.delete(paths[0], headers=auth(ADMIN_TOKEN)).status_code == 204
    assert not directory.exists()


# ---------------------------------------------------------------- 文献解读
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"


def _pdf(name: str = "a.pdf"):
    return ("files", (name, PDF_BYTES, "application/pdf"))


def _interpret(client, files, question: str | None = None, token=OTHER_TOKEN):
    data = {} if question is None else {"question": question}
    return client.post("/v1/answers/interpret", files=files, data=data, headers=auth(token))


def _docsets(data_root: Path) -> list[Path]:
    directory = data_root / "docsets"
    return sorted(p for p in directory.iterdir()) if directory.exists() else []


def test_interpret_single_pdf_requires_question(client, data_root: Path):
    before = _docsets(data_root)

    r = _interpret(client, [_pdf()])

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert _docsets(data_root) == before, "被拒的请求不该留下 docset 目录"


def test_interpret_single_pdf_creates_a_qa_turn(client, arq, data_root: Path):
    r = _interpret(client, [_pdf()], "样本量多少")

    assert r.status_code == 202
    body = r.json()
    assert body["engine"] == "interpret"
    assert body["question"] == "样本量多少"
    assert body["filters_label"] == "文献解读 · 1 篇 PDF"
    assert body["options"]["mode"] == "qa"
    assert body["options"]["use_kb"] is True
    assert body["options"]["n_files"] == 1
    thread_id = None
    with SessionLocal() as db:
        row = db.get(Answer, body["id"])
        thread_id = row.thread_id
        assert thread_id and row.parent_id is None
        assert db.get(Job, body["job_id"]).kind == "interpret"
    assert arq.calls[-1][0] == "run_interpret_job"
    docset = data_root / "docsets" / thread_id
    assert (docset / "1.pdf").read_bytes() == PDF_BYTES
    manifest = json.loads((docset / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"] == [{"n": 1, "filename": "a.pdf", "bytes": len(PDF_BYTES)}]


def test_interpret_group_queues_overview_then_the_question(client, arq):
    r = _interpret(client, [_pdf("a.pdf"), _pdf("b.pdf")], "两篇的样本量分别是多少")

    assert r.status_code == 202
    first = r.json()
    assert first["question"] == "梳理这组文献的组织关系与差异"
    assert first["options"]["mode"] == "overview"
    assert first["options"]["use_kb"] is True

    thread = client.get(f"/v1/answers/{first['id']}/thread", headers=auth(OTHER_TOKEN)).json()
    assert [t["question"] for t in thread] == ["梳理这组文献的组织关系与差异", "两篇的样本量分别是多少"]
    with SessionLocal() as db:
        second = db.get(Answer, thread[1]["id"])
        assert second.parent_id == first["id"]
        assert second.thread_id == db.get(Answer, first["id"]).thread_id
        assert second.options["mode"] == "qa" and second.options["use_kb"] is False
        assert second.filters_label == "文献解读 · 2 篇 PDF"
    assert [c[0] for c in arq.calls] == ["run_interpret_job", "run_interpret_job"]


def test_interpret_rejects_non_pdf_and_oversized_sets(client, data_root: Path):
    before = _docsets(data_root)

    bad = _interpret(client, [_pdf(), ("files", ("b.txt", b"hello", "text/plain"))], "问题")
    assert bad.status_code == 422
    assert _docsets(data_root) == before, "部分落盘后失败必须清掉整个 docset"

    too_many = _interpret(client, [_pdf(f"{i}.pdf") for i in range(11)], "问题")
    assert too_many.status_code == 422
    assert too_many.json()["code"] == "validation_error"
    assert _docsets(data_root) == before


def _ready_interpret(client, files=None, question="样本量多少"):
    """建一轮已完成的解读答案：追问的前提是 thread 里有 ready 回合。"""
    body = _interpret(client, files or [_pdf()], question).json()
    with SessionLocal() as db:
        row = db.get(Answer, body["id"])
        row.status, row.answer_md = "ready", "# 结论"
        db.get(Job, body["job_id"]).status = "succeeded"
        db.commit()
        return body["id"], row.thread_id


def test_followup_on_interpret_reuses_the_docset(client, arq):
    first, thread_id = _ready_interpret(client)

    r = client.post(f"/v1/answers/{first}/followup", json={"question": "主要结局是什么"},
                    headers=auth(OTHER_TOKEN))

    assert r.status_code == 202
    body = r.json()
    assert body["engine"] == "interpret"
    with SessionLocal() as db:
        row = db.get(Answer, body["id"])
        assert row.thread_id == thread_id and row.parent_id == first
        assert row.options["mode"] == "qa" and row.options["use_kb"] is False
        assert row.filters_label == "文献解读 · 1 篇 PDF"
        assert db.get(Job, body["job_id"]).kind == "interpret"
    assert arq.calls[-1][0] == "run_interpret_job"


def test_deleting_the_last_interpret_turn_removes_the_docset(client, data_root: Path):
    first, thread_id = _ready_interpret(client)
    second = client.post(f"/v1/answers/{first}/followup", json={"question": "再说说"},
                         headers=auth(OTHER_TOKEN)).json()["id"]
    with SessionLocal() as db:
        db.get(Answer, second).status = "ready"
        db.commit()
    docset = data_root / "docsets" / thread_id

    assert client.delete(f"/v1/answers/{second}", headers=auth(OTHER_TOKEN)).status_code == 204
    assert docset.is_dir(), "会话还有回合时不能删掉共享的 docset"

    assert client.delete(f"/v1/answers/{first}", headers=auth(OTHER_TOKEN)).status_code == 204
    assert not docset.exists()


def test_interpret_paper_markdown_comes_from_the_docset(client, data_root: Path):
    answer_id, thread_id = _ready_interpret(client)
    docset = data_root / "docsets" / thread_id
    (docset / "1.md").write_text('<a id="p1"></a>**¶1** 上传原文', encoding="utf-8")

    ok = client.get(f"/v1/answers/{answer_id}/papers/1/markdown", headers=auth(OTHER_TOKEN))
    assert ok.status_code == 200
    assert '<a id="p1">' in ok.text

    assert client.get(f"/v1/answers/{answer_id}/papers/2/markdown",
                      headers=auth(OTHER_TOKEN)).status_code == 404


def test_interpret_paper_detail_serves_paragraphs_and_kb_facts(client, data_root: Path):
    answer_id, thread_id = _ready_interpret(client)
    docset = data_root / "docsets" / thread_id
    (docset / "1.md").write_text("# 上传文献", encoding="utf-8")
    (docset / "1_paragraphs.json").write_text(
        json.dumps([{"id": 1, "sec": "p.1", "page": 1, "text": "段落"}]), encoding="utf-8")
    (docset / "kb-facts-1.json").write_text(
        json.dumps({"version": 1, "facts": [{"fact": "f", "pid": 1, "quote": "q", "verified": True}]}),
        encoding="utf-8")
    with SessionLocal() as db:
        db.get(Answer, answer_id).papers = [{"n": 1, "title": "上传文献", "source": "upload"}]
        db.commit()

    body = client.get(f"/v1/answers/{answer_id}/papers/1", headers=auth(OTHER_TOKEN)).json()

    assert body["source"] == "upload"
    assert body["fulltext_md"] == "# 上传文献"
    assert [p["id"] for p in body["paragraphs"]] == [1]
    assert [f["fact"] for f in body["facts"]] == ["f"]
    assert body["notes_md"] == "" and body["citations"] == []
