@echo off
chcp 65001 >nul
title OmniCrawler Studio — Multi-Engine Web Crawler Local
cd /d "%~dp0"
echo ============================================================================
echo   Iniciando OmniCrawler Studio em http://127.0.0.1:8765
echo   Motores instalados: Crawl4AI, Playwright, Trafilatura, Scrapy/Parsel
echo ============================================================================
start "" "http://127.0.0.1:8765"
".\.venv\Scripts\python.exe" app.py
pause
