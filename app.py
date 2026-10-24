import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crawler_engines import (
    HAS_CRAWL4AI,
    HAS_PLAYWRIGHT,
    HAS_TRAFILATURA,
    execute_crawl_job,
)
from storage_manager import (
    SAVED_DIR,
    build_zip_bytes_for_folder,
    delete_saved_record,
    list_saved_history,
    save_selected_crawl_data,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="OmniCrawler Studio",
    description="Multi-Engine Local Web Crawler & Selective Archiver (100% Gratuito)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory cache of recent crawl jobs so the user can inspect and save different combinations anytime
RECENT_JOBS: Dict[str, Dict[str, Any]] = {}
MAX_CACHED_JOBS = 25


class SaveSelectionModel(BaseModel):
    save_clean_content: bool = Field(
        default=True,
        description="Salvar conteúdo limpo (.md Markdown, .txt Texto, .json Dados Estruturados)",
    )
    save_tables_links: bool = Field(
        default=True,
        description="Salvar tabelas HTML extraídas (.csv/.json) e lista de links (.csv/.json)",
    )
    save_original_html: bool = Field(
        default=False,
        description="Salvar código-fonte HTML bruto original da(s) página(s)",
    )
    save_visual_capture: bool = Field(
        default=False,
        description="Salvar Screenshot de página inteira (.png) e captura visual (.pdf)",
    )
    save_media_files: bool = Field(
        default=False,
        description="Baixar arquivos e mídias encontrados na página (Imagens, PDFs, documentos)",
    )
    selected_media_urls: Optional[List[str]] = Field(
        default=None,
        description="Lista opcional de URLs de mídia específicas selecionadas pelo usuário",
    )
    max_media_files: int = Field(default=35, ge=1, le=200)


class CrawlRequestModel(BaseModel):
    url: str
    engine: str = "auto"  # auto | crawl4ai | playwright | trafilatura | scrapy_parsel
    follow_links: bool = False
    max_pages: int = Field(default=1, ge=1, le=50)
    max_depth: int = Field(default=1, ge=1, le=4)
    same_domain_only: bool = True
    url_pattern: Optional[str] = None
    wait_ms: int = Field(default=1200, ge=0, le=15000)
    scroll_page: bool = True
    capture_visuals: bool = True
    custom_headers: Optional[Dict[str, str]] = None
    custom_css_selector: Optional[str] = None
    custom_xpath: Optional[str] = None
    auto_save: bool = False
    save_options: Optional[SaveSelectionModel] = None


class SaveFromJobRequest(BaseModel):
    job_id: str
    save_options: SaveSelectionModel


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Dashboard index.html não encontrado.</h1>", status_code=404)
    return FileResponse(index_file)


@app.get("/api/engines")
async def get_available_engines():
    """Returns metadata, disk usage info, and status of all installed free crawler engines."""
    return {
        "engines": [
            {
                "id": "auto",
                "name": "Modo Auto Inteligente (Recomendado)",
                "badge": "Híbrido",
                "installed": True,
                "disk_info": "Compartilhado (0 MB extra)",
                "speed": "Adaptativo",
                "js_rendering": True,
                "visual_capture": True,
                "description": "Escolhe automaticamente entre Crawl4AI/Playwright (quando precisa de JS, Screenshot e PDF) ou Seletores/Trafilatura.",
            },
            {
                "id": "crawl4ai",
                "name": "Crawl4AI (Python + Playwright)",
                "badge": "IA & Markdown",
                "installed": HAS_CRAWL4AI,
                "disk_info": "~45 MB pacote Python (compartilha Chromium)",
                "speed": "Médio (~2–5s/página)",
                "js_rendering": True,
                "visual_capture": True,
                "description": "O melhor para extrair conteúdo limpo em Markdown estruturado para leitura/IA, suportando páginas dinâmicas (React/SPA), Screenshot e PDF.",
            },
            {
                "id": "playwright",
                "name": "Playwright Stealth Full Browser",
                "badge": "Visual & JS Pesado",
                "installed": HAS_PLAYWRIGHT,
                "disk_info": "~37 MB pacote + ~180 MB Chromium compartilhado",
                "speed": "Médio (~2–5s/página)",
                "js_rendering": True,
                "visual_capture": True,
                "description": "Navegador Chromium headless completo com scroll automático para lazy-loading, Screenshot full-page em alta resolução e geração nativa de PDF.",
            },
            {
                "id": "trafilatura",
                "name": "Trafilatura + HTTPX Ultra-Rápido",
                "badge": "Ultra-Leve",
                "installed": HAS_TRAFILATURA,
                "disk_info": "~18 MB (sem abrir navegador)",
                "speed": "Instantâneo (~0.3–1s/página)",
                "js_rendering": False,
                "visual_capture": False,
                "description": "Extrator linguístico de altíssima velocidade para notícias, artigos, blogs, wikis e documentação estática sem gastar RAM de navegador.",
            },
            {
                "id": "scrapy_parsel",
                "name": "Scrapy / Parsel Spider Engine",
                "badge": "Seletores & Deep Crawl",
                "installed": True,
                "disk_info": "~28 MB",
                "speed": "Muito Rápido (~0.4–1.2s/página)",
                "js_rendering": False,
                "visual_capture": False,
                "description": "Motor de seletores CSS/XPath do Scrapy + extração de JSON-LD, tabelas e varredura rápida de múltiplas páginas internas.",
            },
        ],
        "saved_directory": str(SAVED_DIR),
    }


@app.post("/api/crawl")
async def api_run_crawl(req: CrawlRequestModel):
    try:
        job_result = await execute_crawl_job(
            start_url=req.url,
            engine=req.engine,
            follow_links=req.follow_links,
            max_pages=req.max_pages,
            max_depth=req.max_depth,
            same_domain_only=req.same_domain_only,
            url_pattern=req.url_pattern,
            wait_ms=req.wait_ms,
            scroll_page=req.scroll_page,
            capture_visuals=req.capture_visuals,
            custom_headers=req.custom_headers,
            custom_css_selector=req.custom_css_selector,
            custom_xpath=req.custom_xpath,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Erro na varredura: {exc}")

    # Store in recent jobs memory cache
    if len(RECENT_JOBS) >= MAX_CACHED_JOBS:
        oldest_key = next(iter(RECENT_JOBS))
        RECENT_JOBS.pop(oldest_key, None)
    RECENT_JOBS[job_result["job_id"]] = job_result

    saved_info = None
    if req.auto_save and req.save_options is not None:
        try:
            saved_info = await save_selected_crawl_data(
                job_data=job_result,
                save_options=req.save_options.model_dump(),
            )
        except Exception as save_exc:
            saved_info = {"error": str(save_exc)}

    return {
        "job": job_result,
        "auto_saved": saved_info,
    }


@app.post("/api/save")
async def api_save_selected(req: SaveFromJobRequest):
    job_data = RECENT_JOBS.get(req.job_id)
    if not job_data:
        raise HTTPException(
            status_code=404,
            detail="Sessão desta coleta expirou da memória. Execute o request novamente para salvar.",
        )
    try:
        saved_info = await save_selected_crawl_data(
            job_data=job_data,
            save_options=req.save_options.model_dump(),
        )
        return saved_info
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao salvar arquivos: {exc}")


@app.get("/api/history")
async def api_get_history():
    return {
        "items": list_saved_history(limit=60),
        "saved_directory": str(SAVED_DIR),
    }


@app.get("/api/history/{record_id}/zip")
async def api_download_saved_zip(record_id: int):
    items = list_saved_history(limit=200)
    target = next((x for x in items if x["id"] == record_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    try:
        zip_bytes = build_zip_bytes_for_folder(target["folder_path"])
        folder_name = Path(target["folder_path"]).name
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{folder_name}.zip"'
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/history/{record_id}/open-folder")
async def api_open_folder_in_explorer(record_id: int):
    items = list_saved_history(limit=200)
    target = next((x for x in items if x["id"] == record_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    folder = Path(target["folder_path"])
    if not folder.exists():
        raise HTTPException(status_code=404, detail="A pasta não existe mais no disco.")
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", str(folder)])
    return {"opened": str(folder)}


@app.post("/api/open-root-saved-folder")
async def api_open_root_folder():
    SAVED_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", str(SAVED_DIR)])
    return {"opened": str(SAVED_DIR)}


@app.delete("/api/history/{record_id}")
async def api_delete_history_item(record_id: int, delete_files: bool = True):
    ok = delete_saved_record(record_id, delete_disk_files=delete_files)
    if not ok:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    return {"deleted": record_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=False)
