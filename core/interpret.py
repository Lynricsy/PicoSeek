#!/usr/bin/env python
"""文献解读：把用户上传的一组 PDF（docset）当作唯一证据来源，产出综述或问答。

与 `ask.run_ask` 的区别：没有检索、没有逐篇 PICOS 精读，也不做相关性打分——
文献是用户给定的，全部必须进入上下文。因此这里只有两个阶段：
`fulltext`（解析 PDF，结果落在 docset 目录里可复用）与 `synthesize`（一次模型调用）。

产物与 ask 共用同一套下游：`resolve_markers` 归一化 `[n¶pid]` 标记，
`cited_passages` / `location_appendix` 渲染原文定位，`write_kb_snapshot` 交给
后台 `answer_kb` 任务入库，于是上传的 PDF 同时进共享文献库。
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import ingest
import knowledge_store as ks
from ask import (
    Emit,
    PipelineCancelled,
    PipelineError,
    _atomic_json,
    cited_passages,
    llm,
    location_appendix,
    print_emit,
    resolve_markers,
    write_kb_snapshot,
)
from ingest import PdfUnreadable

PER_PAPER_MAX = 60000      # 单篇送入模型的字符上限
TOTAL_MAX = 360000         # 全部论文合计上限；每篇预算 = min(PER_PAPER_MAX, TOTAL_MAX // n)
HISTORY_TURNS = 6          # 追问时携带的历史回合数
HISTORY_CHARS = 4000       # 每个历史回合正文的截断长度
OVERVIEW_QUESTION = "梳理这组文献的组织关系与差异"

# 与 ask.fetch_fulltext 同一字面量：下游（前端提示、人工排查）按这句话识别被截断的上下文
TRUNCATED = "\n[... truncated: later paragraphs not shown to the reader model ...]"
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
DOI_SCAN_CHARS = 8000      # DOI 只可能出现在首页页眉/摘要区，全文扫描徒增误报


@dataclass(frozen=True)
class InterpretOptions:
    docset_dir: str
    question: str
    mode: Literal["overview", "qa"]
    history: tuple[tuple[str, str], ...] = ()      # (question, body_md 截断) 升序


@dataclass
class InterpretResult:
    papers: list[dict]         # ask 风格的 paper dict（含 paras/fulltext_md 等中间产物）
    used_n: list[int]
    body_md: str               # 裸标记 [n¶pid]
    answer_md: str             # 带链接的完整渲染稿
    citations: list[dict]
    papers_dir: str            # = docset_dir


def manifest_path(docset_dir: str) -> str:
    return os.path.join(docset_dir, "manifest.json")


def find_doi(text: str) -> str:
    """从正文开头找一个 DOI；找不到返回空串（上传件本来就常常没有 DOI）。"""
    m = DOI_RE.search(text[:DOI_SCAN_CHARS])
    return m.group(0).rstrip(".,;)]") if m else ""


def guess_title(pdf_path: str, paras: list[dict], filename: str) -> str:
    """标题优先取 PDF 元数据，其次首段，最后退回文件名。

    元数据常被排版软件写成 `Microsoft Word - xxx.docx` 之类的垃圾，所以要求
    「有空格、长度像标题」，并显式排除已知的模板前缀。
    """
    try:
        from pypdf import PdfReader

        raw = str((PdfReader(pdf_path).metadata or {}).get("/Title") or "").strip()
    except Exception:  # noqa: BLE001  元数据不可读不是错误，退回下一档来源
        raw = ""
    lowered = raw.lower()
    if (8 <= len(raw) <= 300 and " " in raw
            and not lowered.startswith(("microsoft word", "untitled", "doc1"))):
        return raw
    for p in paras:
        text = " ".join(str(p.get("text") or "").split())
        if 20 <= len(text) <= 300:
            return text[:200]
    return os.path.splitext(filename)[0][:200]


def _read_manifest(docset_dir: str) -> dict:
    try:
        with open(manifest_path(docset_dir), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PipelineError("docset manifest missing") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), list) or not data["files"]:
        raise PipelineError("docset manifest missing")
    return data


def _paper_header(title: str, filename: str, doi: str, n_paras: int) -> str:
    return (f"# {title}\n\n上传文件：{filename}  \nDOI:{doi}  \n"
            f"source: upload · {n_paras} paragraphs\n\n")


def ensure_parsed(docset_dir: str, *, emit: Emit = print_emit,
                  should_cancel: Callable[[], bool] = lambda: False) -> list[dict]:
    """解析 docset 里的每篇 PDF，返回 ask 风格的 paper dict 列表。

    解析产物（`<n>.md` / `<n>_paragraphs.json`）落在 docset 目录里：追问时直接复用，
    同一组文献只解析一次。manifest 会被补上 title / doi / n_paragraphs 供 API 读取。
    """
    def _check() -> None:
        if should_cancel():
            raise PipelineCancelled()

    manifest = _read_manifest(docset_dir)
    entries = manifest["files"]
    total = len(entries)
    budget = min(PER_PAPER_MAX, TOTAL_MAX // total)

    _check()
    emit({"type": "stage", "stage": "fulltext", "status": "started", "detail": {"files": total}})

    papers: list[dict] = []
    dirty = False
    for index, entry in enumerate(entries, 1):
        _check()
        n = int(entry["n"])
        filename = str(entry.get("filename") or f"{n}.pdf")
        pdf_path = os.path.join(docset_dir, f"{n}.pdf")
        md_path = os.path.join(docset_dir, f"{n}.md")
        paras_path = os.path.join(docset_dir, f"{n}_paragraphs.json")
        if os.path.exists(md_path) and os.path.exists(paras_path):
            with open(paras_path, encoding="utf-8") as f:
                paras = json.load(f)
            with open(md_path, encoding="utf-8") as f:
                fulltext_md = f.read()
            title = str(entry.get("title") or "") or guess_title(pdf_path, paras, filename)
            doi = str(entry.get("doi") or "")
        else:
            try:
                text, paras = ingest.parse_pdf(pdf_path, PER_PAPER_MAX)
            except PdfUnreadable as e:
                raise PdfUnreadable(f"{filename}: {e}") from e
            title = guess_title(pdf_path, paras, filename)
            doi = find_doi(text)
            fulltext_md = _paper_header(title, filename, doi, len(paras)) + ks.anchored_markdown(paras)
            _atomic_json(paras_path, paras)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(fulltext_md)
        if (entry.get("title"), entry.get("doi"), entry.get("n_paragraphs")) != (title, doi, len(paras)):
            entry.update(title=title, doi=doi, n_paragraphs=len(paras))
            dirty = True

        model_text = ks.numbered_text(paras)
        if len(model_text) > budget:
            model_text = model_text[:budget] + TRUNCATED
        papers.append({
            "n": n, "pmid": "", "doi": doi, "pmcid": "", "title": title, "year": "",
            "journal": "", "issn": "", "authors": "", "quartile": "", "rank": None,
            "types": [], "source": "upload", "relevance": None, "filename": filename,
            "md_file": f"{n}.md", "paras": paras, "fulltext_md": fulltext_md,
            "text": model_text, "cites": [],
        })
        emit({"type": "progress", "stage": "fulltext", "current": index, "total": total,
              "title": filename})

    if dirty:
        _atomic_json(manifest_path(docset_dir), manifest)
    emit({"type": "stage", "stage": "fulltext", "status": "finished",
          "detail": {"papers": total, "paragraphs": sum(len(p["paras"]) for p in papers)}})
    return papers


# ------------------------------------------------------------------ prompts
# 引用规则逐字沿用 ask.SYN_SYS：标记含义、一个方括号一个标记、跟随用户语言。
_CITE_RULES = """Inside the papers, every paragraph is prefixed with a number like [¶12]; a marker like [3¶12] means
"paper 3, paragraph 12". Cite every factual claim with such a marker, copying the paragraph number exactly as shown
(never invent paragraph numbers). Every bracket holds exactly ONE marker: write [3¶12] [3¶14], never ranges or
bundles like [3¶12-¶14] or [3¶12, 3¶14]. Write in the SAME LANGUAGE as the user's question."""

OVERVIEW_SYS = f"""You are a research analyst comparing a set of uploaded papers.
You are given the full text of every paper the user uploaded, each labelled [n]. Use ONLY these texts.
{_CITE_RULES}
Return Markdown with EXACTLY these four section labels, each on its own line:
**文献概览 / Papers at a glance** — a Markdown table with columns: [n] | 标题 | 研究类型 | 核心问题. One row per paper.
**组织关系 / How the papers relate** — 主题脉络、方法谱系、时间先后、延续或互补关系。Only state relations the texts
actually support; if two papers are unrelated, say so.
**差异对照 / Key differences** — a Markdown table whose first column is 维度 and whose remaining columns are [1] [2] …
The 维度 rows are fixed and in this order: 人群与样本量 / 干预或方法 / 对照 / 主要结局与结果 / 结论方向 / 局限.
Write "未报告" for anything a paper does not state.
**总结 / Synthesis** — 3-5 sentences.
Do NOT write the reference list; it will be appended automatically."""

QA_SYS = f"""You are a research assistant answering questions strictly from the uploaded papers.
You are given the full text of every paper the user uploaded, each labelled [n]. Answer ONLY from these texts;
if they do not contain the evidence, say explicitly what is missing instead of guessing.
{_CITE_RULES}
PREVIOUS TURNS, when given, exist only so you can resolve references like "它" or "第二篇"; never cite anything from
them that is not also in the paper texts.
Return Markdown with these section labels, each on its own line:
**结论 / Bottom line** — 2-4 sentences.
**证据 / Evidence** — bullets, each ending with its marker.
**局限 / Caveats** — bullets; include this section only when the papers state relevant limitations.
Do NOT write the reference list; it will be appended automatically."""


def _papers_block(papers: list[dict]) -> str:
    return "".join(f"PAPER [{p['n']}] {p['title']} — file: {p['filename']}\n{p['text']}\n\n"
                   for p in papers)


def _user_message(opts: InterpretOptions, papers: list[dict]) -> str:
    if opts.mode == "overview":
        return f"PAPERS: {len(papers)}\n\n" + _papers_block(papers)
    parts = [f"QUESTION: {opts.question}\n\n"]
    if opts.history:
        parts.append("PREVIOUS TURNS:\n")
        parts.extend(f"- Q: {q}\n  A: {a}\n" for q, a in opts.history)
    parts.append("\nPAPERS:\n")
    parts.append(_papers_block(papers))
    return "".join(parts)


def _ref_line(p: dict, paper_urls: dict[int, str]) -> str:
    return (f"[{p['n']}] {p['title']} 〔上传 PDF · {p['filename']}〕 "
            f"[原文]({paper_urls[p['n']]})")


def run_interpret(opts: InterpretOptions, *, emit: Emit = print_emit,
                  should_cancel: Callable[[], bool] = lambda: False,
                  paper_urls: dict[int, str]) -> InterpretResult:
    """解析 docset → 一次模型调用 → 归一化引用并渲染完整稿。

    失败用异常表达（PdfUnreadable / PipelineError / PipelineCancelled），
    调用方据 `.code` 决定任务终态。
    """
    def _check() -> None:
        if should_cancel():
            raise PipelineCancelled()

    papers = ensure_parsed(opts.docset_dir, emit=emit, should_cancel=should_cancel)
    by_n = {p["n"]: p for p in papers}

    _check()
    system = OVERVIEW_SYS if opts.mode == "overview" else QA_SYS
    emit({"type": "stage", "stage": "synthesize", "status": "started", "detail": {"mode": opts.mode}})
    raw = llm(system, _user_message(opts, papers), think=True, timeout=None,
              emit=emit, operation="interpret")
    emit({"type": "stage", "stage": "synthesize", "status": "finished", "detail": {"chars": len(raw)}})

    body_md, used = resolve_markers(raw, by_n, None)
    linked, _ = resolve_markers(raw, by_n, paper_urls)
    passages = cited_passages(used, by_n)
    used_n = sorted({n for n, _ in used}) or [p["n"] for p in papers]

    refs = "\n".join(_ref_line(p, paper_urls) for p in papers)
    answer_md = (f"# Q: {opts.question}\n\n文献解读　|　{len(papers)} 篇上传 PDF\n\n{linked}\n\n"
                 f"**参考文献 / References**\n{refs}\n\n"
                 f"{location_appendix(passages, by_n, paper_urls)}\n\n"
                 "*This is a literature summary for research/educational use, not medical advice.*\n")
    return InterpretResult(papers=papers, used_n=used_n, body_md=body_md, answer_md=answer_md,
                           citations=passages, papers_dir=opts.docset_dir)


def save_kb_snapshot(res: InterpretResult) -> str:
    """上传的 PDF 同样进共享文献库：入库输入与 ask 用同一份快照格式。"""
    return write_kb_snapshot(res.papers_dir, "", res.papers)
