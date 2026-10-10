#!/usr/bin/env python
"""Journal ranking (quartile / 分区) lookup and filtering.

Data sources (all local files under data/journal_ranks/, loaded in this order; later files override):
  * scimagojr_*.csv   — SCImago Journal Rank export (free). Column "SJR Best Quartile" -> Q1..Q4.
                        Download:  python journal_rank.py download [YEAR]   (uses headless Playwright,
                        because scimagojr.com sits behind Cloudflare and blocks plain HTTP clients).
  * cas_*.csv / custom_*.csv — optional 中科院分区表 / JCR export you provide yourself. Needs a column
                        containing "ISSN" (or "刊名"/"Journal") and a column whose header contains
                        "分区" / "Quartile" / "zone" with values like 1, 2, Q1, "1区", "一区", "Q2".
                        A column containing "Top" (是/Yes/1) is kept as the `top` flag.
Lookup keys: ISSN (print / electronic / linking) first, normalised title second.

Usage in code:
    import journal_rank as jr
    info = jr.lookup(issn="0140-6736", title="Lancet")   # {'quartile': 'Q1', 'sjr': 145.0, 'zone': 1, ...} or None
    ok   = jr.passes(info, allowed={"Q1","Q2"})
CLI:
    python journal_rank.py lookup "0140-6736"
    python journal_rank.py lookup "Nature Medicine"
    python journal_rank.py download 2024
"""
from __future__ import annotations

import csv
import glob
import os
import re
import sys
import time
from typing import Optional

from picos_paths import RANK_DIR as DATA_DIR

_BY_ISSN: dict[str, dict] = {}
_BY_TITLE: dict[str, dict] = {}
_TABLES: list[dict] = []        # 每张表一条 {"file","year","journals","source"}
_SIG: tuple | None = None       # 目录内表文件的 (名, mtime_ns, 大小) 快照；变化即重载
_LOADED_AT: float | None = None

ZONE_WORDS = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def norm_issn(s: str) -> str:
    s = (s or "").strip().upper().replace("-", "").replace(" ", "")
    return s if re.fullmatch(r"[0-9]{7}[0-9X]", s) else ""


def norm_title(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)              # "Lancet (London, England)" -> "Lancet"
    s = re.sub(r"^the\s+", "", s.strip())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def parse_zone(v: str) -> Optional[int]:
    """'Q1' / '1' / '1区' / '一区' / 'Q2 (SJR)' -> 1..4"""
    v = (v or "").strip()
    m = re.search(r"[Qq]\s*([1-4])", v)
    if m:
        return int(m.group(1))
    m = re.search(r"([一二三四1-4])\s*区", v)
    if m:
        return ZONE_WORDS[m.group(1)]
    if v in ("1", "2", "3", "4"):
        return int(v)
    return None


Rec = dict
Index = dict[str, dict]


def _add(rec: Rec, by_issn: Index, by_title: Index) -> None:
    for i in rec.get("issns", []):
        if i:
            by_issn[i] = rec
    t = norm_title(rec.get("title", ""))
    if t:
        by_title[t] = rec


def _load_scimago(path: str, by_issn: Index, by_title: Index) -> int:
    n = 0
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        rd = csv.DictReader(f, delimiter=";")
        for row in rd:
            q = parse_zone(row.get("SJR Best Quartile", ""))
            if q is None:
                continue
            issns = [norm_issn(x) for x in (row.get("Issn") or "").split(",")]
            sjr_raw = (row.get("SJR") or "").replace(",", ".")
            try:
                sjr = float(sjr_raw)
            except ValueError:
                sjr = None
            cats = row.get("Categories") or ""
            _add({"title": row.get("Title", ""), "issns": [i for i in issns if i], "zone": q, "quartile": f"Q{q}",
                  "sjr": sjr, "h_index": row.get("H index"), "categories": cats, "top": False,
                  "source": "SCImago " + os.path.basename(path)}, by_issn, by_title)
            n += 1
    return n


def _load_custom(path: str, by_issn: Index, by_title: Index) -> int:
    """Generic CSV (中科院分区表 export etc.). Delimiter sniffed; header matched by keywords."""
    n = 0
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rd = csv.DictReader(f, dialect=dialect)
        cols = rd.fieldnames or []
        issn_cols = [c for c in cols if "issn" in c.lower()]
        title_cols = [c for c in cols if any(k in c.lower() for k in ("刊名", "journal", "title", "期刊"))]
        zone_cols = [c for c in cols if any(k in c.lower() for k in ("分区", "quartile", "zone", "jcr", "q"))
                     and c not in issn_cols and c not in title_cols]
        zone_cols.sort(key=lambda c: ("大类" not in c, "分区" not in c.lower()))  # prefer 大类分区
        top_cols = [c for c in cols if "top" in c.lower()]
        if not (issn_cols or title_cols) or not zone_cols:
            print(f"[journal_rank] skip {path}: cannot find ISSN/title + 分区 columns in {cols}", file=sys.stderr)
            return 0
        for row in rd:
            zone = None
            for c in zone_cols:
                zone = parse_zone(row.get(c, ""))
                if zone:
                    break
            if not zone:
                continue
            issns = []
            for c in issn_cols:
                issns += [norm_issn(x) for x in re.split(r"[;,/ ]+", row.get(c, "") or "")]
            title = next((row.get(c, "") for c in title_cols if row.get(c)), "")
            top = any(str(row.get(c, "")).strip().lower() in ("是", "yes", "y", "1", "true", "top") for c in top_cols)
            _add({"title": title, "issns": [i for i in issns if i], "zone": zone, "quartile": f"Q{zone}", "sjr": None,
                  "categories": "", "top": top, "source": os.path.basename(path)}, by_issn, by_title)
            n += 1
    return n


def _table_files() -> list[str]:
    """SCImago 表在前、其它自定义表在后；同组按文件名排序，后加载者覆盖同刊记录。"""
    return sorted(glob.glob(os.path.join(DATA_DIR, "scimagojr*.csv"))) + \
        sorted(p for p in glob.glob(os.path.join(DATA_DIR, "*.csv")) if "scimagojr" not in os.path.basename(p))


def _signature() -> tuple:
    """目录内表文件的 (名, mtime_ns, 大小) 快照：任一项变化就说明换表/新表，需要重载。"""
    sig = []
    for p in _table_files():
        try:
            st = os.stat(p)
        except FileNotFoundError:      # glob 与 stat 之间被删掉
            continue
        sig.append((os.path.basename(p), st.st_mtime_ns, st.st_size))
    return tuple(sig)


def _year_of(basename: str) -> Optional[int]:
    m = re.search(r"(?<!\d)(19|20)\d{2}(?!\d)", basename)
    return int(m.group(0)) if m else None


def _describe(t: Rec) -> str:
    return f"{t['file']} ({t['journals']} journals)"


def load(force: bool = False) -> list[str]:
    """Load all ranking tables. 文件签名未变则复用已加载结果；变了就自动重载（无需重启进程）。"""
    global _BY_ISSN, _BY_TITLE, _TABLES, _SIG, _LOADED_AT
    sig = _signature()
    if sig == _SIG and not force:
        return [_describe(t) for t in _TABLES]
    by_issn: Index = {}
    by_title: Index = {}
    tables: list[Rec] = []
    for p in _table_files():
        base = os.path.basename(p)
        scimago = "scimagojr" in base
        n = _load_scimago(p, by_issn, by_title) if scimago else _load_custom(p, by_issn, by_title)
        if n:
            tables.append({"file": base, "year": _year_of(base), "journals": n,
                           "source": "scimago" if scimago else "custom"})
    # 构建完成后一次性换引用：并发读者要么看到旧表要么看到新表，不会看到半空表
    _BY_ISSN, _BY_TITLE, _TABLES, _SIG, _LOADED_AT = by_issn, by_title, tables, sig, time.time()
    return [_describe(t) for t in tables]


def lookup(issn: str = "", title: str = "") -> Optional[dict]:
    """Return ranking record for a journal, or None if unknown. `issn` may hold several ids separated by ; , or space."""
    load()
    by_issn, by_title = _BY_ISSN, _BY_TITLE   # 先取引用，避免查询中途被 load() 换掉
    for raw in re.split(r"[;,\s]+", issn or ""):
        i = norm_issn(raw)
        if i and i in by_issn:
            return by_issn[i]
    t = norm_title(title)
    if t and t in by_title:
        return by_title[t]
    return None


def stats() -> dict:
    """已加载分区表的概况：每张表的文件/年份/刊数/来源，以及索引规模与加载时刻。"""
    load()
    return {"tables": list(_TABLES), "issns": len(_BY_ISSN), "titles": len(_BY_TITLE), "loaded_at": _LOADED_AT}


def parse_quartile_arg(s: str) -> set[int]:
    """'Q1,Q2' / '1,2' / '1-3' / '一区,二区' / 'Q1-Q2' -> {1,2}"""
    s = (s or "").strip()
    if not s:
        return set()
    out: set[int] = set()
    for part in re.split(r"[,，;/ ]+", s):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"[Qq]?([1-4])\s*[-~至]\s*[Qq]?([1-4])", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.update(range(min(a, b), max(a, b) + 1))
            continue
        z = parse_zone(part)
        if z:
            out.add(z)
    return out


def passes(info: Optional[dict], zones: set[int], keep_unranked: bool = False) -> bool:
    if not zones:
        return True
    if info is None:
        return keep_unranked
    return info["zone"] in zones


def label(info: Optional[dict]) -> str:
    """Short human label, e.g. 'Q1 / SJR 6.9' or '未收录'."""
    if not info:
        return "分区未知"
    s = info["quartile"]
    if info.get("sjr") is not None:
        s += f" SJR {info['sjr']:.2f}"
    if info.get("top"):
        s += " Top"
    return s


# ---------------------------------------------------------------- download (SCImago via Playwright)
def download_scimago(year: int, dest_dir: str = DATA_DIR) -> str:
    """Fetch the SCImago journal-rank CSV for `year` through a headless browser (Cloudflare-protected site)."""
    # playwright 由 core[paywall] extra 提供（backend 依赖已带上，workspace .venv 与镜像均有）
    from playwright.sync_api import sync_playwright  # type: ignore

    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"scimagojr_{year}.csv")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(accept_downloads=True, user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"))
        pg = ctx.new_page()
        pg.goto(f"https://www.scimagojr.com/journalrank.php?year={year}", wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(8000)  # let the Cloudflare challenge settle
        with pg.expect_download(timeout=120000) as dl:
            pg.goto(f"https://www.scimagojr.com/journalrank.php?out=xls&year={year}")
        dl.value.save_as(dest)
        b.close()
    with open(dest, encoding="utf-8", errors="replace") as f:
        head = f.readline()
    if "SJR Best Quartile" not in head:
        os.remove(dest)
        raise RuntimeError("download did not return a SCImago CSV (Cloudflare challenge not passed?)")
    return dest


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
    elif args[0] == "download":
        import datetime as _dt
        y = int(args[1]) if len(args) > 1 else _dt.date.today().year - 1
        print("saved:", download_scimago(y))
    elif args[0] == "lookup":
        print("tables:", load())
        key = " ".join(args[1:])
        info = lookup(issn=key, title=key)
        print(info or "not found")
    elif args[0] == "stats":
        s = stats()
        print("tables:", [t["file"] for t in s["tables"]])
        print(f"{s['issns']} ISSNs, {s['titles']} titles")
    else:
        print(__doc__)
