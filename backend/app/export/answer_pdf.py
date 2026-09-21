"""把一条 ready 的答案渲染成 PDF。

三端（Web / Apple / Flutter）都调同一个端点拿这份字节：三套排版引擎不可能产出同一
份 PDF，所以排版只在服务端做一次。落地方式是「HTML 模板 + Chromium 打印」——runtime
镜像本来就装了 Playwright Chromium（机构全文抓取用），不必再引 weasyprint/reportlab
这类另一套排版栈。

模块标题、徽标文案、语义色都复刻三端界面的固定元数据（`SectionHeading.MODULES`、
`SourceBadge`、`RankBadge`、`KbSupplement`），不复用模型写的标签字面量。
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import re
from collections import Counter
from functools import cache
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from ..models import Answer
from ..services.answers import http_answer_markdown
from .markdown import citation_color, highlight_quotes, render_markdown
from .sections import AnswerSection, split_answer_sections

HERE = Path(__file__).parent

#: 模块 key → (中文标题, 英文小字, 语义色)；与 `SectionHeading.tsx` 的 MODULES 同源。
MODULES: dict[str, tuple[str, str, str]] = {
    "conclusion": ("结论", "BOTTOM LINE", "primary"),
    "evidence": ("证据", "EVIDENCE", "primary"),
    "picos": ("PICOS 证据表", "PICOS TABLE", "primary"),
    "caveats": ("局限", "CAVEATS", "warning"),
    "sources": ("参考文献", "REFERENCES", "neutral"),
    "passages": ("引用原文", "SOURCE PASSAGES", "neutral"),
    "kb": ("知识库补充", "KNOWLEDGE BASE", "neutral"),
    "queries": ("检索式", "SEARCH QUERIES", "neutral"),
}

#: 全文来源徽标；与 `SourceBadge.tsx` 同源。
SOURCE_LABELS = {
    "pmc": "全文 · PMC",
    "pdf": "全文 · PDF",
    "inst": "全文 · 机构",
    "upload": "全文 · 上传",
    "abstract": "仅摘要",
}

DISCLAIMER = "本文为文献综述，仅供科研与教学参考，不构成医疗建议。"

QUARTILE_RE = re.compile(r"^(?:Q)?([1-4])$", re.IGNORECASE)
#: Windows/macOS 都不接受的文件名字符，连同控制字符一起折成空格。
FILENAME_BAD_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

RENDER_TIMEOUT_S = 60
#: 每次导出都要拉一个 Chromium，限并发避免打印把 worker 机器挤爆。
_SEMAPHORE = asyncio.Semaphore(2)

_HEADER = """
<div style="width:100%;margin:0 18mm;padding-bottom:3mm;border-bottom:0.5px solid #E1DDD7;
 font:400 7.5pt/1.2 Inter,system-ui,sans-serif;color:#6B6157;display:flex;align-items:center;gap:4px">
  <img src="{logo}" style="width:10px;height:10px">
  <span style="font-weight:600;color:#1B63D6">PicoSeek</span>
  <span style="margin-left:auto;text-align:right">{question}</span>
</div>
"""

_FOOTER = """
<div style="width:100%;margin:0 18mm;padding-top:2mm;
 font:400 7.5pt/1.2 Inter,system-ui,sans-serif;color:#6B6157;display:flex;align-items:center;gap:8px">
  <span>{disclaimer}</span>
  <span style="margin-left:auto;white-space:nowrap">第 <span class="pageNumber"></span> 页 /
   共 <span class="totalPages"></span> 页</span>
</div>
"""


@cache
def _logo_data_uri() -> str:
    data = base64.b64encode((HERE / "logo.png").read_bytes()).decode("ascii")
    return "data:image/png;base64," + data


@cache
def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(HERE),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env


def _local(value: dt.datetime) -> dt.datetime:
    """SQLite 读回来的是 naive datetime，一律当 UTC，再换到进程本地时区。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone()


def _stamp(row: Answer) -> dt.datetime:
    return _local(row.finished_at or row.created_at)


def _rank_badge(paper: dict[str, Any]) -> dict[str, str]:
    """分区徽标；与 `RankBadge.tsx` 同一套判定。"""
    match = QUARTILE_RE.match(str(paper.get("quartile") or ""))
    if not match:
        return {"text": paper.get("rank_label") or "未收录", "tone": "neutral"}
    q = match.group(1)
    return {"text": paper.get("rank_label") or f"Q{q}", "tone": f"q{q}"}


def _reference(paper: dict[str, Any], cited: int) -> dict[str, Any]:
    verified = paper.get("n_citations_verified") or 0
    total = paper.get("n_citations") or 0
    badges = [
        _rank_badge(paper),
        {"text": SOURCE_LABELS.get(paper.get("source") or "abstract", paper.get("source") or ""),
         "tone": "neutral"},
        {"text": f"引文核实 {verified}/{total}",
         "tone": "success" if verified == total else "warning"},
    ]
    ids: list[dict[str, str | None]] = []
    if paper.get("pmid"):
        ids.append({"text": f"PMID:{paper['pmid']}",
                    "href": f"https://pubmed.ncbi.nlm.nih.gov/{quote(str(paper['pmid']))}/"})
    if paper.get("doi"):
        ids.append({"text": f"DOI:{paper['doi']}",
                    "href": f"https://doi.org/{quote(str(paper['doi']))}"})
    if paper.get("pmcid"):
        ids.append({"text": str(paper["pmcid"]), "href": None})
    meta = [
        {"text": paper.get("authors") or "", "italic": False},
        {"text": paper.get("journal") or "", "italic": True},
        {"text": paper.get("year") or "", "italic": False},
    ]
    return {
        "n": paper["n"],
        "color": citation_color(paper["n"]),
        "title": paper.get("title") or "未提供标题",
        "meta": [m for m in meta if m["text"]],
        "badges": [b for b in badges if b["text"]],
        "cited": cited,
        "ids": ids,
    }


def _passage_groups(
    citations: list[dict[str, Any]], by_n: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """引用原文附录：按论文分组，组头与位置文案取自 `core/ask.py` `location_appendix`。"""
    groups: list[dict[str, Any]] = []
    for item in sorted(citations, key=lambda c: (c["n"], c["pid"])):
        n = item["n"]
        if not groups or groups[-1]["n"] != n:
            paper = by_n.get(n, {})
            title = (paper.get("title") or "")[:100]
            head = f"[{n}] {title} — {paper.get('journal', '')} ({paper.get('year', '')})"
            groups.append({"n": n, "color": citation_color(n), "head": head, "items": []})
        loc = item.get("sec") or ""
        if item.get("page"):
            loc = f"{loc}, p.{item['page']}" if loc else f"p.{item['page']}"
        groups[-1]["items"].append({
            "pid": item["pid"],
            "loc": loc,
            "text": Markup(highlight_quotes(item.get("text") or "", item.get("quotes") or [])),
            "key_finding": not item.get("from_marker", True),
        })
    return groups


def build_answer_html(row: Answer, *, root_question: str | None = None) -> str:
    """组装打印用 HTML。调用方保证 `row.status == "ready"`。

    `body_md` 为空的行（旧 CLI 稿与 codex 回合）没有结构化 papers，整篇按单块渲染：
    它自带参考文献段落，再按标签切分会把那段并进「局限」；裸 `[n]` 也不当引用，
    与 Apple `citationLimit: nil`、Flutter `papers.isEmpty ? null` 的口径一致。
    """
    structured = row.body_md is not None
    body = row.body_md or "" if structured else http_answer_markdown(row)
    papers = sorted(row.papers or [], key=lambda p: p["n"])
    by_n = {p["n"]: p for p in papers}
    max_n = max((p["n"] for p in papers), default=0) if structured else 0
    citations = list(row.citations or [])
    passages = {(c["n"], c["pid"]) for c in citations}

    raw_sections = split_answer_sections(body) if structured else [AnswerSection("other", body)]
    used: list[tuple[int, int | None]] = []
    sections: list[dict[str, Any]] = []
    for section in raw_sections:
        html, section_used = render_markdown(section.markdown, max_n=max_n, passages=passages)
        used += section_used
        module = MODULES.get(section.kind)
        sections.append({
            "kind": section.kind,
            "html": Markup(html),
            "title": module[0] if module else "",
            "eyebrow": module[1] if module else "",
            "tone": module[2] if module else "neutral",
        })

    counts = Counter(n for n, _ in used)
    cited_ns = set(counts)
    references = [_reference(p, counts.get(p["n"], 0))
                  for p in papers if p.get("relevance") != 0 or p["n"] in cited_ns]

    meta: list[str] = []
    if row.n_papers is not None:
        meta.append(f"{row.n_papers} 篇文献 · {row.n_fulltext or 0} 篇全文")
    meta.append(_stamp(row).strftime("%Y-%m-%d %H:%M %Z"))

    return _env().get_template("answer.html.j2").render(
        logo=_logo_data_uri(),
        question=row.question,
        is_codex=row.engine == "codex",
        filters_label=row.filters_label or "",
        root_question=root_question if row.parent_id else None,
        meta=meta,
        sections=sections,
        modules=MODULES,
        references=references,
        n_papers=len(papers),
        n_fulltext=row.n_fulltext or 0,
        passage_groups=_passage_groups(citations, by_n),
        n_citations=len(citations),
        kb_hits=list(row.kb_hits or []),
        queries=list(row.queries or []),
        answer_id=row.id,
        disclaimer=DISCLAIMER,
    )


def export_filename(row: Answer) -> tuple[str, str]:
    """`(ascii 文件名, utf-8 文件名)`：前者给 `filename=`，后者给 `filename*=`。"""
    slug = " ".join(FILENAME_BAD_RE.sub(" ", row.question).split())[:40].strip()
    date = _stamp(row).strftime("%Y%m%d")
    return f"PicoSeek-{row.id}.pdf", f"PicoSeek-{date}-{slug or row.id}.pdf"


async def _render(html: str, question: str) -> bytes:
    from playwright.async_api import async_playwright

    header = _HEADER.format(logo=_logo_data_uri(), question=escape(question[:40]))
    footer = _FOOTER.format(disclaimer=escape(DISCLAIMER))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="load")
            await page.evaluate("document.fonts.ready")
            return await page.pdf(
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template=header,
                footer_template=footer,
                margin={"top": "24mm", "bottom": "20mm", "left": "18mm", "right": "18mm"},
            )
        finally:
            await browser.close()


async def render_pdf(html: str, question: str = "") -> bytes:
    """打印成 PDF。浏览器不复用：导出是低频操作，省掉常驻进程与它的泄漏面。"""
    async with _SEMAPHORE:
        return await asyncio.wait_for(_render(html, question), RENDER_TIMEOUT_S)
