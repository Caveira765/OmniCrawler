import asyncio
import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urldefrag

import httpx
import html2text
from bs4 import BeautifulSoup
from parsel import Selector

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    HAS_CRAWL4AI = True
except ImportError:
    HAS_CRAWL4AI = False


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

MEDIA_EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif"},
    "document": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".rtf", ".epub"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"},
    "media": {".mp4", ".webm", ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".mov"},
}


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    clean, _ = urldefrag(url)
    return clean


def classify_asset_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    for category, exts in MEDIA_EXTENSIONS.items():
        for ext in exts:
            if path_lower.endswith(ext):
                return category
    return None


def extract_structured_from_html(html_content: str, base_url: str) -> Dict[str, Any]:
    """Extracts metadata, headings, tables, links, and media assets from raw HTML."""
    soup = BeautifulSoup(html_content or "", "lxml")

    # 1. Metadata
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    meta_tags: Dict[str, str] = {}
    for m in soup.find_all("meta"):
        key = m.get("name") or m.get("property") or m.get("http-equiv")
        val = m.get("content")
        if key and val:
            meta_tags[str(key).strip()] = str(val).strip()
    if not title:
        title = meta_tags.get("og:title") or meta_tags.get("twitter:title") or base_url

    description = (
        meta_tags.get("description")
        or meta_tags.get("og:description")
        or meta_tags.get("twitter:description")
        or ""
    )

    # 2. Headings (H1 - H4)
    headings: List[Dict[str, str]] = []
    for level in ["h1", "h2", "h3", "h4"]:
        for h in soup.find_all(level):
            txt = h.get_text(" ", strip=True)
            if txt:
                headings.append({"level": level.upper(), "text": txt})

    # 3. Tables
    tables: List[Dict[str, Any]] = []
    for idx, tbl in enumerate(soup.find_all("table")):
        rows_data: List[List[str]] = []
        for tr in tbl.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if any(cells):
                rows_data.append(cells)
        if rows_data:
            headers = rows_data[0] if len(rows_data) > 1 else []
            body = rows_data[1:] if len(rows_data) > 1 else rows_data
            tables.append({
                "index": idx + 1,
                "headers": headers,
                "rows": body,
                "row_count": len(rows_data),
            })

    # 4. Links & Media Assets
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc.lower()

    internal_links: List[Dict[str, str]] = []
    external_links: List[Dict[str, str]] = []
    seen_links: Set[str] = set()

    media_assets: List[Dict[str, str]] = []
    seen_media: Set[str] = set()

    def add_media(raw_u: str, m_type: str, label: str = ""):
        if not raw_u or raw_u.startswith("data:") or raw_u.startswith("javascript:") or raw_u.startswith("blob:"):
            return
        full_u = urljoin(base_url, raw_u.strip())
        full_u, _ = urldefrag(full_u)
        if full_u in seen_media:
            return
        seen_media.add(full_u)
        media_assets.append({
            "url": full_u,
            "type": m_type,
            "label": (label or os.path.basename(urlparse(full_u).path) or full_u)[:120],
        })

    # Images
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        alt = img.get("alt") or img.get("title") or ""
        if src:
            add_media(str(src), "image", str(alt).strip())

    # Video / Audio sources
    for media_tag in soup.find_all(["video", "audio", "source"]):
        src = media_tag.get("src")
        if src:
            add_media(str(src), "media", media_tag.name)

    # Links (<a>)
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base_url, href)
        full_url, _ = urldefrag(full_url)
        parsed_u = urlparse(full_url)
        if parsed_u.scheme not in ("http", "https"):
            continue

        text = a.get_text(" ", strip=True) or parsed_u.path or full_url
        asset_cat = classify_asset_url(full_url)
        if asset_cat:
            add_media(full_url, asset_cat, text)

        if full_url not in seen_links:
            seen_links.add(full_url)
            entry = {"url": full_url, "text": text[:160]}
            if parsed_u.netloc.lower() == base_domain:
                internal_links.append(entry)
            else:
                external_links.append(entry)

    # 5. Build Sequential DOM Harvest Queue (for item-by-item physical spider navigation)
    harvest_nodes: List[Dict[str, Any]] = []
    harvest_nodes.append({
        "index": 1,
        "category": "meta",
        "tag": "<title>",
        "title": title[:90] or base_url,
        "snippet": description[:160] or f"URL: {base_url}",
        "x_pct": 0.50,
        "y_pct": 0.06,
    })

    body_root = soup.body or soup
    dom_candidates = body_root.find_all(["h1", "h2", "h3", "p", "table", "img", "blockquote", "pre", "ul"], limit=140)
    max_harvest = 24
    col_cycle = [0.26, 0.68, 0.38, 0.74, 0.22, 0.58]

    for idx_el, el in enumerate(dom_candidates):
        if len(harvest_nodes) >= max_harvest:
            break
        tname = (el.name or "").lower()
        cat = "content"
        item_title = ""
        item_snippet = ""

        if tname in ("h1", "h2", "h3"):
            txt = el.get_text(" ", strip=True)
            if len(txt) < 3:
                continue
            cat = "heading"
            item_title = f"{tname.upper()}: {txt[:75]}"
            item_snippet = txt[:150]
        elif tname == "p":
            txt = el.get_text(" ", strip=True)
            if len(txt) < 45:
                continue
            cat = "paragraph"
            item_title = f"Parágrafo ({len(txt.split())} palavras)"
            item_snippet = txt[:160]
        elif tname == "table":
            rows = el.find_all("tr")
            if not rows:
                continue
            cat = "table"
            first_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])[:4]]
            item_title = f"Tabela HTML ({len(rows)} linhas)"
            item_snippet = " | ".join(c for c in first_cells if c)[:150] or "Dados tabulares"
        elif tname == "img":
            src = el.get("src") or el.get("data-src") or ""
            if not src or str(src).startswith("data:"):
                continue
            alt = (el.get("alt") or os.path.basename(urlparse(str(src)).path) or "Imagem").strip()
            if len(alt) < 2:
                continue
            cat = "media"
            item_title = f"Imagem: {alt[:65]}"
            item_snippet = urljoin(base_url, str(src))[:150]
        elif tname in ("blockquote", "pre", "ul"):
            txt = el.get_text(" ", strip=True)
            if len(txt) < 35:
                continue
            cat = "block"
            item_title = f"Bloco <{tname}>"
            item_snippet = txt[:150]

        if item_title:
            n_idx = len(harvest_nodes) + 1
            harvest_nodes.append({
                "index": n_idx,
                "category": cat,
                "tag": f"<{tname}>",
                "title": item_title,
                "snippet": item_snippet,
                "x_pct": col_cycle[n_idx % len(col_cycle)],
                "y_pct": round(0.10 + (n_idx / (max_harvest + 2)) * 0.82, 3),
            })

    # Add up to 4 key internal/external links at the end of the page tour if room
    for lk in (internal_links[:3] + external_links[:2]):
        if len(harvest_nodes) >= max_harvest + 3:
            break
        n_idx = len(harvest_nodes) + 1
        harvest_nodes.append({
            "index": n_idx,
            "category": "link",
            "tag": "<a href>",
            "title": f"Link: {(lk.get('text') or 'URL')[:65]}",
            "snippet": lk.get("url", "")[:150],
            "x_pct": col_cycle[n_idx % len(col_cycle)],
            "y_pct": round(min(0.95, 0.12 + (n_idx / (max_harvest + 4)) * 0.84), 3),
        })

    # Normalize y_pct across actual count so items span top-to-bottom of page smoothly
    total_h_nodes = max(1, len(harvest_nodes))
    for i, node in enumerate(harvest_nodes):
        node["y_pct"] = round(0.07 + (i / max(1, total_h_nodes - 1)) * 0.86, 3) if total_h_nodes > 1 else 0.25

    # 6. Clean Fallback Markdown & Plain Text via html2text
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        tag.decompose()

    h2t = html2text.HTML2Text()
    h2t.ignore_links = False
    h2t.ignore_images = False
    h2t.body_width = 0
    h2t.protect_links = True
    markdown_fallback = h2t.handle(str(soup)).strip()
    plain_text = soup.get_text("\n", strip=True)

    return {
        "title": title,
        "description": description,
        "meta_tags": meta_tags,
        "headings": headings,
        "tables": tables,
        "internal_links": internal_links,
        "external_links": external_links,
        "media_assets": media_assets,
        "harvest_nodes": harvest_nodes,
        "markdown_fallback": markdown_fallback,
        "plain_text": plain_text,
    }


async def crawl_single_trafilatura(
    url: str,
    headers: Dict[str, str],
    timeout_sec: int = 25,
) -> Dict[str, Any]:
    """Engine 1: Trafilatura + HTTPX (Ultra-fast, lightweight extraction without browser overhead)."""
    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=timeout_sec,
        verify=False,
    ) as client:
        resp = await client.get(url)
        final_url = str(resp.url)
        status_code = resp.status_code
        raw_html = resp.text

    structured = extract_structured_from_html(raw_html, final_url)

    clean_md = ""
    if HAS_TRAFILATURA and raw_html:
        try:
            extracted = trafilatura.extract(
                raw_html,
                url=final_url,
                output_format="markdown",
                include_links=True,
                include_images=True,
                include_tables=True,
                include_formatting=True,
            )
            if extracted:
                clean_md = extracted.strip()
        except Exception:
            pass

    if not clean_md:
        clean_md = structured["markdown_fallback"]

    elapsed = round(time.perf_counter() - t0, 2)
    return {
        "url": final_url,
        "status_code": status_code,
        "engine_used": "trafilatura",
        "elapsed_seconds": elapsed,
        "title": structured["title"],
        "description": structured["description"],
        "markdown": clean_md,
        "plain_text": structured["plain_text"],
        "raw_html": raw_html,
        "meta_tags": structured["meta_tags"],
        "headings": structured["headings"],
        "tables": structured["tables"],
        "internal_links": structured["internal_links"],
        "external_links": structured["external_links"],
        "media_assets": structured["media_assets"],
        "harvest_nodes": structured["harvest_nodes"],
        "screenshot_b64": None,
        "pdf_b64": None,
    }


async def crawl_single_scrapy_parsel(
    url: str,
    headers: Dict[str, str],
    timeout_sec: int = 25,
    custom_css_selector: Optional[str] = None,
    custom_xpath: Optional[str] = None,
) -> Dict[str, Any]:
    """Engine 2: Scrapy/Parsel High-Speed XPath & CSS Selector Engine."""
    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=timeout_sec,
        verify=False,
    ) as client:
        resp = await client.get(url)
        final_url = str(resp.url)
        status_code = resp.status_code
        raw_html = resp.text

    selector = Selector(text=raw_html)
    structured = extract_structured_from_html(raw_html, final_url)

    # Custom selector extraction if provided by user
    custom_matches: List[str] = []
    if custom_css_selector:
        try:
            custom_matches.extend(
                [x.strip() for x in selector.css(custom_css_selector).getall() if x.strip()]
            )
        except Exception as e:
            custom_matches.append(f"Erro CSS Selector: {e}")
    if custom_xpath:
        try:
            custom_matches.extend(
                [x.strip() for x in selector.xpath(custom_xpath).getall() if x.strip()]
            )
        except Exception as e:
            custom_matches.append(f"Erro XPath: {e}")

    # Also extract JSON-LD structured data via Parsel XPath
    json_ld_items: List[Any] = []
    for raw_ld in selector.xpath('//script[@type="application/ld+json"]/text()').getall():
        try:
            json_ld_items.append(json.loads(raw_ld.strip()))
        except Exception:
            pass

    if json_ld_items:
        structured["meta_tags"]["json_ld_count"] = str(len(json_ld_items))

    clean_md = structured["markdown_fallback"]
    if custom_matches:
        matches_md = "\n\n".join(f"- `{m[:500]}`" for m in custom_matches[:100])
        clean_md = f"### 🎯 Resultados do Seletor Customizado ({len(custom_matches)} itens)\n\n{matches_md}\n\n---\n\n{clean_md}"

    elapsed = round(time.perf_counter() - t0, 2)
    return {
        "url": final_url,
        "status_code": status_code,
        "engine_used": "scrapy_parsel",
        "elapsed_seconds": elapsed,
        "title": structured["title"],
        "description": structured["description"],
        "markdown": clean_md,
        "plain_text": structured["plain_text"],
        "raw_html": raw_html,
        "meta_tags": structured["meta_tags"],
        "headings": structured["headings"],
        "tables": structured["tables"],
        "internal_links": structured["internal_links"],
        "external_links": structured["external_links"],
        "media_assets": structured["media_assets"],
        "harvest_nodes": structured["harvest_nodes"],
        "custom_matches": custom_matches,
        "json_ld": json_ld_items,
        "screenshot_b64": None,
        "pdf_b64": None,
    }


async def crawl_single_playwright(
    url: str,
    headers: Dict[str, str],
    wait_ms: int = 1200,
    scroll_page: bool = True,
    capture_screenshot: bool = True,
    capture_pdf: bool = True,
    timeout_sec: int = 45,
) -> Dict[str, Any]:
    """Engine 3: Playwright Full Headless Browser (JS rendering, full-page Screenshot PNG, PDF, lazy-load scroll)."""
    if not HAS_PLAYWRIGHT:
        raise RuntimeError("Playwright não está instalado no ambiente.")

    t0 = time.perf_counter()
    screenshot_b64: Optional[str] = None
    pdf_b64: Optional[str] = None
    raw_html = ""
    final_url = url
    status_code = 200

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=headers.get("User-Agent", DEFAULT_USER_AGENT),
            viewport={"width": 1440, "height": 900},
            extra_http_headers={k: v for k, v in headers.items() if k.lower() != "user-agent"},
            ignore_https_errors=True,
        )
        page = await context.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
            if resp:
                status_code = resp.status
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass

            if scroll_page:
                try:
                    await page.evaluate(
                        """async () => {
                            await new Promise((resolve) => {
                                let totalHeight = 0;
                                const distance = 600;
                                const maxScrolls = 12;
                                let scrolls = 0;
                                const timer = setInterval(() => {
                                    const scrollHeight = document.body ? document.body.scrollHeight : 0;
                                    window.scrollBy(0, distance);
                                    totalHeight += distance;
                                    scrolls += 1;
                                    if (totalHeight >= scrollHeight || scrolls >= maxScrolls) {
                                        clearInterval(timer);
                                        window.scrollTo(0, 0);
                                        resolve();
                                    }
                                }, 120);
                            });
                        }"""
                    )
                except Exception:
                    pass

            if wait_ms > 0:
                await page.wait_for_timeout(min(wait_ms, 15000))

            final_url = page.url
            raw_html = await page.content()

            if capture_screenshot:
                try:
                    png_bytes = await page.screenshot(full_page=True, type="png")
                    screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
                except Exception:
                    try:
                        png_bytes = await page.screenshot(full_page=False, type="png")
                        screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
                    except Exception:
                        pass

            if capture_pdf:
                try:
                    pdf_bytes = await page.pdf(format="A4", print_background=True)
                    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
                except Exception:
                    pass
        finally:
            await context.close()
            await browser.close()

    structured = extract_structured_from_html(raw_html, final_url)
    clean_md = ""
    if HAS_TRAFILATURA and raw_html:
        try:
            clean_md = trafilatura.extract(
                raw_html,
                url=final_url,
                output_format="markdown",
                include_links=True,
                include_images=True,
                include_tables=True,
            ) or ""
        except Exception:
            pass
    if not clean_md or len(clean_md) < 120:
        clean_md = structured["markdown_fallback"]

    elapsed = round(time.perf_counter() - t0, 2)
    return {
        "url": final_url,
        "status_code": status_code,
        "engine_used": "playwright",
        "elapsed_seconds": elapsed,
        "title": structured["title"],
        "description": structured["description"],
        "markdown": clean_md,
        "plain_text": structured["plain_text"],
        "raw_html": raw_html,
        "meta_tags": structured["meta_tags"],
        "headings": structured["headings"],
        "tables": structured["tables"],
        "internal_links": structured["internal_links"],
        "external_links": structured["external_links"],
        "media_assets": structured["media_assets"],
        "harvest_nodes": structured["harvest_nodes"],
        "screenshot_b64": screenshot_b64,
        "pdf_b64": pdf_b64,
    }


async def crawl_single_crawl4ai(
    url: str,
    headers: Dict[str, str],
    wait_ms: int = 1200,
    scroll_page: bool = True,
    capture_screenshot: bool = True,
    capture_pdf: bool = True,
    timeout_sec: int = 45,
) -> Dict[str, Any]:
    """Engine 4: Crawl4AI (AI-Ready Markdown Extraction + Playwright Browser + Media/Links)."""
    if not HAS_CRAWL4AI:
        return await crawl_single_playwright(
            url=url,
            headers=headers,
            wait_ms=wait_ms,
            scroll_page=scroll_page,
            capture_screenshot=capture_screenshot,
            capture_pdf=capture_pdf,
            timeout_sec=timeout_sec,
        )

    t0 = time.perf_counter()
    try:
        browser_cfg = BrowserConfig(
            headless=True,
            user_agent=headers.get("User-Agent", DEFAULT_USER_AGENT),
            headers={k: v for k, v in headers.items() if k.lower() != "user-agent"},
            viewport_width=1440,
            viewport_height=900,
        )
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            screenshot=capture_screenshot,
            pdf=capture_pdf,
            scan_full_page=scroll_page,
            delay_before_return_html=max(wait_ms / 1000.0, 0.5),
            page_timeout=timeout_sec * 1000,
        )
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)

        if not getattr(result, "success", False):
            # Fallback to direct Playwright if Crawl4AI encounters config/version mismatch
            return await crawl_single_playwright(
                url=url,
                headers=headers,
                wait_ms=wait_ms,
                scroll_page=scroll_page,
                capture_screenshot=capture_screenshot,
                capture_pdf=capture_pdf,
                timeout_sec=timeout_sec,
            )

        raw_html = getattr(result, "html", "") or ""
        final_url = getattr(result, "url", url) or url
        status_code = getattr(result, "status_code", 200) or 200
        structured = extract_structured_from_html(raw_html, final_url)

        md_obj = getattr(result, "markdown", None)
        clean_md = ""
        if isinstance(md_obj, str):
            clean_md = md_obj.strip()
        elif md_obj is not None:
            clean_md = (
                getattr(md_obj, "fit_markdown", "")
                or getattr(md_obj, "raw_markdown", "")
                or str(md_obj)
            ).strip()

        if not clean_md:
            clean_md = structured["markdown_fallback"]

        screenshot_b64 = getattr(result, "screenshot", None)
        pdf_raw = getattr(result, "pdf", None)
        pdf_b64: Optional[str] = None
        if isinstance(pdf_raw, bytes):
            pdf_b64 = base64.b64encode(pdf_raw).decode("ascii")
        elif isinstance(pdf_raw, str):
            pdf_b64 = pdf_raw

        elapsed = round(time.perf_counter() - t0, 2)
        return {
            "url": final_url,
            "status_code": status_code,
            "engine_used": "crawl4ai",
            "elapsed_seconds": elapsed,
            "title": structured["title"],
            "description": structured["description"],
            "markdown": clean_md,
            "plain_text": structured["plain_text"],
            "raw_html": raw_html,
            "meta_tags": structured["meta_tags"],
            "headings": structured["headings"],
            "tables": structured["tables"],
            "internal_links": structured["internal_links"],
            "external_links": structured["external_links"],
            "media_assets": structured["media_assets"],
            "screenshot_b64": screenshot_b64,
            "pdf_b64": pdf_b64,
        }
    except Exception:
        return await crawl_single_playwright(
            url=url,
            headers=headers,
            wait_ms=wait_ms,
            scroll_page=scroll_page,
            capture_screenshot=capture_screenshot,
            capture_pdf=capture_pdf,
            timeout_sec=timeout_sec,
        )


async def execute_crawl_job(
    start_url: str,
    engine: str = "auto",
    follow_links: bool = False,
    max_pages: int = 1,
    max_depth: int = 1,
    same_domain_only: bool = True,
    url_pattern: Optional[str] = None,
    wait_ms: int = 1200,
    scroll_page: bool = True,
    capture_visuals: bool = True,
    custom_headers: Optional[Dict[str, str]] = None,
    custom_css_selector: Optional[str] = None,
    custom_xpath: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes a single-page or multi-page (BFS deep crawl) request using the chosen engine.
    Supports: 'auto', 'crawl4ai', 'playwright', 'trafilatura', 'scrapy_parsel'.
    """
    norm_start = normalize_url(start_url)
    if not norm_start:
        raise ValueError("URL inválida ou vazia.")

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    if custom_headers:
        headers.update({str(k): str(v) for k, v in custom_headers.items() if k and v})

    max_pages = max(1, min(int(max_pages), 50)) if follow_links else 1
    max_depth = max(1, min(int(max_depth), 4)) if follow_links else 0

    start_domain = urlparse(norm_start).netloc.lower()
    queue: List[Tuple[str, int]] = [(norm_start, 0)]
    visited: Set[str] = set()
    pages_results: List[Dict[str, Any]] = []

    job_t0 = time.perf_counter()

    async def run_one_page(target_url: str, is_first_page: bool) -> Dict[str, Any]:
        # Capture visuals (Screenshot/PDF) on the main page or when explicitly requested
        want_visuals = capture_visuals and (is_first_page or max_pages <= 5)

        chosen = engine.lower().strip()
        if chosen == "auto":
            if custom_css_selector or custom_xpath:
                chosen = "scrapy_parsel"
            elif want_visuals and (HAS_CRAWL4AI or HAS_PLAYWRIGHT):
                chosen = "crawl4ai" if HAS_CRAWL4AI else "playwright"
            else:
                chosen = "trafilatura"

        if chosen == "trafilatura":
            res = await crawl_single_trafilatura(target_url, headers=headers)
            # If user also wanted visuals on the first page in trafilatura mode, optionally capture via playwright if requested
            return res
        elif chosen == "scrapy_parsel":
            return await crawl_single_scrapy_parsel(
                target_url,
                headers=headers,
                custom_css_selector=custom_css_selector,
                custom_xpath=custom_xpath,
            )
        elif chosen == "playwright":
            return await crawl_single_playwright(
                target_url,
                headers=headers,
                wait_ms=wait_ms,
                scroll_page=scroll_page,
                capture_screenshot=want_visuals,
                capture_pdf=want_visuals,
            )
        else:  # crawl4ai
            return await crawl_single_crawl4ai(
                target_url,
                headers=headers,
                wait_ms=wait_ms,
                scroll_page=scroll_page,
                capture_screenshot=want_visuals,
                capture_pdf=want_visuals,
            )

    while queue and len(pages_results) < max_pages:
        current_url, depth = queue.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)

        try:
            page_data = await run_one_page(current_url, is_first_page=(len(pages_results) == 0))
            page_data["depth"] = depth
            pages_results.append(page_data)

            # Enqueue discovered links if follow_links is enabled and depth < max_depth
            if follow_links and depth < max_depth and len(pages_results) < max_pages:
                candidate_links = list(page_data.get("internal_links", []))
                if not same_domain_only:
                    candidate_links.extend(page_data.get("external_links", []))

                for link_obj in candidate_links:
                    next_u = link_obj.get("url", "")
                    if not next_u or next_u in visited:
                        continue
                    if same_domain_only and urlparse(next_u).netloc.lower() != start_domain:
                        continue
                    if url_pattern and url_pattern.strip():
                        if url_pattern.strip().lower() not in next_u.lower():
                            continue
                    if classify_asset_url(next_u) is not None:
                        continue
                    queue.append((next_u, depth + 1))
        except Exception as exc:
            pages_results.append({
                "url": current_url,
                "status_code": 0,
                "engine_used": engine,
                "elapsed_seconds": 0,
                "depth": depth,
                "title": f"Erro ao acessar {current_url}",
                "description": str(exc),
                "markdown": f"### ❌ Falha ao varrer `{current_url}`\n\n```\n{exc}\n```",
                "plain_text": str(exc),
                "raw_html": "",
                "meta_tags": {},
                "headings": [],
                "tables": [],
                "internal_links": [],
                "external_links": [],
                "media_assets": [],
                "screenshot_b64": None,
                "pdf_b64": None,
                "error": str(exc),
            })

    total_elapsed = round(time.perf_counter() - job_t0, 2)

    # Aggregate summary across all crawled pages
    all_media: List[Dict[str, str]] = []
    seen_m: Set[str] = set()
    all_tables: List[Dict[str, Any]] = []
    all_internal: List[Dict[str, str]] = []
    seen_int: Set[str] = set()
    all_external: List[Dict[str, str]] = []
    seen_ext: Set[str] = set()

    combined_md_parts: List[str] = []

    for idx, p in enumerate(pages_results):
        header_prefix = f"# [{idx + 1}/{len(pages_results)}] {p.get('title') or p.get('url')}\n**URL:** {p.get('url')} | **Status:** {p.get('status_code')} | **Motor:** `{p.get('engine_used')}`\n\n"
        combined_md_parts.append(header_prefix + (p.get("markdown") or ""))

        for m in p.get("media_assets", []):
            if m["url"] not in seen_m:
                seen_m.add(m["url"])
                all_media.append({**m, "source_page": p["url"]})
        for t in p.get("tables", []):
            all_tables.append({**t, "source_page": p["url"]})
        for l in p.get("internal_links", []):
            if l["url"] not in seen_int:
                seen_int.add(l["url"])
                all_internal.append(l)
        for l in p.get("external_links", []):
            if l["url"] not in seen_ext:
                seen_ext.add(l["url"])
                all_external.append(l)

    primary = pages_results[0] if pages_results else {}
    combined_markdown = "\n\n---\n\n".join(combined_md_parts) if len(pages_results) > 1 else (primary.get("markdown") or "")

    return {
        "job_id": hashlib.md5(f"{norm_start}-{time.time()}".encode()).hexdigest()[:12],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "start_url": norm_start,
        "domain": start_domain,
        "engine_requested": engine,
        "engine_used": primary.get("engine_used", engine),
        "follow_links": follow_links,
        "pages_crawled": len(pages_results),
        "total_elapsed_seconds": total_elapsed,
        "title": primary.get("title") or norm_start,
        "description": primary.get("description") or "",
        "combined_markdown": combined_markdown,
        "pages": pages_results,
        "summary": {
            "total_words": len(combined_markdown.split()),
            "total_chars": len(combined_markdown),
            "total_tables": len(all_tables),
            "total_internal_links": len(all_internal),
            "total_external_links": len(all_external),
            "total_media_assets": len(all_media),
            "has_screenshot": bool(primary.get("screenshot_b64")),
            "has_pdf": bool(primary.get("pdf_b64")),
        },
        "aggregated": {
            "tables": all_tables,
            "internal_links": all_internal,
            "external_links": all_external,
            "media_assets": all_media,
        },
    }
