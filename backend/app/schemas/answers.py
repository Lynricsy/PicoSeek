"""问答任务的入参与结果模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .common import JobError, UtcDateTime
from .events import ToolCall
from .kb import KbHit
from .papers import Fact, Paragraph, VerifiedQuote

AnswerStatus = Literal["queued", "running", "ready", "failed", "cancelled"]
AnswerEngine = Literal["ask", "codex", "interpret"]
# JSON 入口不接受 interpret：文献解读必须带文件，只能走 multipart 的 /answers/interpret
CreateEngine = Literal["ask", "codex"]
TERMINAL_ANSWER_STATUSES = frozenset({"ready", "failed", "cancelled"})


def _strip(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("question must not be blank")
    return v


class AnswerCreate(BaseModel):
    """创建问答任务。年份既可用「最近 N 年」也可用明确区间，但不能同时给。

    `engine="ask"` 走确定性流水线（检索 → 全文 → 逐篇读 → 综合），全部字段生效；
    `engine="codex"` 交给 Codex agent 自己决定调哪些 MCP 工具，过滤条件会翻成检索
    要求写进提问，`max_chars` / `use_paywall` / `keep_unranked` / `kb_hits` 不生效。
    """

    question: str = Field(min_length=1, max_length=2000)
    engine: CreateEngine = "ask"
    papers: int = Field(default=8, ge=1, le=30)
    years: int | None = Field(default=None, ge=1, le=50)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    quartiles: list[int] = []
    journals: list[str] = []
    keep_unranked: bool = False
    use_paywall: bool = True
    use_kb: bool = True
    kb_hits: int = Field(default=0, ge=0, le=20)
    max_chars: int = Field(default=28000, ge=4000, le=60000)

    _strip_question = field_validator("question")(staticmethod(_strip))

    @field_validator("quartiles")
    @classmethod
    def _check_quartiles(cls, v: list[int]) -> list[int]:
        for z in v:
            if z not in (1, 2, 3, 4):
                raise ValueError("quartiles must be within 1..4")
        return sorted(set(v))

    @field_validator("journals")
    @classmethod
    def _check_journals(cls, v: list[str]) -> list[str]:
        out = []
        for j in v:
            j = j.strip()
            if not j or len(j) > 100:
                raise ValueError("each journal filter must be 1..100 chars after stripping")
            out.append(j)
        return out

    @model_validator(mode="after")
    def _check_years(self) -> AnswerCreate:
        if self.years is not None and self.year_from is not None:
            raise ValueError("years and year_from are mutually exclusive")
        if self.year_to is not None and self.year_from is None:
            raise ValueError("year_to requires year_from")
        if self.year_from is not None and self.year_to is not None and self.year_to < self.year_from:
            raise ValueError("year_to must be >= year_from")
        return self


class AnswerPaper(BaseModel):
    n: int
    pmid: str = ""
    doi: str = ""
    pmcid: str = ""
    title: str = ""
    year: str = ""
    journal: str = ""
    issn: str = ""
    authors: str = ""
    quartile: str = ""
    rank_label: str = ""
    source: Literal["pmc", "pdf", "inst", "upload", "abstract"] = "abstract"
    relevance: int | None = None
    n_paragraphs: int = 0
    n_citations: int = 0
    n_citations_verified: int = 0


class AnswerPaperDetail(AnswerPaper):
    notes_md: str = ""
    citations: list[VerifiedQuote] = []
    facts: list[Fact] = []
    paragraphs: list[Paragraph] = []
    fulltext_md: str = ""


class Citation(BaseModel):
    """答案正文引到的一个原文段落。"""

    n: int
    pmid: str = ""
    pid: int
    sec: str = ""
    page: int | None = None
    text: str
    quotes: list[str] = []
    from_marker: bool = True


class FollowupCreate(BaseModel):
    """在已有 codex 会话上追问；其余选项一律沿用被追问的那一轮。"""

    question: str = Field(min_length=1, max_length=2000)

    _strip_question = field_validator("question")(staticmethod(_strip))


class AnswerSummary(BaseModel):
    id: str
    job_id: str | None = None
    status: AnswerStatus
    question: str
    engine: AnswerEngine = "ask"
    filters_label: str | None = None
    n_papers: int | None = None
    n_fulltext: int | None = None
    created_at: UtcDateTime
    finished_at: UtcDateTime | None = None
    error: JobError | None = None
    # 会话折叠用：同一 thread 的回合数，以及本行是追问时的根问题
    n_turns: int = 1
    root_question: str | None = None

    model_config = {"from_attributes": True}


class Answer(AnswerSummary):
    question_en: str | None = None
    parent_id: str | None = None
    queries: list[str] = []
    options: dict = {}
    started_at: UtcDateTime | None = None
    papers: list[AnswerPaper] = []
    body_md: str | None = None
    citations: list[Citation] = []
    kb_hits: list[KbHit] = []
    trace: list[ToolCall] = []
