#!/usr/bin/env python
"""Question -> literature search -> full-text download -> per-paper reading -> cited answer.

Usage:
    python ask.py "SGLT2抑制剂对HFpEF患者有什么获益？"
    python ask.py --years 3 --quartile Q1,Q2 "..."          # last 3 years, SCImago Q1/Q2 journals only
    python ask.py --year 2018-2023 --zone 1-3 "..."          # custom year range, 一区~三区
    python ask.py --journal "Nature,Lancet,JAMA" "..."       # journal-family filter (e.g. Nature 子刊)
    python ask.py --papers 8 --no-kb "..."                   # skip atomic-fact extraction / vector indexing
Outputs:
    answers/<ts>.md              answer with [n] / [n¶k] citations + "原文定位" appendix
    answers/<ts>_papers/         per paper: <pmid>.md (anchored full text), _notes.md, _facts.json, _citations.json
    library/<pmid>/              persistent copy (fulltext.md, paragraphs.json, facts.json, meta.json)
    kb/                          vector store of atomic facts + paragraphs (python knowledge_store.py search "...")
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import time
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx

import journal_rank as jr
import knowledge_store as ks
import picos_paths
import literature as lit
from picos_paths import ANSWERS_DIR

try:
    import paywall_fetch  # noqa: E402  institutional-access downloader (SPIDER_PROJECT login state)
except Exception:  # noqa: BLE001  (playwright missing etc.)
    paywall_fetch = None
PAYWALL_STATE = picos_paths.PAYWALL_STATE
PAYWALL_MAX = int(os.environ.get("PAYWALL_MAX_PER_RUN", "5"))

LLM_BASE = os.environ.get("LLM_BASE", "http://127.0.0.1:4000/v1")
LLM_KEY = os.environ.get("LOCAL_QWEN_KEY", "sk-123456")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3-14b")
UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "picoseek@example.com")


# ------------------------------------------------------------------ 事件与异常
Emit = Callable[[dict], None]


def print_emit(event: dict) -> None:
    """CLI 的事件消费者：把结构化事件打回原来的 stdout 行格式。"""
    kind = event.get("type")
    if kind == "log":
        print(f"[{dt.datetime.now():%H:%M:%S}] {event['message']}", flush=True)
    elif kind == "stage":
        print(f"[{dt.datetime.now():%H:%M:%S}] {event['stage']} {event['status']}", flush=True)
    elif kind == "progress":
        print(f"[{dt.datetime.now():%H:%M:%S}]   {event['stage']} {event['current']}/{event['total']} "
              f"PMID:{event.get('pmid') or ''}", flush=True)


class PipelineError(Exception):
    """流水线的可预期失败：调用方据 .code 决定对外错误码。"""

    code = "internal_error"


class NoPapers(PipelineError):
    code = "no_papers"


class NothingRelevant(PipelineError):
    code = "nothing_relevant"


class LLMUnavailable(PipelineError):
    code = "llm_unavailable"


class PipelineCancelled(Exception):
    """协作式取消：在阶段边界与逐篇边界抛出。"""


# ------------------------------------------------------------------ LLM
def llm(system: str, user: str, *, think: bool = False, temperature: float = 0.2,
        timeout: float | None = 600, emit: Emit = print_emit, operation: str = "llm") -> str:
    body = {
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": think},
    }
    last = ""
    for attempt in range(3):
        started = time.monotonic()
        metrics = {"stage": "kb" if operation == "facts" else operation, "operation": operation,
                   "request_id": uuid.uuid4().hex, "attempt": attempt + 1, "model": LLM_MODEL,
                   "finish_reason": None, "response_id": None, "prompt_tokens": None,
                   "completion_tokens": None, "reasoning_tokens": None, "cached_tokens": None,
                   "success": False}
        try:
            r = httpx.post(f"{LLM_BASE}/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {LLM_KEY}"},
                           timeout=httpx.Timeout(timeout, connect=30, write=30, pool=30))
            r.raise_for_status()
            payload = r.json()
            choice = payload["choices"][0]
            usage = payload.get("usage") or {}
            metrics.update(
                model=payload.get("model") or LLM_MODEL,
                response_id=payload.get("id"), finish_reason=choice.get("finish_reason"),
                prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            )
            if choice.get("finish_reason") == "length":
                raise LLMUnavailable("LLM output truncated at server context limit; no complete answer")
            txt = choice["message"]["content"] or ""
            txt = re.sub(r"<think>.*?(?:</think>|$)", "", txt, flags=re.S).strip()
            if not txt:
                raise LLMUnavailable("LLM returned an empty answer")
            metrics["success"] = True
            return txt
        except LLMUnavailable:
            # 相同预算重试无法修复截断；让任务明确失败，不能把空正文标为完成。
            raise
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__
            emit({"type": "log", "level": "warning", "message": f"LLM error ({last}); retry {attempt+1}"})
        finally:
            metrics["elapsed_s"] = round(time.monotonic() - started, 6)
            emit({"type": "log", "level": "info",
                  "message": "llm_metrics " + json.dumps(metrics, ensure_ascii=False)})
        time.sleep(3)
    # 返回空串会让后续阶段静默产出空答案；让调用方看到真实原因
    raise LLMUnavailable(f"LLM unavailable after 3 attempts: {last}")


# ------------------------------------------------------------------ filters
class Filters:
    """Year range / journal quartile / journal-name filters (Google-Scholar-like)."""

    def __init__(self, years: int = 0, year: str = "", quartile: str = "", journal: str = "", keep_unranked: bool = False):
        self.y0 = self.y1 = 0
        now = dt.date.today().year
        if year:
            m = re.fullmatch(r"\s*(\d{4})\s*(?:[-~至:]\s*(\d{4}))?\s*", year)
            if not m:
                raise ValueError(f"year must be YYYY or YYYY-YYYY, got {year!r}")
            self.y0, self.y1 = int(m.group(1)), int(m.group(2) or m.group(1))
        elif years:
            self.y0, self.y1 = now - years + 1, now
        self.zones = jr.parse_quartile_arg(quartile)
        self.journals = [j.strip().lower() for j in re.split(r"[,，;]", journal) if j.strip()]
        self.keep_unranked = keep_unranked
        self.dropped: dict[str, int] = {"year": 0, "quartile": 0, "unranked": 0, "journal": 0}
        self.candidates = 0      # search_all 填：去重后的候选数
        self.kept = 0            # search_all 填：过滤后剩余数（未截断到 --papers）

    @property
    def active(self) -> bool:
        return bool(self.y0 or self.zones or self.journals)

    def describe(self) -> str:
        parts = []
        if self.y0:
            parts.append(f"年份 {self.y0}-{self.y1}")
        if self.zones:
            parts.append("分区 " + "/".join(f"Q{z}" for z in sorted(self.zones)) + ("（含未收录）" if self.keep_unranked else ""))
        if self.journals:
            parts.append("期刊含 " + "|".join(self.journals))
        return "; ".join(parts) or "无"

    def pubmed_year_params(self) -> dict:
        return {"mindate": str(self.y0), "maxdate": str(self.y1), "datetype": "pdat"} if self.y0 else {}

    def epmc_year_clause(self) -> str:
        return f" AND PUB_YEAR:[{self.y0} TO {self.y1}]" if self.y0 else ""

    def annotate(self, p: dict) -> None:
        info = jr.lookup(issn=p.get("issn", ""), title=p.get("journal", ""))
        p["rank"] = info
        p["quartile"] = info["quartile"] if info else ""
        p["zone"] = info["zone"] if info else 0

    def keep(self, p: dict) -> bool:
        if self.y0:
            try:
                y = int(str(p.get("year", ""))[:4])
            except ValueError:
                y = 0
            if not (self.y0 <= y <= self.y1):
                self.dropped["year"] += 1
                return False
        if self.journals and not any(j in (p.get("journal") or "").lower() for j in self.journals):
            self.dropped["journal"] += 1
            return False
        if self.zones:
            if not p.get("rank"):
                if not self.keep_unranked:
                    self.dropped["unranked"] += 1
                    return False
            elif p["zone"] not in self.zones:
                self.dropped["quartile"] += 1
                return False
        return True


# ------------------------------------------------------------------ 1. queries
def make_queries(question: str, *, emit: Emit = print_emit) -> tuple[list[str], str]:
    sysmsg = ("You are a medical librarian. Convert the user's question into PubMed search queries. "
              "Return ONLY JSON: {\"english_question\": str, \"queries\": [str, str, str]}. "
              "Queries must be English, 3-8 words, use synonyms/MeSH-like terms, no boolean operators, no quotes.")
    out = llm(sysmsg, question, emit=emit, operation="queries")
    m = re.search(r"\{.*\}", out, re.S)
    try:
        js = json.loads(m.group(0))
        qs = [q.strip() for q in js.get("queries", []) if q.strip()][:3]
        return qs or [question], js.get("english_question", question)
    except Exception:  # noqa: BLE001
        return [question], question


# ------------------------------------------------------------------ 2. search
def epmc_search(query: str, limit: int, fulltext_only: bool, flt: Filters) -> list[dict]:
    q = query + (" AND HAS_FT:y AND OPEN_ACCESS:y" if fulltext_only else "") + flt.epmc_year_clause()
    try:
        r = httpx.get(f"{lit.EPMC}/search", params={"query": q, "format": "json", "pageSize": limit,
                                                     "resultType": "lite", "sort": "CITED desc"}, timeout=30)
        res = (r.json().get("resultList") or {}).get("result") or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in res:
        if a.get("source") != "MED":
            continue
        out.append({"pmid": a.get("pmid", ""), "pmcid": a.get("pmcid", ""), "doi": a.get("doi", ""),
                    "title": a.get("title", ""), "year": str(a.get("pubYear", "")), "journal": a.get("journalTitle", ""),
                    "issn": a.get("journalIssn", ""),
                    "authors": a.get("authorString", ""), "cited": int(a.get("citedByCount", 0) or 0),
                    "types": [], "abstract": ""})
    return out


def pubmed_search(query: str, limit: int, flt: Filters) -> list[dict]:
    params = {"db": "pubmed", "term": query, "retmax": limit, "sort": "relevance", "retmode": "json", **flt.pubmed_year_params()}
    try:
        r = lit.ncbi_get("esearch.fcgi", params)
        if r is None:
            return []
        ids = r.json().get("esearchresult", {}).get("idlist") or []
        recs = lit.pubmed_fetch_records(ids)
    except lit.UpstreamError:
        # 单个检索源不可用时保留其他来源的结果。
        return []
    return [{"pmid": p["pmid"], "pmcid": p["pmc"], "doi": p["doi"], "title": p["title"], "year": p["year"],
             "journal": p["journal"], "issn": p.get("issn", ""),
             "authors": ", ".join(p["authors"][:3]) + (" et al." if len(p["authors"]) > 3 else ""),
             "cited": 0, "types": p["types"], "abstract": p["abstract"]} for p in recs]


def rank_key(p: dict) -> tuple:
    t = " ".join(p.get("types", [])).lower()
    quality = 3 if "meta-analysis" in t or "systematic review" in t else 2 if "randomized" in t else 1 if "review" in t or "guideline" in t else 0
    zone_bonus = (5 - p["zone"]) if p.get("zone") else 0   # Q1 -> 4 ... Q4 -> 1, unknown -> 0
    return (quality, zone_bonus, 1 if p.get("pmcid") else 0, p.get("cited", 0), p.get("year", ""))


def search_all(queries: list[str], n_papers: int, flt: Filters, *, emit: Emit = print_emit) -> list[dict]:
    seen: dict[str, dict] = {}
    # a filter shrinks the pool, so fetch a bigger candidate set first
    n_pm, n_ep = (30, 25) if flt.active else (10, 8)
    for q in queries:
        for rec in pubmed_search(q, n_pm, flt) + epmc_search(q, n_ep, True, flt):
            key = rec["pmid"] or rec["doi"] or rec["title"]
            if key and key not in seen:
                seen[key] = rec
            elif key:
                cur = seen[key]
                for k in ("pmcid", "doi", "abstract", "types", "issn"):
                    if not cur.get(k) and rec.get(k):
                        cur[k] = rec[k]
                cur["cited"] = max(cur.get("cited", 0), rec.get("cited", 0))
    cands = list(seen.values())
    for p in cands:
        flt.annotate(p)
    kept = [p for p in cands if flt.keep(p)]
    flt.candidates, flt.kept = len(cands), len(kept)
    emit({"type": "log", "level": "info",
          "message": (f"candidates: {len(cands)} found, {len(kept)} pass filters (dropped: "
                      + ", ".join(f"{k} {v}" for k, v in flt.dropped.items() if v) + ")")
                     if flt.active else f"candidates: {len(cands)}"})
    papers = sorted(kept, key=rank_key, reverse=True)
    return papers[:n_papers]


# ------------------------------------------------------------------ 3. full text
def unpaywall_pdf(doi: str) -> str:
    if not doi:
        return ""
    try:
        r = httpx.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": UNPAYWALL_EMAIL}, timeout=20)
        if r.status_code == 200:
            loc = r.json().get("best_oa_location") or {}
            return loc.get("url_for_pdf") or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


SKIP_SECS = ("references", "associated data", "supporting information", "supplementary material", "acknowledgements",
             "acknowledgments", "funding", "conflict of interest", "competing interests", "author contributions")


def fetch_fulltext(p: dict, outdir: str, max_chars: int, *, emit: Emit = print_emit) -> dict:
    """Fill p['paras'] (numbered paragraphs), p['text'] (model-facing numbered text) and p['source']."""
    pmid = p["pmid"]
    paras: list[dict] = []
    source = "abstract"
    if p.get("pmcid"):
        try:
            secs = lit.epmc_fulltext_paragraphs(p["pmcid"])
        except lit.UpstreamError as exc:
            emit({"type": "log", "level": "warning", "message": str(exc)})
            secs = []
        if secs:
            keep = [(t, ps) for t, ps in secs if not any(t.lower().startswith(s) for s in SKIP_SECS)]
            paras = ks.paragraphs_from_sections(keep)
            source = "pmc" if paras else "abstract"
    if not paras:
        url = unpaywall_pdf(p.get("doi", ""))
        if url:
            fn = os.path.join(outdir, f"{pmid or 'paper'}.pdf")
            try:
                with httpx.stream("GET", url, follow_redirects=True, timeout=60,
                                  headers={"User-Agent": "Mozilla/5.0 PicoSeek-medlit"}) as r:
                    if r.status_code == 200 and "pdf" in r.headers.get("content-type", "").lower():
                        with open(fn, "wb") as f:
                            for chunk in r.iter_bytes():
                                f.write(chunk)
                        t = lit.pdf_text(fn, max_chars * 2)
                        if not t.startswith("ERROR"):
                            paras = ks.paragraphs_from_pdf_text(t)
                            source = "pdf" if paras else "abstract"
            except Exception:  # noqa: BLE001
                pass
    if not paras and p.get("doi") and paywall_fetch and os.path.exists(PAYWALL_STATE) and p.get("_paywall_ok"):
        fn = os.path.join(outdir, f"{pmid or 'paper'}.pdf")
        ok, note = paywall_fetch.download_pdf(p["doi"], fn, PAYWALL_STATE)
        emit({"type": "log", "level": "info",
              "message": f"  institutional {'OK ' if ok else 'no '} DOI:{p['doi']} — {note}"})
        if ok:
            t = lit.pdf_text(fn, max_chars * 2)
            if not t.startswith("ERROR") and len(t) > 2000:
                paras = ks.paragraphs_from_pdf_text(t)
                source = "inst" if paras else "abstract"
    if not paras:
        if not p.get("abstract") and pmid:
            try:
                recs = lit.pubmed_fetch_records([pmid])
            except lit.UpstreamError as exc:
                emit({"type": "log", "level": "warning", "message": str(exc)})
                recs = []
            if recs:
                p["abstract"] = recs[0]["abstract"]
                p["types"] = recs[0]["types"]
                p["issn"] = p.get("issn") or recs[0].get("issn", "")
        paras = ks.paragraphs_from_abstract(p.get("abstract", ""))
        source = "abstract"
    # cap the model-facing text; keep the full paragraph list for locating / indexing
    text = ks.numbered_text(paras)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated: later paragraphs not shown to the reader model ...]"
    p["paras"], p["text"], p["source"] = paras, text, source
    p["md_file"] = f"{pmid or 'paper'}.md"
    header = (f"# {p['title']}\n\n{p['authors']} ({p['year']}) *{p['journal']}* {jr.label(p.get('rank'))}  \n"
              f"PMID:{pmid} DOI:{p.get('doi','')} PMCID:{p.get('pmcid','')}  \nsource: {source} · {len(paras)} paragraphs\n\n")
    p["fulltext_md"] = header + ks.anchored_markdown(paras)
    with open(os.path.join(outdir, p["md_file"]), "w", encoding="utf-8") as f:
        f.write(p["fulltext_md"])
    # 段落单独落盘：--no-kb 时 library/ 里不会有这篇，answers/<id>_papers/ 必须自包含
    with open(os.path.join(outdir, f"{pmid or 'paper'}_paragraphs.json"), "w", encoding="utf-8") as f:
        json.dump(paras, f, ensure_ascii=False, indent=1)
    return p


# ------------------------------------------------------------------ 4. read
READ_SYS = """You are a meticulous clinical research analyst. You will be given ONE paper (full text or abstract,
already converted to plain text; every paragraph is prefixed with a number like [¶12]) and a research question.
Extract ONLY what the paper itself reports.
Return Markdown with EXACTLY these headings, following the PICOS framework:
### Relevance (0-3)
### P — Patient / 研究对象  (population, disease/condition, inclusion & exclusion criteria, age/sex, setting, sample size n per arm)
### I — Intervention / 干预措施  (drug/procedure/exposure, dose, frequency, route, duration)
### C — Comparator / 对照方式  (placebo / standard care / active control / no treatment; dose & duration)
### O — Outcome / 结局指标  (primary and secondary outcomes with exact numbers: HR/RR/OR with 95% CI, p values,
    absolute event rates, mean differences; follow-up time; adverse events)
### S — Study design / 研究设计  (RCT / meta-analysis / cohort / case-control / cross-sectional / review; blinding,
    randomization, number of centres, registration, follow-up duration)
### Key findings relevant to the question
    Bullets. EVERY bullet must end with its source location in this exact form:  (¶12: "8-20 verbatim words copied from that paragraph")
    Use the paragraph number shown in the text; quote must be copied exactly, no paraphrase.
### Limitations stated by the authors  (each bullet also ends with (¶n: "verbatim quote"))
Rules: never add information that is not in the text; write "Not reported" for any PICOS item the paper does not state;
if the paper is not relevant say Relevance 0 and stop."""


# 分值有三种写法：标题行之后（`### Relevance (0-3)` 换行 `2 — …`）、标题行内（`### Relevance (2)`），
# 以及 READ_SYS 末句要求的裸写法（`Relevance 0`，不相关时模型就此收尾）。
# 三者都必须跳过标题里的取值范围：直接取 `Relevance` 之后的第一个数字会把范围下限 0 当成分值，
# 而 READ_SYS 要求模型逐字照抄该标题，于是每篇都判为不相关，整轮问答以 nothing_relevant 失败。
_NOT_A_RANGE = r"\b(?!\s*[-–—]\s*\d)"
REL_PATTERNS = (
    re.compile(r"Relevance[^\n]*?(?::|\n)\s*\**\s*([0-3])" + _NOT_A_RANGE),
    re.compile(r"Relevance\s*[:(]\s*\**\s*([0-3])" + _NOT_A_RANGE),
    re.compile(r"Relevance\s+\**\s*([0-3])" + _NOT_A_RANGE),
)


def parse_relevance(notes: str) -> int:
    """取阅读笔记里的 0-3 分值；实在解析不出时按 1 处理，宁可多读一篇也不误杀证据。"""
    for pat in REL_PATTERNS:
        m = pat.search(notes)
        if m:
            return int(m.group(1))
    return 1


def read_paper(i: int, p: dict, question_en: str, *, emit: Emit = print_emit) -> dict:
    if not p.get("text"):
        p["notes"] = "### Relevance (0-3)\n0 (no text available)"
        p["relevance"] = 0   # 不显式写 0 的话，下游 p.get("relevance", 1) 会把无正文的文献当成相关
        p["cites"] = []
        return p
    user = f"QUESTION: {question_en}\n\nPAPER [{i}] {p['title']} ({p['year']}, {p['journal']}) — source: {p['source']}\n\n{p['text']}"
    p["notes"] = llm(READ_SYS, user, emit=emit, operation="read")
    p["relevance"] = parse_relevance(p["notes"])
    p["cites"] = ks.verify_citations(p["notes"], p["paras"])
    # rewrite the notes so the synthesis model sees corrected/verified paragraph ids: (¶12: "...") -> [n¶12]
    def _fix(m: re.Match) -> str:
        claimed, quote = int(m.group(1)), m.group(2).strip()
        c = next((c for c in p["cites"] if c["claimed_pid"] == claimed and c["quote"] == quote), None)
        if c and c["verified"]:
            return f'[{i}¶{c["pid"]}]'
        return f"[{i}¶{claimed}?]"
    p["notes_for_synthesis"] = ks.CITE_RE.sub(_fix, p["notes"])
    return p


# ------------------------------------------------------------------ 5. synthesize
SYN_SYS = """You are a medical literature assistant writing an evidence summary for a clinician-researcher.
You are given PICOS reading notes for several papers, each labelled [n]. Inside the notes, markers like [3¶12] mean
"paper 3, paragraph 12" — a verified location in the original text. Write the answer in the SAME LANGUAGE as the
user's question, using ONLY facts from the notes. Cite every factual claim. PREFER the paragraph markers over plain [n]:
whenever the note line you are using carries a marker like [3¶12], cite [3¶12] (copy it exactly as given; never invent
paragraph numbers); use plain [n] only for facts whose note line has no marker. Every bracket holds exactly ONE marker
copied verbatim from the notes: write [3¶12] [3¶14], never ranges or bundles like [3¶12-¶14] or [3¶12, 3¶14].
Do not invent numbers, studies or citations; if the notes are insufficient, say what is missing.
Format:
**结论 / Bottom line** — 2-4 sentences.
**证据 / Evidence** — bullets, each with study type, n, effect sizes, ending with [n] or [n¶k].
**PICOS 证据表 / PICOS table** — a Markdown table with columns: [n] | P 研究对象 | I 干预措施 | C 对照方式 | O 结局指标 | S 研究设计.
One row per paper with Relevance >= 1; keep each cell concise (<= 25 words); write "未报告" if not reported.
**局限 / Caveats** — bullets.
Do NOT write the reference list; it will be appended automatically."""


def synthesize(question: str, papers: list[dict], *, emit: Emit = print_emit) -> str:
    notes = "\n\n".join(f"[{p['n']}] {p['title']} ({p['year']}) — {p['journal']} {jr.label(p.get('rank'))} — text source: {p['source']}\n"
                        f"{p.get('notes_for_synthesis') or p['notes']}" for p in papers)
    # 不设独立思考或输出额度，由服务端分配剩余上下文；生成期间不设读取超时。
    return llm(SYN_SYS, f"USER QUESTION: {question}\n\nREADING NOTES:\n{notes}",
               think=True, timeout=None, emit=emit, operation="synthesize")


def _short_authors(a: str) -> str:
    names = [x.strip() for x in a.replace(" et al.", "").split(",") if x.strip()]
    return ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")


def paper_link(p: dict, target: str | dict[int, str]) -> str:
    """CLI 使用相对文件路径，HTTP 使用本次阅读快照的资源 URL。"""
    return target[p["n"]] if isinstance(target, dict) else f"{target}/{p['md_file']}"


def ref_line(p: dict, papers_dir_rel: str | dict[int, str]) -> str:
    p["authors"] = _short_authors(p["authors"])
    ids = " ".join(x for x in [f"PMID:{p['pmid']}" if p["pmid"] else "", f"DOI:{p['doi']}" if p.get("doi") else "",
                               p.get("pmcid", "")] if x)
    src = {"pmc": "全文(PMC)", "pdf": "全文(OA PDF)", "inst": "全文(机构订阅)", "abstract": "仅摘要"}[p["source"]]
    rank = jr.label(p.get("rank"))
    return (f"[{p['n']}] {p['authors']} ({p['year']}). {p['title']} *{p['journal']}* 〔{rank}〕. {ids} 〔{src}〕 "
            f"[原文]({paper_link(p, papers_dir_rel)})")


# 正文标记词法：`3¶12`、`3¶12-¶14`（区间，破折号变体/第二个 ¶ 可省）、纯篇号 `3`；
# 结尾 `?` 是未核实引文的标记，按段落照常解析。
MARK_TOKEN_RE = re.compile(r"(\d{1,2})(?:¶(\d{1,4})(?:\s*[-–—~]\s*¶?(\d{1,4}))?)?\??")
MARK_GROUP_RE = re.compile(r"\[([^\[\]\n]{1,200})\]")
MARK_SEPS = " \t,，、;；"
MARK_RANGE_MAX = 8  # 区间跨度上限：更大的区间等于泛指全篇，降级为 [n]


def resolve_markers(body: str, by_n: dict[int, dict],
                    papers_dir_rel: str | dict[int, str] | None) -> tuple[str, list[tuple[int, int]]]:
    """归一化正文里的 [3¶12] / [3¶26, 3¶29] / [2¶4, 5] / [3¶1-¶5, 3¶3] 标记，并收集 (n, pid)。

    `papers_dir_rel` 为 None 时输出裸标记 `[n¶pid]`（HTTP 场景：前端自己决定跳哪儿）；
    给了相对目录或资源映射就输出指向本次原文段落锚点的 Markdown 链接。
    未知段落一律降级为 `[n]`；组内标记按词法逐个解析，一个畸形写法不再连坐整组。
    """
    used: list[tuple[int, int]] = []

    def _mark(n: int, pid: int) -> str | None:
        """已知段落 → 标记/链接；未知段落 → None，由调用方降级。"""
        p = by_n.get(n)
        if not p or not any(q["id"] == pid for q in p["paras"]):
            return None
        if (n, pid) not in used:
            used.append((n, pid))
        if papers_dir_rel is None:
            return f"[{n}¶{pid}]"
        return f"[{n}¶{pid}]({paper_link(p, papers_dir_rel)}#p{pid})"

    def _lex(content: str) -> list[re.Match] | None:
        """整组词法校验：只由标记 token 与分隔符组成才返回 token 列表，夹杂正文返回 None。

        必须先整组校验再解析：`_token` 会往 `used` 里登记段落，边扫边解析会让
        `[1¶1 说明]` 这类要原样保留的括号污染引用清单（下游误标 from_marker）。
        """
        toks: list[re.Match] = []
        pos = 0
        for tok in MARK_TOKEN_RE.finditer(content):
            if content[pos:tok.start()].strip(MARK_SEPS):
                return None
            toks.append(tok)
            pos = tok.end()
        return toks if toks and not content[pos:].strip(MARK_SEPS) else None

    def _token(m: re.Match) -> str:
        n = int(m.group(1))
        if m.group(2) is None:
            return f"[{n}]"
        lo, hi = int(m.group(2)), int(m.group(3) or m.group(2))
        if hi < lo or hi - lo + 1 > MARK_RANGE_MAX:
            return f"[{n}]"
        marks = [s for pid in range(lo, hi + 1) if (s := _mark(n, pid))]
        return " ".join(marks) if marks else f"[{n}]"

    def _group(m: re.Match) -> str:
        content = m.group(1)
        if "¶" not in content:  # [4] / [2024]：不是段落引用组，原样保留
            return m.group(0)
        toks = _lex(content)
        if toks is None:
            return m.group(0)
        parts: list[str] = []
        for tok in toks:
            for part in _token(tok).split():
                if part not in parts:  # 同一降级篇号只留一个 [n]
                    parts.append(part)
        return " ".join(parts)
    return MARK_GROUP_RE.sub(_group, body), used


def cited_passages(used: list[tuple[int, int]], by_n: dict[int, dict]) -> list[dict]:
    """把「正文引到的段落」摊平成结构化清单（附录渲染与 HTTP 响应共用同一份）。

    只被 `[n]` 引用的论文没有段落标记，此时兜底取该篇核实过的 key-finding 段落，
    保证每篇都能定位到原文。`from_marker` 区分这两种来源。
    """
    by_paper: dict[int, list[int]] = {}
    for n, pid in used:
        by_paper.setdefault(n, [])
        if pid not in by_paper[n]:
            by_paper[n].append(pid)
    for n, p in by_n.items():
        if by_paper.get(n):
            continue
        vc = [c for c in p.get("cites", []) if c["verified"]]
        for c in ([c for c in vc if c.get("key_finding")] + [c for c in vc if not c.get("key_finding")])[:3]:
            if c["pid"] not in by_paper.setdefault(n, []):
                by_paper[n].append(c["pid"])
    out: list[dict] = []
    marked = set(used)
    for n in sorted(by_paper):
        p = by_n[n]
        paras = {q["id"]: q for q in p["paras"]}
        for pid in sorted(by_paper[n]):
            q = paras.get(pid)
            if not q:
                continue
            out.append({"n": n, "pmid": p.get("pmid", ""), "pid": pid, "sec": q["sec"], "page": q.get("page"),
                        "text": q["text"],
                        "quotes": [c["quote"] for c in p.get("cites", []) if c["verified"] and c["pid"] == pid],
                        "from_marker": (n, pid) in marked})
    return out


def location_appendix(passages: list[dict], by_n: dict[int, dict], papers_dir_rel: str | dict[int, str]) -> str:
    """'原文定位' section: for every cited paragraph show where it is and the verified quote / paragraph text."""
    lines = ["**原文定位 / Source passages**（点击 ¶ 链接可跳到原文段落；完整核实清单见各篇 `_citations.json`）"]

    def _warn(n: int) -> None:
        bad = [c for c in by_n[n].get("cites", []) if not c["verified"]]
        if bad:
            lines.append(f"- ⚠ {len(bad)} 条模型引文未能在原文中核实（见 {by_n[n]['pmid'] or 'paper'}_citations.json）")

    cur: int | None = None
    for it in passages:
        n = it["n"]
        p = by_n[n]
        if n != cur:
            if cur is not None:
                _warn(cur)
            cur = n
            lines.append(f"\n[{n}] {p['title'][:100]} — {p['journal']} ({p['year']}) 〔{p['source']}〕")
        loc = it["sec"] + (f", p.{it['page']}" if it.get("page") else "")
        link = f"{paper_link(p, papers_dir_rel)}#p{it['pid']}"
        if it["quotes"]:
            for qu in it["quotes"][:2]:
                lines.append(f"- [¶{it['pid']}]({link}) {loc}: “{qu}”")
        else:
            lines.append(f"- [¶{it['pid']}]({link}) {loc}: {it['text'][:220]}{'…' if len(it['text']) > 220 else ''}")
    if cur is not None:
        _warn(cur)
    return "\n".join(lines)


# ------------------------------------------------------------------ 6. pipeline
@dataclass(frozen=True)
class AskOptions:
    question: str
    papers: int = 8
    max_chars: int = 28000
    workers: int = 4
    use_paywall: bool = True
    years: int = 0
    year: str = ""
    quartile: str = ""
    journal: str = ""
    keep_unranked: bool = False
    use_kb: bool = True
    kb_hits: int = 0


@dataclass
class AskResult:
    run_id: str
    question: str
    question_en: str
    queries: list[str]
    filters_label: str
    papers: list[dict]            # 流水线内部的 paper dict（含 paras/cites/facts 等中间产物）
    used_n: list[int]             # relevance > 0 的编号
    body_md: str                  # 综合稿正文，引用为裸标记 [n¶pid]
    answer_md: str                # 完整渲染稿（含链接、参考文献、原文定位），即 answers/<id>.md
    citations: list[dict]
    kb_hits: list[dict]
    out_path: str
    papers_dir: str
    n_fulltext: int


_KB_META_FIELDS = ("pmid", "doi", "pmcid", "title", "year", "journal", "issn",
                   "quartile", "authors", "source", "types")


def _atomic_json(path: str, value: object) -> None:
    """同目录写完并同步后替换，恢复只会读到完整版本。"""
    fd, temporary = tempfile.mkstemp(prefix=".kb-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=1, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        directory = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_kb_snapshot(papers_dir: str, question_en: str, papers: list[dict]) -> str:
    """保存入库输入快照（ask 与 interpret 共用）；不依赖答案行或可变 library 文件。"""
    rows = [{**{k: p.get(k) for k in _KB_META_FIELDS}, "n": p["n"],
             "paras": p["paras"], "fulltext_md": p["fulltext_md"]} for p in papers]
    path = os.path.join(papers_dir, "kb-input.json")
    _atomic_json(path, {"version": 1, "question_en": question_en, "papers": rows})
    return path


def save_kb_snapshot(res: AskResult) -> str:
    """保存所有候选论文的独立入库输入。"""
    return write_kb_snapshot(res.papers_dir, res.question_en, res.papers)


def _valid_facts(facts: object) -> bool:
    return isinstance(facts, list) and all(
        isinstance(f, dict) and isinstance(f.get("fact"), str) and bool(f["fact"].strip())
        and isinstance(f.get("verified"), bool)
        and (f.get("pid") is None or type(f["pid"]) is int)
        and isinstance(f.get("quote"), str)
        for f in facts
    )


def _extract_kb_facts(p: dict, question_en: str, outdir: str, *,
                      emit: Emit, should_cancel: Callable[[], bool]) -> list[dict]:
    def check() -> None:
        if should_cancel():
            raise PipelineCancelled()

    check()
    # 指纹绑定完整输入，旧答案/改过的段落/半写文件不能作为已完成抽取复用。
    identity = {"meta": {k: p.get(k) for k in _KB_META_FIELDS}, "paras": p["paras"],
                "fulltext_md": p["fulltext_md"], "question_en": question_en}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    checkpoint = os.path.join(outdir, f"kb-facts-{p['n']}.json")
    facts = None
    try:
        with open(checkpoint, encoding="utf-8") as f:
            cached = json.load(f)
        if (isinstance(cached, dict) and cached.get("version") == 1
                and cached.get("input_sha256") == digest and _valid_facts(cached.get("facts"))):
            facts = cached["facts"]
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    if facts is None:
        facts = (ks.extract_facts(p["paras"], lambda s, u: llm(s, u, emit=emit, operation="facts"),
                                  question_en) if p["paras"] else [])
        if not _valid_facts(facts):
            raise ValueError("invalid extracted facts")
        # 即使取消在请求期间到达，也先保留完整抽取结果，恢复无需再花一次模型请求。
        _atomic_json(checkpoint, {"version": 1, "input_sha256": digest, "facts": facts})
    check()
    return facts


def _index_kb_paper(p: dict, facts: list[dict], outdir: str, store: ks.KnowledgeStore, *,
                    emit: Emit, should_cancel: Callable[[], bool]) -> int:
    def check() -> None:
        if should_cancel():
            raise PipelineCancelled()

    check()
    p["facts"] = facts
    # 保留 CLI 既有的 facts 导出；恢复判定只信带输入指纹的 checkpoint。
    _atomic_json(os.path.join(outdir, f"{p['pmid'] or 'paper'}_facts.json"), facts)
    meta = {k: p.get(k) for k in _KB_META_FIELDS}
    meta.update({"indexed_at": dt.datetime.now().isoformat(timespec="seconds"),
                 "n_paragraphs": len(p["paras"]), "n_facts": len(facts)})
    p["library_dir"] = ks.save_to_library(meta, p["paras"], facts, p["fulltext_md"])
    check()
    # 索引失败必须交给后台任务重试；replace 默认语义保证重复执行不产生重复行。
    n_items = store.add_paper(meta, p["paras"], facts)
    emit({"type": "log", "level": "info",
          "message": f"  [{p['n']}] {len(facts)} facts + {len(p['paras'])} paragraphs -> kb ({n_items} items)"})
    return n_items


def index_kb_snapshot(snapshot_path: str, *, position: int, emit: Emit = print_emit,
                      should_cancel: Callable[[], bool] = lambda: False) -> dict:
    """一次只处理一篇；位置由 durable job 保存，重试可安全重放同一位置。"""
    if should_cancel():
        raise PipelineCancelled()
    with open(snapshot_path, encoding="utf-8") as f:
        snapshot = json.load(f)
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        raise ValueError("unsupported kb snapshot")
    papers = snapshot["papers"]
    if not isinstance(papers, list) or not isinstance(snapshot["question_en"], str):
        raise ValueError("invalid kb snapshot")
    if type(position) is not int or not 0 <= position < len(papers):
        raise ValueError("kb snapshot position out of range")
    p = papers[position]
    if (not isinstance(p, dict) or type(p.get("n")) is not int
            or not isinstance(p.get("paras"), list) or not isinstance(p.get("fulltext_md"), str)):
        raise ValueError("invalid kb snapshot paper")
    outdir = os.path.dirname(snapshot_path)
    facts = _extract_kb_facts(p, snapshot["question_en"], outdir,
                              emit=emit, should_cancel=should_cancel)
    items = _index_kb_paper(p, facts, outdir, ks.KnowledgeStore(),
                            emit=emit, should_cancel=should_cancel)
    emit({"type": "progress", "stage": "kb", "current": position + 1, "total": len(papers),
          "pmid": p.get("pmid"), "title": p.get("title")})
    return {"paper_count": len(papers), "next_position": position + 1, "items": items}


def run_ask(opts: AskOptions, *, run_id: str | None = None, emit: Emit = print_emit,
            should_cancel: Callable[[], bool] = lambda: False,
            paper_urls: dict[int, str] | None = None, defer_kb: bool = False) -> AskResult:
    """整条问答流水线。CLI 与 HTTP worker 共用；进度通过 `emit` 推出，取消通过 `should_cancel` 轮询。

    失败用异常表达（NoPapers / NothingRelevant / LLMUnavailable / PipelineCancelled），
    不再像 CLI 那样打印一行然后 return —— 调用方需要据此决定 HTTP 状态与任务终态。
    """
    def _check() -> None:
        if should_cancel():
            raise PipelineCancelled()

    def _stage(stage: str, status: str, **detail) -> None:
        emit({"type": "stage", "stage": stage, "status": status, "detail": detail})

    run_id = run_id or dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    flt = Filters(opts.years, opts.year, opts.quartile, opts.journal, opts.keep_unranked)
    tables = jr.load()
    if flt.zones and not tables:
        emit({"type": "log", "level": "warning",
              "message": "--quartile given but no ranking table in data/journal_ranks/ (run: python journal_rank.py download)"})
    papers_dir_rel = f"{run_id}_papers"
    outdir = os.path.join(ANSWERS_DIR, papers_dir_rel)
    os.makedirs(outdir, exist_ok=True)

    emit({"type": "log", "level": "info", "message": f"Q: {opts.question}"})
    emit({"type": "log", "level": "info",
          "message": f"filters: {flt.describe()}" + (f"   ranking tables: {', '.join(tables)}" if tables else "")})

    _check()
    _stage("queries", "started")
    queries, q_en = make_queries(opts.question, emit=emit)
    emit({"type": "log", "level": "info", "message": f"queries: {queries}"})
    _stage("queries", "finished", queries=queries, question_en=q_en)

    _check()
    _stage("search", "started", queries=queries)
    papers = search_all(queries, opts.papers, flt, emit=emit)
    for i, p in enumerate(papers, 1):
        p["n"] = i
    for p in papers:
        emit({"type": "log", "level": "info",
              "message": (f"  - {p['title'][:80]} ({p['year']}) {p['journal'][:30]} "
                          f"〔{jr.label(p.get('rank'))}〕 PMID:{p['pmid']} {'PMC✔' if p.get('pmcid') else ''}")})
    _stage("search", "finished", candidates=flt.candidates, kept=flt.kept, dropped=dict(flt.dropped),
           papers=[{"n": p["n"], "pmid": p["pmid"], "title": p["title"], "year": p["year"],
                    "journal": p["journal"], "rank_label": jr.label(p.get("rank")), "pmcid": p.get("pmcid", "")}
                   for p in papers])
    if not papers:
        raise NoPapers("no papers pass the filters; relax years/quartile/journal or keep unranked journals")

    _check()
    _stage("fulltext", "started", total=len(papers))
    emit({"type": "log", "level": "info", "message": "downloading full texts (open access) ..."})
    with cf.ThreadPoolExecutor(opts.workers) as ex:
        futs = {ex.submit(fetch_fulltext, p, outdir, opts.max_chars, emit=emit): p for p in papers}
        for k, fut in enumerate(cf.as_completed(futs), 1):
            p = futs[fut]
            fut.result()
            emit({"type": "progress", "stage": "fulltext", "current": k, "total": len(papers),
                  "pmid": p["pmid"], "title": p["title"]})
            _check()
    if paywall_fetch and os.path.exists(PAYWALL_STATE) and opts.use_paywall:
        todo = [p for p in papers if p["source"] == "abstract" and p.get("doi")][:PAYWALL_MAX]
        if todo:
            emit({"type": "log", "level": "info",
                  "message": f"institutional access: trying {len(todo)} paywalled paper(s) serially (polite delay) ..."})
            for p in todo:  # serial on purpose: one browser at a time, delay between requests
                _check()
                p["_paywall_ok"] = True
                fetch_fulltext(p, outdir, opts.max_chars, emit=emit)
    elif opts.use_paywall:
        emit({"type": "log", "level": "info",
              "message": "institutional access: skipped (no sd_state.json — run paywall_fetch.py login to enable)"})
    for p in papers:
        emit({"type": "log", "level": "info",
              "message": f"  {p['source']:8s} {len(p['paras']):4d} paragraphs {len(p['text']):6d} chars  PMID:{p['pmid']}"})
    n_fulltext = sum(p["source"] != "abstract" for p in papers)
    _stage("fulltext", "finished", fulltext=n_fulltext, total=len(papers),
           sources={p["n"]: p["source"] for p in papers})

    _check()
    _stage("read", "started", total=len(papers))
    emit({"type": "log", "level": "info", "message": "reading papers with the model ..."})
    with cf.ThreadPoolExecutor(opts.workers) as ex:
        futs = {ex.submit(read_paper, p["n"], p, q_en, emit=emit): p for p in papers}
        for k, fut in enumerate(cf.as_completed(futs), 1):
            p = futs[fut]
            fut.result()
            emit({"type": "progress", "stage": "read", "current": k, "total": len(papers),
                  "pmid": p["pmid"], "title": p["title"]})
            _check()
    for p in papers:
        with open(os.path.join(outdir, f"{p['pmid'] or 'paper'}_notes.md"), "w", encoding="utf-8") as f:
            f.write(p["notes"])
        with open(os.path.join(outdir, f"{p['pmid'] or 'paper'}_citations.json"), "w", encoding="utf-8") as f:
            json.dump(p.get("cites", []), f, ensure_ascii=False, indent=1)
        nv = sum(c["verified"] for c in p.get("cites", []))
        emit({"type": "log", "level": "info",
              "message": (f"  [{p['n']}] relevance {p.get('relevance', '?')}  "
                          f"quotes verified {nv}/{len(p.get('cites', []))}  PMID:{p['pmid']}")})
    used = [p for p in papers if p.get("relevance", 1) > 0]
    emit({"type": "log", "level": "info", "message": f"relevant papers: {len(used)}/{len(papers)}"})
    _stage("read", "finished", relevant=len(used), total=len(papers))

    if not used:
        raise NothingRelevant("nothing relevant found in the retrieved papers")

    store = None
    if opts.use_kb and not defer_kb:
        _check()
        _stage("kb", "started", total=len(papers))
        emit({"type": "log", "level": "info", "message": "extracting atomic knowledge + indexing into kb/ ..."})
        store = ks.KnowledgeStore()
        facts_by_n: dict[int, list[dict]] = {}
        # CLI 保留原并发抽取；checkpoint 各篇独立，真正的索引写入仍串行。
        with cf.ThreadPoolExecutor(opts.workers) as ex:
            futs = {ex.submit(_extract_kb_facts, p, q_en, outdir,
                              emit=emit, should_cancel=should_cancel): p for p in papers}
            for k, fut in enumerate(cf.as_completed(futs), 1):
                p = futs[fut]
                facts_by_n[p["n"]] = fut.result()
                emit({"type": "progress", "stage": "kb", "current": k, "total": len(papers),
                      "pmid": p["pmid"], "title": p["title"]})
                _check()
        for p in papers:
            _index_kb_paper(p, facts_by_n[p["n"]], outdir, store,
                            emit=emit, should_cancel=should_cancel)
        st = store.stats()
        emit({"type": "log", "level": "info",
              "message": f"kb now holds {st['items']} items from {st['papers']} papers (embedder: {st.get('embedder')})"})
        _stage("kb", "finished", items=st["items"], papers=st["papers"])

    if opts.use_kb and opts.kb_hits and store is None:
        store = ks.KnowledgeStore()

    _check()
    _stage("synthesize", "started", papers=len(used))
    emit({"type": "log", "level": "info", "message": "synthesizing answer ..."})
    by_n = {p["n"]: p for p in used}
    raw_body = synthesize(opts.question, used, emit=emit)
    body_md, used_marks = resolve_markers(raw_body, by_n, None)
    link_target = paper_urls if paper_urls is not None else papers_dir_rel
    linked_body, _ = resolve_markers(raw_body, by_n, link_target)
    citations = cited_passages(used_marks, by_n)
    refs = "\n".join(ref_line(p, link_target) for p in used)
    n_full = sum(p["source"] != "abstract" for p in used)
    kb_note, hits = "", []
    if store is not None and opts.kb_hits:
        hits = store.search(q_en, top_k=opts.kb_hits, kind="fact", pmids=None)
        hits = [h for h in hits if h.get("pmid") not in {p["pmid"] for p in papers}]
        if hits:
            kb_note = "\n\n**知识库相关事实（来自以往检索，未纳入本次综合）**\n" + "\n".join(
                f"- {h['text']} — {h.get('title', '')[:80]} ({h.get('year')}) PMID:{h.get('pmid')} ¶{h.get('pid')}" for h in hits)
    answer = (f"# Q: {opts.question}\n\n筛选条件：{flt.describe()}　|　阅读 {len(used)} 篇（{n_full} 篇全文）\n\n{linked_body}\n\n"
              f"**参考文献 / References**（{n_full}/{len(used)} 篇读了全文）\n{refs}\n\n"
              f"{location_appendix(citations, by_n, link_target)}{kb_note}\n\n"
              f"*This is a literature summary for research/educational use, not medical advice.*\n")
    out = os.path.join(ANSWERS_DIR, f"{run_id}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(answer)
    emit({"type": "log", "level": "info",
          "message": f"saved: {out}\nfull texts + notes + facts: {outdir}"})
    _stage("synthesize", "finished", chars=len(answer))
    return AskResult(run_id=run_id, question=opts.question, question_en=q_en, queries=queries,
                     filters_label=flt.describe(), papers=papers, used_n=[p["n"] for p in used],
                     body_md=body_md, answer_md=answer, citations=citations, kb_hits=hits,
                     out_path=out, papers_dir=outdir, n_fulltext=n_full)


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--papers", type=int, default=8, help="max papers to read")
    ap.add_argument("--max-chars", type=int, default=28000, help="max chars of text fed per paper")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-paywall", action="store_true", help="skip institutional-access downloads")
    g = ap.add_argument_group("filters (Google-Scholar-like)")
    g.add_argument("--years", type=int, default=0, help="only papers from the last N years (e.g. 3)")
    g.add_argument("--year", default="", help="custom range YYYY-YYYY or a single YYYY (overrides --years)")
    g.add_argument("--quartile", "--zone", dest="quartile", default="",
                   help="journal quartile / 分区: 'Q1,Q2', '1-3', '一区,二区' (SCImago SJR best quartile; add your own CAS table in data/journal_ranks/)")
    g.add_argument("--journal", default="", help="comma list of journal-name substrings, e.g. 'Nature,Lancet' (matches Nature 子刊 too)")
    g.add_argument("--keep-unranked", action="store_true", help="with --quartile, keep journals absent from the ranking tables")
    k = ap.add_argument_group("knowledge base")
    k.add_argument("--no-kb", action="store_true", help="skip atomic-fact extraction and vector indexing")
    k.add_argument("--kb-hits", type=int, default=0, help="also feed the top-N knowledge-base hits from earlier runs into the synthesis (0 = off)")
    args = ap.parse_args()

    try:
        opts = AskOptions(question=args.question, papers=args.papers, max_chars=args.max_chars,
                          workers=args.workers, use_paywall=not args.no_paywall, years=args.years,
                          year=args.year, quartile=args.quartile, journal=args.journal,
                          keep_unranked=args.keep_unranked, use_kb=not args.no_kb, kb_hits=args.kb_hits)
        res = run_ask(opts)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    except PipelineError as e:
        print(str(e), flush=True)
        raise SystemExit(1) from e
    print("\n" + "=" * 80 + "\n" + res.answer_md)


if __name__ == "__main__":
    main()
