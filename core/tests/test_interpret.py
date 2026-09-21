"""run_interpret 的契约：解析幂等、manifest 回填、提示词分发、引用归一化、上下文预算。

PDF 解析与 LLM 都换成替身；被断言的是「一组上传 PDF 变成带定位的解读稿」这个
可观测结果，而不是内部调用顺序。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import interpret
import literature as lit

SENTENCE = "Sentence about trial outcomes with numbers. "
BODY = "--- page 1 ---\n" + SENTENCE * 120
LONG_BODY = "--- page 1 ---\n" + SENTENCE * 3000       # > PER_PAPER_MAX，用来验证预算截断


def _docset(tmp_path: Path, names: list[str]) -> Path:
    directory = tmp_path / "docset"
    directory.mkdir()
    files = []
    for i, name in enumerate(names, 1):
        (directory / f"{i}.pdf").write_bytes(b"%PDF-1.4\n")
        files.append({"n": i, "filename": name, "bytes": 9})
    (directory / "manifest.json").write_text(
        json.dumps({"version": 1, "files": files}), encoding="utf-8")
    return directory


@pytest.fixture
def pdf_text(monkeypatch):
    """替身解析器：记录被解析过的路径，便于断言幂等。"""
    calls: list[str] = []

    def fake(path: str, max_chars: int) -> str:
        calls.append(path)
        return BODY

    monkeypatch.setattr(lit, "pdf_text", fake)
    return calls


@pytest.fixture
def fake_llm(monkeypatch):
    """记录 system/user，并回一段带真实段落标记的答复。"""
    seen: dict = {}

    def fake(system: str, user: str, **kw) -> str:
        seen["system"], seen["user"] = system, user
        return "**结论 / Bottom line** — 两篇都报告了主要结局 [1¶2]，第二篇样本更大 [2¶1]。"

    monkeypatch.setattr(interpret, "llm", fake)
    return seen


def _opts(directory: Path, **kw) -> interpret.InterpretOptions:
    base = {"docset_dir": str(directory), "question": interpret.OVERVIEW_QUESTION,
            "mode": "overview"}
    return interpret.InterpretOptions(**(base | kw))


def test_parsing_is_idempotent_across_turns(tmp_path, pdf_text):
    directory = _docset(tmp_path, ["a.pdf", "b.pdf"])

    first = interpret.ensure_parsed(str(directory))
    assert len(pdf_text) == 2
    second = interpret.ensure_parsed(str(directory))

    assert len(pdf_text) == 2, "追问不该重新解析同一组 PDF"
    assert [p["paras"] for p in second] == [p["paras"] for p in first]
    assert (directory / "1.md").read_text(encoding="utf-8").startswith("# ")
    assert json.loads((directory / "2_paragraphs.json").read_text(encoding="utf-8"))[0]["id"] == 1


def test_manifest_is_backfilled_with_title_doi_and_paragraph_count(tmp_path, pdf_text, monkeypatch):
    monkeypatch.setattr(lit, "pdf_text",
                        lambda path, max_chars: BODY + "\nhttps://doi.org/10.1016/j.x.2026.01.002\n")
    directory = _docset(tmp_path, ["trial.pdf"])

    papers = interpret.ensure_parsed(str(directory))

    entry = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["files"][0]
    assert entry["title"] == papers[0]["title"] and entry["title"]
    assert entry["doi"] == "10.1016/j.x.2026.01.002"
    assert entry["n_paragraphs"] == len(papers[0]["paras"])


def test_unreadable_pdf_names_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(lit, "pdf_text", lambda path, max_chars: "ERROR: encrypted")
    directory = _docset(tmp_path, ["scan.pdf"])

    with pytest.raises(interpret.PdfUnreadable) as exc:
        interpret.ensure_parsed(str(directory))

    assert "scan.pdf" in str(exc.value)
    assert exc.value.code == "pdf_unreadable"


def test_overview_and_qa_use_different_prompts(tmp_path, pdf_text, fake_llm):
    directory = _docset(tmp_path, ["a.pdf", "b.pdf"])
    urls = {1: "/v1/answers/x/papers/1/markdown", 2: "/v1/answers/x/papers/2/markdown"}

    interpret.run_interpret(_opts(directory), paper_urls=urls)
    assert fake_llm["system"] is interpret.OVERVIEW_SYS
    assert fake_llm["user"].startswith("PAPERS: 2")

    interpret.run_interpret(_opts(directory, mode="qa", question="样本量多少",
                                  history=(("上一问", "上一答"),)), paper_urls=urls)
    assert fake_llm["system"] is interpret.QA_SYS
    assert fake_llm["user"].startswith("QUESTION: 样本量多少")
    assert "PREVIOUS TURNS:\n- Q: 上一问\n  A: 上一答" in fake_llm["user"]


def test_markers_are_normalised_and_located(tmp_path, pdf_text, fake_llm):
    directory = _docset(tmp_path, ["a.pdf", "b.pdf"])
    urls = {1: "/v1/answers/x/papers/1/markdown", 2: "/v1/answers/x/papers/2/markdown"}

    res = interpret.run_interpret(_opts(directory), paper_urls=urls)

    assert "[1¶2]" in res.body_md and "](" not in res.body_md      # 正文里是裸标记
    assert res.used_n == [1, 2]
    assert "[1¶2](/v1/answers/x/papers/1/markdown#p2)" in res.answer_md
    assert "〔上传 PDF · a.pdf〕" in res.answer_md
    assert res.citations[0]["from_marker"] is True
    assert {c["n"] for c in res.citations} == {1, 2}


def test_per_paper_budget_truncates_long_papers(tmp_path, monkeypatch, fake_llm):
    monkeypatch.setattr(lit, "pdf_text", lambda path, max_chars: LONG_BODY)
    directory = _docset(tmp_path, ["a.pdf", "b.pdf"])

    papers = interpret.ensure_parsed(str(directory))

    budget = min(interpret.PER_PAPER_MAX, interpret.TOTAL_MAX // 2)
    assert papers[0]["text"].endswith(interpret.TRUNCATED)
    assert len(papers[0]["text"]) == budget + len(interpret.TRUNCATED)
    assert len(papers[0]["paras"]) > 0     # 完整段落仍保留，只有送模型的文本被截


def test_stage_events_bracket_parsing_and_synthesis(tmp_path, pdf_text, fake_llm):
    directory = _docset(tmp_path, ["a.pdf", "b.pdf"])
    events: list[dict] = []

    interpret.run_interpret(_opts(directory), emit=events.append,
                            paper_urls={1: "/p/1", 2: "/p/2"})

    assert [f"{e['stage']}/{e['status']}" for e in events if e["type"] == "stage"] == [
        "fulltext/started", "fulltext/finished", "synthesize/started", "synthesize/finished"]
    assert [(e["current"], e["total"]) for e in events if e["type"] == "progress"] == [(1, 2), (2, 2)]


def test_cancellation_is_cooperative(tmp_path, pdf_text):
    directory = _docset(tmp_path, ["a.pdf"])
    with pytest.raises(interpret.PipelineCancelled):
        interpret.ensure_parsed(str(directory), should_cancel=lambda: True)


def test_missing_manifest_is_a_pipeline_error(tmp_path):
    directory = tmp_path / "empty"
    directory.mkdir()
    with pytest.raises(interpret.PipelineError):
        interpret.ensure_parsed(str(directory))
