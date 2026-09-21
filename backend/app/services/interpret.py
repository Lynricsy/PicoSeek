"""文献解读 docset 的路径与清单管理。

一次上传的一组 PDF 叫一个 docset，它的 id 就是这条会话的 `thread_id`，
文件落在 `PICOSEEK_DATA/docsets/<thread_id>/`：`<n>.pdf` 是原件，
`<n>.md` / `<n>_paragraphs.json` 是 worker 解析出的产物，`manifest.json`
记录编号与文件名。路径守卫与 `answers.answer_paths` 同一写法，
所有对外可见的 id / 文件名都不参与任意路径拼接。
"""
from __future__ import annotations

import json
import os
import re
import shutil

from picos_paths import DOCSETS_DIR

from ..errors import ApiError

ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def docset_dir(thread_id: str) -> str:
    """只允许 docsets 根目录下的直接子目录，拒绝路径与符号链接越界。"""
    if not ID_RE.fullmatch(thread_id or ""):
        raise ApiError(404, "not_found", "invalid docset")
    root = os.path.realpath(DOCSETS_DIR)
    path = os.path.join(root, thread_id)
    if os.path.dirname(os.path.realpath(path)) != root:
        raise ApiError(404, "not_found", "invalid docset")
    return path


def docset_file(thread_id: str, name: str) -> str:
    if not NAME_RE.fullmatch(name or ""):
        raise ApiError(404, "not_found", "invalid docset file")
    directory = docset_dir(thread_id)
    path = os.path.join(directory, name)
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(directory):
        raise ApiError(404, "not_found", "invalid docset file")
    return path


def paper_path(thread_id: str, n: int, suffix: str = ".md") -> str:
    return docset_file(thread_id, f"{n}{suffix}")


def write_manifest(thread_id: str, files: list[dict]) -> None:
    with open(os.path.join(docset_dir(thread_id), "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "files": files}, f, ensure_ascii=False, indent=1)


def remove_docset(thread_id: str) -> None:
    shutil.rmtree(docset_dir(thread_id), ignore_errors=True)


def filters_label(n_files: int) -> str:
    return f"文献解读 · {n_files} 篇 PDF"
