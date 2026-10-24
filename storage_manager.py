import asyncio
import base64
import csv
import io
import json
import os
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

BASE_DIR = Path(__file__).resolve().parent
SAVED_DIR = BASE_DIR / "saved_crawls"
DB_PATH = BASE_DIR / "omnicrawler.db"

SAVED_DIR.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_crawls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                saved_at TEXT NOT NULL,
                start_url TEXT NOT NULL,
                domain TEXT NOT NULL,
                title TEXT,
                engine_used TEXT,
                pages_crawled INTEGER DEFAULT 1,
                options_saved_json TEXT,
                files_saved_json TEXT,
                folder_path TEXT NOT NULL,
                total_size_bytes INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()


init_db()


def slugify(value: str, max_len: int = 50) -> str:
    value = re.sub(r"[^\w\s-]", "_", value.strip().lower())
    value = re.sub(r"[-\s_]+", "_", value).strip("_")
    return (value or "crawl")[:max_len]


async def download_media_files(
    media_list: List[Dict[str, str]],
    target_media_dir: Path,
    max_files: int = 40,
) -> List[Dict[str, Any]]:
    """Downloads images, PDFs, documents, and other media assets concurrently."""
    target_media_dir.mkdir(parents=True, exist_ok=True)
    items = media_list[:max_files]
    downloaded: List[Dict[str, Any]] = []
    sem = asyncio.Semaphore(6)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=20.0,
        verify=False,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
        },
    ) as client:

        async def fetch_one(idx: int, item: Dict[str, str]):
            url = item.get("url", "")
            m_type = item.get("type", "asset")
            if not url:
                return
            async with sem:
                try:
                    resp = await client.get(url)
                    if resp.status_code >= 400:
                        return
                    parsed = urlparse(url)
                    raw_name = os.path.basename(parsed.path) or f"asset_{idx + 1}"
                    safe_name = re.sub(r"[^\w.\-]", "_", raw_name)[:80]
                    if "." not in safe_name:
                        ctype = resp.headers.get("content-type", "").lower()
                        if "pdf" in ctype:
                            safe_name += ".pdf"
                        elif "png" in ctype:
                            safe_name += ".png"
                        elif "jpeg" in ctype or "jpg" in ctype:
                            safe_name += ".jpg"
                        elif "webp" in ctype:
                            safe_name += ".webp"
                        elif "svg" in ctype:
                            safe_name += ".svg"
                    sub_dir = target_media_dir / slugify(m_type, 20)
                    sub_dir.mkdir(parents=True, exist_ok=True)
                    file_path = sub_dir / f"{idx + 1:03d}_{safe_name}"
                    file_path.write_bytes(resp.content)
                    downloaded.append({
                        "url": url,
                        "type": m_type,
                        "saved_file": str(file_path.relative_to(target_media_dir.parent)),
                        "size_bytes": len(resp.content),
                    })
                except Exception:
                    pass

        await asyncio.gather(*(fetch_one(i, m) for i, m in enumerate(items)))

    return downloaded


async def save_selected_crawl_data(
    job_data: Dict[str, Any],
    save_options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Saves ONLY the user-selected categories from a crawl job into a dedicated folder
    and records the entry in the local SQLite database.

    Supported boolean flags in `save_options`:
      - save_clean_content: Markdown (.md), Plain Text (.txt), Structured JSON (.json)
      - save_tables_links: Extracted HTML Tables (.csv/.json) + Internal/External Links (.csv/.json)
      - save_original_html: Raw HTML source (.html) for primary and sub-pages
      - save_visual_capture: Full-page Screenshot (.png) and Printable Page (.pdf)
      - save_media_files: Downloads actual images, PDFs, and documents found on the page
      - selected_media_urls: Optional list of specific media URLs to download (if empty, downloads all up to max_media_files)
      - max_media_files: Max number of media assets to download (default 35)
    """
    opt_clean = bool(save_options.get("save_clean_content", True))
    opt_tables_links = bool(save_options.get("save_tables_links", False))
    opt_html = bool(save_options.get("save_original_html", False))
    opt_visual = bool(save_options.get("save_visual_capture", False))
    opt_media = bool(save_options.get("save_media_files", False))
    max_media = int(save_options.get("max_media_files", 35))
    selected_media_urls = set(save_options.get("selected_media_urls") or [])

    if not any([opt_clean, opt_tables_links, opt_html, opt_visual, opt_media]):
        raise ValueError("Selecione pelo menos uma categoria para salvar.")

    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    domain_slug = slugify(job_data.get("domain") or "site", 35)
    job_id = job_data.get("job_id") or now_str
    folder_name = f"{now_str}_{domain_slug}_{job_id[:6]}"
    folder_path = SAVED_DIR / folder_name
    folder_path.mkdir(parents=True, exist_ok=True)

    saved_files: List[Dict[str, Any]] = []
    pages = job_data.get("pages") or []
    primary = pages[0] if pages else {}

    def record_file(rel_path: str, category: str, full_p: Path):
        if full_p.exists():
            saved_files.append({
                "name": rel_path,
                "category": category,
                "size_bytes": full_p.stat().st_size,
            })

    # 1. Clean Content (Markdown + Plain Text + Structured Metadata JSON)
    if opt_clean:
        md_path = folder_path / "conteudo_limpo.md"
        md_content = job_data.get("combined_markdown") or primary.get("markdown") or ""
        md_path.write_text(md_content, encoding="utf-8")
        record_file("conteudo_limpo.md", "clean_content", md_path)

        txt_path = folder_path / "texto_puro.txt"
        all_txt = "\n\n====================\n\n".join(
            f"[{p.get('url')}]\n{p.get('plain_text') or ''}" for p in pages
        )
        txt_path.write_text(all_txt, encoding="utf-8")
        record_file("texto_puro.txt", "clean_content", txt_path)

        structured_payload = {
            "job_id": job_data.get("job_id"),
            "start_url": job_data.get("start_url"),
            "domain": job_data.get("domain"),
            "title": job_data.get("title"),
            "description": job_data.get("description"),
            "engine_used": job_data.get("engine_used"),
            "created_at": job_data.get("created_at"),
            "pages_crawled": job_data.get("pages_crawled"),
            "summary": job_data.get("summary"),
            "pages_metadata": [
                {
                    "url": p.get("url"),
                    "status_code": p.get("status_code"),
                    "title": p.get("title"),
                    "description": p.get("description"),
                    "meta_tags": p.get("meta_tags"),
                    "headings": p.get("headings"),
                    "custom_matches": p.get("custom_matches"),
                    "json_ld": p.get("json_ld"),
                }
                for p in pages
            ],
        }
        json_path = folder_path / "dados_estruturados.json"
        json_path.write_text(
            json.dumps(structured_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        record_file("dados_estruturados.json", "clean_content", json_path)

    # 2. Tables & Links (CSV + JSON)
    if opt_tables_links:
        agg = job_data.get("aggregated") or {}
        internal_links = agg.get("internal_links") or primary.get("internal_links") or []
        external_links = agg.get("external_links") or primary.get("external_links") or []
        tables = agg.get("tables") or primary.get("tables") or []

        links_csv_path = folder_path / "links_extraidos.csv"
        with links_csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["tipo", "texto", "url"])
            for l in internal_links:
                writer.writerow(["interno", l.get("text", ""), l.get("url", "")])
            for l in external_links:
                writer.writerow(["externo", l.get("text", ""), l.get("url", "")])
        record_file("links_extraidos.csv", "tables_links", links_csv_path)

        links_json_path = folder_path / "links_extraidos.json"
        links_json_path.write_text(
            json.dumps(
                {"internal_links": internal_links, "external_links": external_links},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        record_file("links_extraidos.json", "tables_links", links_json_path)

        if tables:
            tables_dir = folder_path / "tabelas"
            tables_dir.mkdir(parents=True, exist_ok=True)
            tables_json_path = tables_dir / "todas_tabelas.json"
            tables_json_path.write_text(
                json.dumps(tables, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            record_file("tabelas/todas_tabelas.json", "tables_links", tables_json_path)

            for idx, tbl in enumerate(tables):
                t_csv = tables_dir / f"tabela_{idx + 1:02d}.csv"
                with t_csv.open("w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    if tbl.get("headers"):
                        writer.writerow(tbl["headers"])
                    for row in tbl.get("rows", []):
                        writer.writerow(row)
                record_file(f"tabelas/tabela_{idx + 1:02d}.csv", "tables_links", t_csv)

    # 3. Original HTML Source
    if opt_html:
        if len(pages) == 1:
            html_path = folder_path / "pagina_original.html"
            html_path.write_text(primary.get("raw_html") or "", encoding="utf-8")
            record_file("pagina_original.html", "original_html", html_path)
        else:
            html_dir = folder_path / "html_paginas"
            html_dir.mkdir(parents=True, exist_ok=True)
            for idx, p in enumerate(pages):
                p_slug = slugify(p.get("title") or p.get("url") or f"page_{idx + 1}", 40)
                h_file = html_dir / f"pagina_{idx + 1:02d}_{p_slug}.html"
                h_file.write_text(p.get("raw_html") or "", encoding="utf-8")
                record_file(f"html_paginas/{h_file.name}", "original_html", h_file)

    # 4. Visual Capture (Screenshot PNG + Printable PDF)
    if opt_visual:
        for idx, p in enumerate(pages):
            suffix = "" if idx == 0 else f"_pagina_{idx + 1:02d}"
            scr_b64 = p.get("screenshot_b64")
            if scr_b64:
                try:
                    png_path = folder_path / f"screenshot{suffix}.png"
                    png_path.write_bytes(base64.b64decode(scr_b64))
                    record_file(png_path.name, "visual_capture", png_path)
                except Exception:
                    pass
            pdf_b64 = p.get("pdf_b64")
            if pdf_b64:
                try:
                    pdf_path = folder_path / f"pagina_visual{suffix}.pdf"
                    pdf_path.write_bytes(base64.b64decode(pdf_b64))
                    record_file(pdf_path.name, "visual_capture", pdf_path)
                except Exception:
                    pass

    # 5. Media & Files Download (Images, PDFs, Documents)
    if opt_media:
        agg = job_data.get("aggregated") or {}
        all_media = agg.get("media_assets") or primary.get("media_assets") or []
        if selected_media_urls:
            filtered_media = [m for m in all_media if m.get("url") in selected_media_urls]
        else:
            filtered_media = all_media

        if filtered_media:
            media_dir = folder_path / "midias_arquivos"
            downloaded = await download_media_files(
                filtered_media,
                target_media_dir=media_dir,
                max_files=max_media,
            )
            for d in downloaded:
                full_m = folder_path / d["saved_file"]
                record_file(d["saved_file"].replace("\\", "/"), "media_files", full_m)

    total_bytes = sum(f["size_bytes"] for f in saved_files)
    saved_at = datetime.now().isoformat(timespec="seconds")

    options_summary = {
        "save_clean_content": opt_clean,
        "save_tables_links": opt_tables_links,
        "save_original_html": opt_html,
        "save_visual_capture": opt_visual,
        "save_media_files": opt_media,
    }

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO saved_crawls (
                job_id, created_at, saved_at, start_url, domain, title,
                engine_used, pages_crawled, options_saved_json, files_saved_json,
                folder_path, total_size_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                job_data.get("created_at") or saved_at,
                saved_at,
                job_data.get("start_url") or "",
                job_data.get("domain") or "",
                job_data.get("title") or "",
                job_data.get("engine_used") or "",
                int(job_data.get("pages_crawled") or 1),
                json.dumps(options_summary, ensure_ascii=False),
                json.dumps(saved_files, ensure_ascii=False),
                str(folder_path),
                total_bytes,
            ),
        )
        record_id = cur.lastrowid
        conn.commit()

    return {
        "record_id": record_id,
        "job_id": job_id,
        "saved_at": saved_at,
        "folder_name": folder_name,
        "folder_path": str(folder_path),
        "options_saved": options_summary,
        "files_saved": saved_files,
        "files_count": len(saved_files),
        "total_size_bytes": total_bytes,
    }


def build_zip_bytes_for_folder(folder_path_str: str) -> bytes:
    folder = Path(folder_path_str)
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError("Pasta de salvamento não encontrada.")

    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(folder):
            for file in files:
                abs_f = Path(root) / file
                rel_f = abs_f.relative_to(folder)
                zf.write(abs_f, arcname=str(rel_f))
    mem_zip.seek(0)
    return mem_zip.read()


def list_saved_history(limit: int = 50) -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM saved_crawls
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    results: List[Dict[str, Any]] = []
    for r in rows:
        results.append({
            "id": r["id"],
            "job_id": r["job_id"],
            "created_at": r["created_at"],
            "saved_at": r["saved_at"],
            "start_url": r["start_url"],
            "domain": r["domain"],
            "title": r["title"],
            "engine_used": r["engine_used"],
            "pages_crawled": r["pages_crawled"],
            "options_saved": json.loads(r["options_saved_json"] or "{}"),
            "files_saved": json.loads(r["files_saved_json"] or "[]"),
            "folder_path": r["folder_path"],
            "folder_exists": Path(r["folder_path"]).exists(),
            "total_size_bytes": r["total_size_bytes"],
        })
    return results


def delete_saved_record(record_id: int, delete_disk_files: bool = True) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT folder_path FROM saved_crawls WHERE id = ?", (record_id,)).fetchone()
        if not row:
            return False
        folder_p = Path(row["folder_path"])
        if delete_disk_files and folder_p.exists() and SAVED_DIR in folder_p.parents:
            shutil.rmtree(folder_p, ignore_errors=True)
        conn.execute("DELETE FROM saved_crawls WHERE id = ?", (record_id,))
        conn.commit()
    return True
