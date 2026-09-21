#!/usr/bin/env python
"""数据目录集中定义。

所有运行期产物都挂在 `PICOSEEK_DATA` 下（默认为 core/ 本身，行为与历史一致）；
容器里把它指向挂载卷 `/data`，就能在不改代码的前提下把数据与代码分离。
"""
from __future__ import annotations

import os

CORE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.environ.get("PICOSEEK_DATA") or CORE_DIR

ANSWERS_DIR = os.path.join(DATA_ROOT, "answers")
LIB_DIR = os.path.join(DATA_ROOT, "library")
KB_DIR = os.path.join(DATA_ROOT, "kb")
RANK_DIR = os.path.join(DATA_ROOT, "data", "journal_ranks")
MODELS_DIR = os.path.join(DATA_ROOT, "models")
PDF_DIR = os.path.join(DATA_ROOT, "pdfs")
DOCSETS_DIR = os.path.join(DATA_ROOT, "docsets")
VAR_DIR = os.path.join(DATA_ROOT, "var")
PAYWALL_STATE = os.environ.get("SD_STATE_PATH") or os.path.join(VAR_DIR, "sd_state.json")
