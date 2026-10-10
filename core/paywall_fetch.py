#!/usr/bin/env python
"""Institutional-access PDF downloader (Playwright), compatible with SPIDER_PROJECT's sd_state.json.

Two commands:
  python paywall_fetch.py login  [--url https://www.sciencedirect.com/] [--state sd_state.json]
      Opens a visible Chromium (needs a display: your laptop / an RDP desktop). Log in through your
      institution (OpenAthens / Shibboleth / CARSI ...), then press Enter in the terminal. Saves
      sd_state.json + .session_storage.json + .context.json  (same format SPIDER_PROJECT writes).
  python paywall_fetch.py get DOI [--out file.pdf] [--state sd_state.json]
      Headless: opens https://doi.org/DOI with the saved login state, finds the PDF, downloads it.

Library use (ask.py):  download_pdf(doi, out_path) -> (ok: bool, note: str)
Rules baked in: serial downloads, polite delay, only the papers actually needed for one answer.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from spider_auth import (  # noqa: E402  (vendored from SPIDER_PROJECT/medical_scraper/human_auth.py)
    _collect_session_storage,
    _json_dump_atomic,
    apply_context_overrides,
    detect_human_auth_block,
    session_storage_init_script,
    state_paths,
)

from picos_paths import PAYWALL_STATE as DEFAULT_STATE  # noqa: E402

MIN_DELAY = float(os.environ.get("PAYWALL_MIN_DELAY", "4"))
MAX_DELAY = float(os.environ.get("PAYWALL_MAX_DELAY", "9"))
NAV_TIMEOUT = 60_000
# PW_CHANNEL=chrome -> use the system Google Chrome instead of Playwright's bundled Chromium
LAUNCH_KW = {"channel": os.environ["PW_CHANNEL"]} if os.environ.get("PW_CHANNEL") else {}
_last_download_ts = 0.0
DEBUG = bool(os.environ.get("PAYWALL_DEBUG"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")


class _Resp:
    """Duck-typed object for spider_auth.detect_human_auth_block."""

    def __init__(self, url: str, status: int, text: str):
        self.url, self.status, self.text = url, status, text


def _context_kwargs(state_path: str) -> dict:
    kw = {"user_agent": UA, "viewport": {"width": 1366, "height": 900}, "locale": "en-US",
          "accept_downloads": True}
    if state_path and os.path.exists(state_path):
        kw["storage_state"] = state_path
        apply_context_overrides(kw, state_path)
    return kw


def _pdf_candidates(page_url: str, html: str) -> list[str]:
    """Publisher-specific + generic PDF URL guesses, best first."""
    cands: list[str] = []
    u = page_url
    host = urllib.parse.urlsplit(u).netloc.lower()

    m = re.search(r'<meta[^>]+name="citation_pdf_url"[^>]+content="([^"]+)"', html, re.I) or \
        re.search(r'<meta[^>]+content="([^"]+)"[^>]+name="citation_pdf_url"', html, re.I)
    if m:
        cands.append(m.group(1))

    if "sciencedirect.com" in host:
        pii = re.search(r"/pii/([A-Z0-9]+)", u)
        if pii:
            cands.append(f"https://www.sciencedirect.com/science/article/pii/{pii.group(1)}/pdfft?isDTMRedir=true&download=true")
        for h in re.findall(r'href="([^"]*pdfft[^"]*)"', html):
            cands.append(h.replace("&amp;", "&"))
    elif "onlinelibrary.wiley.com" in host or "wiley.com" in host:
        doi = re.search(r"/doi/(?:full/|abs/|epdf/|pdf/)?(10\.[^?#]+)", u)
        if doi:
            cands.append(f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi.group(1)}?download=true")
    elif "link.springer.com" in host or "springer.com" in host:
        doi = re.search(r"/(?:article|chapter)/(10\.[^?#]+)", u)
        if doi:
            cands.append(f"https://link.springer.com/content/pdf/{doi.group(1)}.pdf")
    elif "nature.com" in host:
        cands.append(u.split("?")[0].rstrip("/") + ".pdf")
    elif "nejm.org" in host or "tandfonline.com" in host or "ahajournals.org" in host or "acpjournals.org" in host \
            or "jamanetwork.com" not in host and re.search(r"/doi/", u):
        doi = re.search(r"/doi/(?:full/|abs/)?(10\.[^?#]+)", u)
        if doi:
            cands.append(urllib.parse.urljoin(u, f"/doi/pdf/{doi.group(1)}?download=true"))
    elif "academic.oup.com" in host:
        for h in re.findall(r'href="([^"]*\.pdf[^"]*)"', html):
            cands.append(h.replace("&amp;", "&"))
    elif "thelancet.com" in host or "cell.com" in host or "jacc.org" in host:
        pii = re.search(r"/(?:fulltext|abstract)/(S[0-9X()-]+)", u)
        if pii:
            cands.append(urllib.parse.urljoin(u, f"/action/showPdf?pii={pii.group(1)}"))
    elif "ieeexplore.ieee.org" in host:
        ar = re.search(r"/document/(\d+)", u)
        if ar:
            cands.append(f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={ar.group(1)}")

    # generic anchors
    for h in re.findall(r'href="([^"]+)"', html):
        hl = h.lower()
        if (".pdf" in hl or "pdf" in hl.split("/")[-1]) and "supplement" not in hl and "javascript" not in hl:
            cands.append(h.replace("&amp;", "&"))
    # dedupe, absolutise
    out, seen = [], set()
    for c in cands:
        c = urllib.parse.urljoin(u, c)
        if c not in seen and c.startswith("http"):
            seen.add(c)
            out.append(c)
    return out[:8]


async def _download_pdf_async(doi: str, out_path: str, state_path: str, headless: bool = True) -> tuple[bool, str]:
    from playwright.async_api import async_playwright

    global _last_download_ts
    wait = MIN_DELAY + random.random() * (MAX_DELAY - MIN_DELAY) - (time.time() - _last_download_ts)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_download_ts = time.time()

    if not os.path.exists(state_path):
        return False, f"no login state at {state_path} (run: python paywall_fetch.py login)"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, **LAUNCH_KW)
        ctx = await browser.new_context(**_context_kwargs(state_path))
        init = session_storage_init_script(state_path)
        if init:
            await ctx.add_init_script(init)
        page = await ctx.new_page()
        try:
            resp = await page.goto(f"https://doi.org/{doi}", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await page.wait_for_timeout(2500)
            html = await page.content()
            block = detect_human_auth_block(_Resp(page.url, resp.status if resp else 0, html))
            # a login/cloudflare wall on the landing page means the saved state is not enough
            if block and ("login" in block or "cloudflare" in block or "body marker" in block or "http status" in block):
                return False, f"blocked at {page.url} ({block}); re-run login on this publisher"

            cands = _pdf_candidates(page.url, html)
            if DEBUG:
                print(f"[debug] landing: {page.url} status={resp.status if resp else None} block={block}")
                print(f"[debug] candidates: {cands}")
            for cand in cands:
                try:
                    r = await ctx.request.get(cand, timeout=NAV_TIMEOUT, headers={"Referer": page.url})
                except Exception as e:  # noqa: BLE001
                    if DEBUG:
                        print(f"[debug] {cand} -> exception {e}")
                    continue
                body = await r.body()
                ctype = r.headers.get("content-type", "")
                if DEBUG:
                    print(f"[debug] {cand} -> {r.status} {ctype} {len(body)}B head={body[:12]!r}")
                if r.status == 200 and (body[:5] == b"%PDF-" or "application/pdf" in ctype):
                    with open(out_path, "wb") as f:
                        f.write(body)
                    return True, f"pdf from {cand} ({len(body)//1024} KB)"
                # some publishers put the PDF behind an HTML viewer page; try one hop
                if r.status == 200 and b"<html" in body[:2000].lower():
                    inner = _pdf_candidates(cand, body.decode("utf-8", "ignore"))
                    for c2 in inner[:3]:
                        if c2 == cand:
                            continue
                        try:
                            r2 = await ctx.request.get(c2, timeout=NAV_TIMEOUT, headers={"Referer": cand})
                            b2 = await r2.body()
                            if r2.status == 200 and b2[:5] == b"%PDF-":
                                with open(out_path, "wb") as f:
                                    f.write(b2)
                                return True, f"pdf from {c2} ({len(b2)//1024} KB)"
                        except Exception:  # noqa: BLE001
                            pass
            return False, f"no PDF link found / not entitled at {page.url}"
        except Exception as e:  # noqa: BLE001
            return False, f"error: {type(e).__name__}: {e}"
        finally:
            await ctx.close()
            await browser.close()


def download_pdf(doi: str, out_path: str, state_path: str = DEFAULT_STATE) -> tuple[bool, str]:
    return asyncio.run(_download_pdf_async(doi, out_path, state_path))


async def _login_async(start_url: str, state_path: str) -> None:
    from playwright.async_api import async_playwright

    paths = state_paths(state_path)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, **LAUNCH_KW)
        kw = _context_kwargs(state_path)
        ctx = await browser.new_context(**kw)
        page = await ctx.new_page()
        await page.goto(start_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        print("\n>>> 在弹出的浏览器里完成机构登录（OpenAthens / Shibboleth / CARSI / 校园 VPN 网关）。")
        print(">>> 登录后随便打开一篇付费文章确认能看到全文，然后回到这里按 Enter 保存登录态。")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await ctx.storage_state(path=paths.storage_state)
        _json_dump_atomic(paths.session_storage, await _collect_session_storage(ctx))
        _json_dump_atomic(paths.context_meta, {
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "context_kwargs": {k: kw[k] for k in ("user_agent", "viewport", "locale") if k in kw},
            "final_url": page.url,
        })
        print(f"saved: {paths.storage_state}\n       {paths.session_storage}\n       {paths.context_meta}")
        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("login"); a.add_argument("--url", default="https://www.sciencedirect.com/"); a.add_argument("--state", default=DEFAULT_STATE)
    b = sub.add_parser("get"); b.add_argument("doi"); b.add_argument("--out", default=""); b.add_argument("--state", default=DEFAULT_STATE)
    b.add_argument("--headed", action="store_true", help="show the browser (debug)")
    args = ap.parse_args()
    if args.cmd == "login":
        asyncio.run(_login_async(args.url, args.state))
    else:
        out = args.out or os.path.join(ROOT, "pdfs", re.sub(r"[^A-Za-z0-9.]+", "_", args.doi) + ".pdf")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        ok, note = asyncio.run(_download_pdf_async(args.doi, out, args.state, headless=not args.headed))
        print(("OK  " if ok else "FAIL") + f"  {note}" + (f"\n -> {out}" if ok else ""))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
