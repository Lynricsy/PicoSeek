#!/usr/bin/env python
"""单篇 PDF 入库：上传文件或按 DOI 经机构访问下载 → 分段 → 抽事实 → library + kb。

与 `ask.fetch_fulltext` 的区别：那条路径把下载/解析/落盘和问答上下文 `p` 绑死（还要写
`answers/<run>_papers/`），无法当独立入口复用；这里只做「一篇文献进库」，事件与失败码
沿用 ask 的形状，方便 HTTP worker 用同一套映射。
"""
from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import journal_rank as jr
import knowledge_store as ks
import literature as lit
from ask import (
    PAYWALL_STATE,
    Emit,
    PipelineCancelled,
    PipelineError,
    llm,
    paywall_fetch,
    print_emit,
)

# meta 的固定键序，与 ask.py 写 library 时一致（消费者：knowledge_store、backend PaperMeta）
META_STR_KEYS = ("pmid", "doi", "pmcid", "title", "year", "journal", "issn", "authors", "quartile")


@dataclass(frozen=True)
class IngestSource:
    kind: Literal["upload", "doi"]
    pdf_path: str
    doi: str
    meta: dict


@dataclass(frozen=True)
class IngestResult:
    key: str
    n_paragraphs: int
    n_facts: int
    items: int


class PdfUnreadable(PipelineError):
    code = "pdf_unreadable"


class FulltextUnavailable(PipelineError):
    code = "fulltext_unavailable"


def parse_pdf(pdf_path: str, max_chars: int) -> tuple[str, list[dict]]:
    """pypdf 取文本并分段；扫描件 / 加密 / 无段落一律抛 PdfUnreadable。

    `run_ingest` 与 `interpret.ensure_parsed` 共用同一套可读性判定，
    两条入口对同一份坏 PDF 必须给出同一个失败码。
    """
    text = lit.pdf_text(pdf_path, max_chars * 2)
    if text.startswith("ERROR"):
        raise PdfUnreadable(text)
    if len(text) <= 2000:      # 阈值与 ask.fetch_fulltext 的机构访问分支一致：更短的多半是扫描件
        raise PdfUnreadable(f"only {len(text)} chars extracted")
    paras = ks.paragraphs_from_pdf_text(text)
    if not paras:
        raise PdfUnreadable("no paragraphs")
    return text, paras


def run_ingest(src: IngestSource, *, emit: Emit = print_emit,
               should_cancel: Callable[[], bool] = lambda: False,
               max_chars: int = 28000) -> IngestResult:
    """把一篇 PDF 落成 library 条目并索引进 kb。失败用异常表达，调用方据 `.code` 映射错误码。"""
    def _check() -> None:
        if should_cancel():
            raise PipelineCancelled()

    def _stage(stage: str, status: str, **detail) -> None:
        emit({"type": "stage", "stage": stage, "status": status, "detail": detail})

    title = str(src.meta.get("title") or "")

    _check()
    _stage("fulltext", "started", source=src.kind, doi=src.doi)

    if src.kind == "doi":
        if paywall_fetch is None:
            raise FulltextUnavailable("playwright not installed")
        os.makedirs(os.path.dirname(src.pdf_path) or ".", exist_ok=True)
        ok, note = paywall_fetch.download_pdf(src.doi, src.pdf_path, PAYWALL_STATE)
        emit({"type": "log", "level": "info",
              "message": f"institutional {'OK' if ok else 'no'} DOI:{src.doi} — {note}"})
        if not ok:
            raise FulltextUnavailable(note)

    _check()
    _text, paras = parse_pdf(src.pdf_path, max_chars)
    _stage("fulltext", "finished", n_paragraphs=len(paras))

    _check()
    _stage("kb", "started", title=title)
    emit({"type": "progress", "stage": "kb", "current": 0, "total": 1, "title": title})
    facts = ks.extract_facts(paras, lambda s, u: llm(s, u, emit=emit), "")
    emit({"type": "progress", "stage": "kb", "current": 1, "total": 1, "title": title})

    _check()
    meta = {k: str(src.meta.get(k) or "") for k in META_STR_KEYS}
    meta["types"] = list(src.meta.get("types") or [])
    meta["source"] = "upload" if src.kind == "upload" else "inst"
    info = None
    if not meta["quartile"]:
        info = jr.lookup(issn=meta["issn"], title=meta["journal"])
        meta["quartile"] = info["quartile"] if info else ""
    rank_label = jr.label(info)
    meta.update(indexed_at=dt.datetime.now().isoformat(timespec="seconds"),
                n_paragraphs=len(paras), n_facts=len(facts))

    header = (f"# {meta['title']}\n\n{meta['authors']} ({meta['year']}) *{meta['journal']}* {rank_label}  \n"
              f"PMID:{meta['pmid']} DOI:{meta['doi']} PMCID:{meta['pmcid']}  \n"
              f"source: {meta['source']} · {len(paras)} paragraphs\n\n")
    lib_dir = ks.save_to_library(meta, paras, facts, header + ks.anchored_markdown(paras))

    store = ks.KnowledgeStore()
    try:
        items = store.add_paper(meta, paras, facts)
    except Exception as e:  # noqa: BLE001  索引失败不该让已落盘的 library 条目回滚
        emit({"type": "log", "level": "warning", "message": f"kb index failed for {meta['title'][:60]}: {e}"})
        items = 0

    _stage("kb", "finished", n_facts=len(facts), items=items)
    return IngestResult(key=os.path.basename(lib_dir), n_paragraphs=len(paras), n_facts=len(facts), items=items)
