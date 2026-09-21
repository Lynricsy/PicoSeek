"""问答任务的创建与结果浏览。

创建走 202 + `Location`：流水线要跑几分钟（检索 → 下全文 → 逐篇读 → 综合），
不可能同步返回。answers 行在入队前就建好，`id` 同时是流水线的 run_id，
于是 `answers/<id>.md` 与 `answers/<id>_papers/` 的命名与 HTTP 资源一一对应。
"""
from __future__ import annotations

import logging
import os
import shutil
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from interpret import OVERVIEW_QUESTION

from ..auth import Principal, require
from ..config import settings
from ..deps import get_arq, get_db
from ..errors import ApiError
from ..export.answer_pdf import build_answer_html, export_filename, render_pdf
from ..models import Answer as AnswerRow
from ..schemas.answers import (
    TERMINAL_ANSWER_STATUSES,
    Answer,
    AnswerCreate,
    AnswerPaperDetail,
    AnswerStatus,
    AnswerSummary,
    FollowupCreate,
)
from ..schemas.common import MarkdownResponse, Page, PdfResponse
from ..services import interpret as interpret_service
from ..services import jobs as jobs_service
from ..services import papers as papers_service
from ..services.answers import (
    answer_paths,
    detail,
    http_answer_markdown,
    paper_artifact_path,
    paper_stems,
    read_json,
    summaries,
    thread_rows,
)

router = APIRouter(tags=["answers"])
logger = logging.getLogger(__name__)


def _row(db: Session, answer_id: str, principal: Principal) -> AnswerRow:
    row = db.get(AnswerRow, answer_id)
    if row is None or not (principal.is_admin or row.user_id == principal.user_id):
        raise ApiError(404, "not_found", f"answer {answer_id!r} not found")
    return row


def _interpret_paper_path(row: AnswerRow, n: int, suffix: str = ".md") -> str:
    """文献解读的原文快照在 docset 目录里，编号上界来自创建时记录的文件数。"""
    if not row.thread_id or not 1 <= n <= int((row.options or {}).get("n_files") or 0):
        raise ApiError(404, "not_found", f"answer {row.id!r} has no paper #{n}")
    return interpret_service.paper_path(row.thread_id, n, suffix)


@router.post("/answers", status_code=202, response_model=Answer, summary="创建问答任务")
async def create_answer(payload: AnswerCreate, response: Response,
                        db: Session = Depends(get_db), arq=Depends(get_arq),  # noqa: ANN001
                        principal: Principal = Depends(require())) -> Answer:
    # 这里必须是 async（enqueue 是协程）；DB 操作都是单行 sqlite 读写，毫秒级，
    # 放在事件循环里可以接受，长查询一律留在 def 路由里
    jobs_service.ensure_capacity(db, principal)
    options = payload.model_dump()
    answer_id = jobs_service.new_id()
    # 两个引擎共用 answers 资源，只有 worker 入口不同：ask 走确定性流水线，codex 交给 agent
    kind, fn_name = (("codex", "run_codex_job") if payload.engine == "codex"
                     else ("ask", "run_ask_job"))
    row = AnswerRow(id=answer_id, job_id=None, user_id=principal.user_id, status="queued",
                    question=payload.question, queries=[], options=options, papers=[],
                    citations=[], kb_hits=[])
    await jobs_service.enqueue(arq, db, kind=kind, params={"answer_id": answer_id, **options},
                               user_id=principal.user_id, fn_name=fn_name, answer=row)
    db.refresh(row)
    response.headers["Location"] = f"/v1/answers/{answer_id}"
    return detail(db, row)


@router.post("/answers/interpret", status_code=202, response_model=Answer,
             summary="上传 PDF 创建文献解读会话")
async def create_interpretation(response: Response,
                                files: list[UploadFile] = File(...),
                                question: str = Form("", max_length=2000),
                                db: Session = Depends(get_db), arq=Depends(get_arq),  # noqa: ANN001
                                principal: Principal = Depends(require())) -> Answer:
    """一组 PDF = 一条会话：docset id 就是 `thread_id`，后续追问复用同一份解析产物。

    多篇且带问题时一次入队两轮（先综述、后回答），arq 是 FIFO 且 worker 串行，
    所以第二轮读得到第一轮的历史。
    """
    question = question.strip()
    limit = settings.interpret_max_files
    if not files or len(files) > limit:
        raise ApiError(422, "validation_error", f"请上传 1..{limit} 个 PDF")
    if len(files) == 1 and not question:
        raise ApiError(422, "validation_error", "单篇解读需要提供问题")
    # 多篇 + 问题会一次占两个名额：整批判定，避免只入队半条会话
    turns = 2 if (len(files) >= 2 and question) else 1
    jobs_service.ensure_capacity(db, principal, n=turns)

    thread_id = jobs_service.new_id()
    directory = interpret_service.docset_dir(thread_id)
    os.makedirs(directory, exist_ok=True)
    manifest: list[dict] = []
    try:
        for i, upload in enumerate(files, 1):
            dest = os.path.join(directory, f"{i}.pdf")
            await papers_service.save_upload(upload, dest, settings.upload_max_mb)
            manifest.append({"n": i, "filename": os.path.basename(upload.filename or f"{i}.pdf")[:200],
                             "bytes": os.path.getsize(dest)})
        interpret_service.write_manifest(thread_id, manifest)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)   # 校验失败不留孤儿 docset
        raise

    label = interpret_service.filters_label(len(files))
    mode = "overview" if len(files) >= 2 else "qa"
    options = {"engine": "interpret", "mode": mode,
               "question": question if mode == "qa" else OVERVIEW_QUESTION,
               "n_files": len(files), "use_kb": True}
    row = AnswerRow(id=jobs_service.new_id(), job_id=None, user_id=principal.user_id,
                    status="queued", question=options["question"], queries=[], options=options,
                    papers=[], citations=[], kb_hits=[], trace=[],
                    thread_id=thread_id, filters_label=label)
    await jobs_service.enqueue(arq, db, kind="interpret",
                               params={"answer_id": row.id, **options},
                               user_id=principal.user_id, fn_name="run_interpret_job", answer=row)
    if turns == 2:
        # 综述写库、追问不再重复写：同一组 PDF 只入库一次
        options2 = {**options, "mode": "qa", "question": question, "use_kb": False}
        second = AnswerRow(id=jobs_service.new_id(), job_id=None, user_id=principal.user_id,
                           status="queued", question=question, queries=[], options=options2,
                           papers=[], citations=[], kb_hits=[], trace=[],
                           parent_id=row.id, thread_id=thread_id, filters_label=label)
        await jobs_service.enqueue(arq, db, kind="interpret",
                                   params={"answer_id": second.id, **options2},
                                   user_id=principal.user_id, fn_name="run_interpret_job",
                                   answer=second)
    db.refresh(row)
    response.headers["Location"] = f"/v1/answers/{row.id}"
    return detail(db, row)


@router.post("/answers/{answer_id}/followup", status_code=202, response_model=Answer,
             summary="在智能体或文献解读会话上追问")
async def followup_answer(answer_id: str, payload: FollowupCreate, response: Response,
                          db: Session = Depends(get_db), arq=Depends(get_arq),  # noqa: ANN001
                          principal: Principal = Depends(require())) -> Answer:
    """在同一个 thread 上再跑一轮；选项沿用被续接的那一轮，只换问题。"""
    row = _row(db, answer_id, principal)
    if row.user_id != principal.user_id:
        # 管理员看得到别人的答案，但会话是别人的：续下去会污染对方的历史
        raise ApiError(403, "forbidden", "只有发起人可以追问")
    if row.engine not in ("codex", "interpret"):
        raise ApiError(409, "conflict", "只有智能体或文献解读的答案支持追问")
    thread = thread_rows(db, row)
    if any(r.status in ("queued", "running") for r in thread):
        raise ApiError(409, "thread_busy", "上一轮还在进行中，稍后再追问")
    ready = [r for r in thread if r.status == "ready" and r.thread_id]
    if not ready:
        raise ApiError(409, "not_ready", "这轮对话还没有可以续接的回合")
    parent = ready[-1]            # 永远接在最后一个成功回合上，而不是请求路径里的那一行
    jobs_service.ensure_capacity(db, principal)
    options = {**(parent.options or {}), "question": payload.question, "engine": parent.engine}
    if parent.engine == "interpret":
        # docset 已经在首轮入过库，追问只读不写
        options.update(mode="qa", use_kb=False)
    kind, fn_name = (("interpret", "run_interpret_job") if parent.engine == "interpret"
                     else ("codex", "run_codex_job"))
    new_id = jobs_service.new_id()
    new_row = AnswerRow(id=new_id, job_id=None, user_id=principal.user_id, status="queued",
                        question=payload.question, queries=[], options=options, papers=[],
                        citations=[], kb_hits=[], trace=[], filters_label=parent.filters_label,
                        parent_id=parent.id, thread_id=parent.thread_id)
    await jobs_service.enqueue(arq, db, kind=kind, params={"answer_id": new_id, **options},
                               user_id=principal.user_id, fn_name=fn_name, answer=new_row)
    db.refresh(new_row)
    response.headers["Location"] = f"/v1/answers/{new_id}"
    return detail(db, new_row)


@router.get("/answers/{answer_id}/thread", response_model=list[AnswerSummary],
            summary="同一会话的全部回合")
def get_answer_thread(answer_id: str, db: Session = Depends(get_db),
                      principal: Principal = Depends(require())) -> list[AnswerSummary]:
    return summaries(db, thread_rows(db, _row(db, answer_id, principal)))


@router.get("/answers", response_model=Page[AnswerSummary], summary="问答任务列表")
def list_answers(status: AnswerStatus | None = None, q: str = "",
                 limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db),
                 principal: Principal = Depends(require())) -> Page[AnswerSummary]:
    conds = []
    if not principal.is_admin:
        conds.append(AnswerRow.user_id == principal.user_id)
    if status:
        conds.append(AnswerRow.status == status)
    if q.strip():
        # 追问行按根问题也能被搜到：会话在列表里只有一行，搜索不该只认最后一问
        pat = f"%{q.strip()}%"
        root = aliased(AnswerRow)
        conds.append(or_(
            AnswerRow.question.like(pat),
            AnswerRow.thread_id.in_(
                select(root.thread_id).where(root.parent_id.is_(None),
                                             root.thread_id.is_not(None),
                                             root.question.like(pat))),
        ))
    # 会话折叠：同一 thread 只留最新一行，历史列表里一次对话就是一条
    later = aliased(AnswerRow)
    conds.append(or_(
        AnswerRow.thread_id.is_(None),
        ~select(later.id).where(
            later.thread_id == AnswerRow.thread_id,
            later.id != AnswerRow.id,
            or_(later.created_at > AnswerRow.created_at,
                and_(later.created_at == AnswerRow.created_at, later.id > AnswerRow.id)),
        ).exists(),
    ))
    total = int(db.scalar(select(func.count(AnswerRow.id)).where(*conds)) or 0)
    rows = db.scalars(select(AnswerRow).where(*conds)
                      .order_by(AnswerRow.created_at.desc(), AnswerRow.id.desc())
                      .limit(limit).offset(offset)).all()
    return Page[AnswerSummary](items=summaries(db, list(rows)),
                               total=total, limit=limit, offset=offset)


@router.get("/answers/{answer_id}", response_model=Answer, summary="问答任务详情")
def get_answer(answer_id: str, db: Session = Depends(get_db),
               principal: Principal = Depends(require())) -> Answer:
    return detail(db, _row(db, answer_id, principal))


@router.get("/answers/{answer_id}/markdown", response_class=MarkdownResponse,
            summary="完整渲染稿（Markdown）")
def get_answer_markdown(answer_id: str, db: Session = Depends(get_db),
                        principal: Principal = Depends(require())) -> MarkdownResponse:
    row = _row(db, answer_id, principal)
    if row.status != "ready":
        raise ApiError(409, "not_ready", f"answer {answer_id!r} is {row.status}")
    return MarkdownResponse(http_answer_markdown(row), headers={"Cache-Control": "no-store"})


@router.get("/answers/{answer_id}/pdf", response_class=PdfResponse, summary="导出 PDF",
            responses={200: {"content": {"application/pdf": {
                "schema": {"type": "string", "format": "binary"}}}}})
async def get_answer_pdf(answer_id: str, db: Session = Depends(get_db),
                         principal: Principal = Depends(require())) -> Response:
    """服务端统一渲染：三端下载到的是同一份字节，排版不随客户端漂移。"""
    row = _row(db, answer_id, principal)
    if row.status != "ready":
        raise ApiError(409, "not_ready", f"answer {answer_id!r} is {row.status}")
    html = build_answer_html(row, root_question=detail(db, row).root_question)
    try:
        pdf = await render_pdf(html, row.question)
    except Exception as exc:  # 打印超时、Chromium 缺失、渲染崩溃：对客户端一律是「稍后重试」
        logger.exception("pdf export failed for %s", answer_id)
        raise ApiError(503, "export_failed", "PDF 渲染失败，请稍后重试") from exc
    ascii_name, utf8_name = export_filename(row)
    return PdfResponse(pdf, headers={
        "Content-Disposition":
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(utf8_name)}',
        "Cache-Control": "no-store"})


@router.get("/answers/{answer_id}/papers/{n}/markdown", response_class=MarkdownResponse,
            summary="本次阅读原文快照（含段落锚点的 Markdown）")
def get_answer_paper_markdown(answer_id: str, n: int = Path(ge=1), db: Session = Depends(get_db),
                              principal: Principal = Depends(require())) -> MarkdownResponse:
    row = _row(db, answer_id, principal)
    if row.engine == "interpret":
        path = _interpret_paper_path(row, n)
    else:
        stem = paper_stems(row).get(n)
        if stem is None:
            raise ApiError(404, "not_found", f"answer {answer_id!r} has no paper #{n}")
        path = paper_artifact_path(answer_id, stem)
    try:
        with open(path, encoding="utf-8") as f:
            return MarkdownResponse(f.read(), headers={"Cache-Control": "no-store"})
    except FileNotFoundError as exc:
        raise ApiError(404, "not_found", f"paper snapshot for {answer_id!r} #{n} is missing") from exc


@router.get("/answers/{answer_id}/papers/{n}", response_model=AnswerPaperDetail,
            summary="本次阅读的第 n 篇（笔记、引文、段落、全文）")
def get_answer_paper(answer_id: str, n: int = Path(ge=1), db: Session = Depends(get_db),
                     principal: Principal = Depends(require())) -> AnswerPaperDetail:
    row = _row(db, answer_id, principal)
    paper = next((p for p in (row.papers or []) if p.get("n") == n), None)
    if paper is None:
        raise ApiError(404, "not_found", f"answer {answer_id!r} has no paper #{n}")
    if row.engine == "interpret":
        # docset 里没有 PICOS 笔记与逐条核实引文；事实由后台入库任务写出后才可见
        with open(_interpret_paper_path(row, n), encoding="utf-8") as f:
            fulltext_md = f.read()
        facts = (read_json(interpret_service.docset_file(row.thread_id, f"kb-facts-{n}.json"),
                           default={}) or {}).get("facts", [])
        return AnswerPaperDetail(
            **paper, notes_md="", citations=[], facts=facts,
            paragraphs=read_json(interpret_service.paper_path(row.thread_id, n, "_paragraphs.json"),
                                 default=[]),
            fulltext_md=fulltext_md,
        )
    # 全文和元数据都限制在本次阅读目录，不从可被覆盖的 library 回退。
    stem = paper.get("pmid") or "paper"
    notes_path = paper_artifact_path(answer_id, stem, "_notes.md")
    fulltext_path = paper_artifact_path(answer_id, stem)
    if not (os.path.exists(notes_path) and os.path.exists(fulltext_path)):
        raise ApiError(404, "not_found", f"per-paper files for {answer_id!r} #{n} are missing")
    with open(notes_path, encoding="utf-8") as f:
        notes_md = f.read()
    with open(fulltext_path, encoding="utf-8") as f:
        fulltext_md = f.read()
    paragraphs = read_json(paper_artifact_path(answer_id, stem, "_paragraphs.json"), default=[])
    return AnswerPaperDetail(
        **paper,
        notes_md=notes_md,
        citations=read_json(paper_artifact_path(answer_id, stem, "_citations.json"), default=[]),
        facts=read_json(paper_artifact_path(answer_id, stem, "_facts.json"), default=[]),
        paragraphs=paragraphs,
        fulltext_md=fulltext_md,
    )


@router.delete("/answers/{answer_id}", status_code=204, summary="删除终态答案")
def delete_answer(answer_id: str, db: Session = Depends(get_db),
                        principal: Principal = Depends(require())) -> Response:
    row = _row(db, answer_id, principal)
    if row.status not in TERMINAL_ANSWER_STATUSES:
        raise ApiError(409, "conflict", f"answer {answer_id!r} is {row.status}; cancel its job first")
    md_path, papers_dir = answer_paths(answer_id)
    # 删除后行会失效，清理 docset 需要的字段必须先取出来
    docset_id = row.thread_id if row.engine == "interpret" else None
    db.delete(row)
    db.commit()
    shutil.rmtree(papers_dir, ignore_errors=True)
    if docset_id and not db.scalar(
            select(func.count(AnswerRow.id)).where(AnswerRow.thread_id == docset_id)):
        # 会话的最后一轮被删掉了：上传的 PDF 与解析产物一起清掉（已入库的副本不受影响）
        interpret_service.remove_docset(docset_id)
    try:
        os.remove(md_path)
    except FileNotFoundError:
        pass
    return Response(status_code=204)
