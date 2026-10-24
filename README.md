# OmniCrawler Studio 🕷️

> **Multi-Engine Local Web Crawler & Selective Archiver** com visualizador interativo em Canvas sincronizado com a taxa de atualização (Hz) do monitor.

---

## 🚀 Funcionalidades

- **4 Motores de Crawling Locais & Gratuitos**:
  - `Auto`: Modo híbrido inteligente.
  - `Crawl4AI`: Renderização dinâmica com Playwright, limpeza de ruídos e conversão para Markdown puro.
  - `Playwright Stealth`: Navegador Chromium headless completo com suporte a rolagem automática, Screenshot em resolução total (PNG) e impressão em PDF.
  - `Trafilatura + HTTPX`: Extração ultrarrápida (~0.4s) direta de corpo de texto, notícias e documentações.
  - `Scrapy / Parsel`: Extração via seletores CSS/XPath customizados, tabelas e dados estruturados JSON-LD.
- **Simulador Físico de Varredura (Hz-Synced)**:
  - Animação procedimental de aranha com cinemática inversa (8 patas articuladas).
  - Coleta visual item a item pela árvore DOM (Títulos, Parágrafos, Mídias, Tabelas, Links) sincronizada com o refresh rate do monitor (60Hz a 240Hz+).
  - Controles interativos: pausar, avançar passo a passo, guiar com o cursor do mouse e velocidade ajustável.
- **Arquivamento Seletivo & Modular**:
  - Escolha exatamente o que salvar sob demanda: Markdown/Texto limpo, HTML bruto original, capturas visuais (PNG/PDF), tabelas (CSV/JSON), lista de links e download de arquivos de mídia.
  - Banco de dados local SQLite (`omnicrawler.db`) para histórico e exportação em formato `.zip`.
- **Interface Gráfica**:
  - Estilização dark mode limpa e funcional (Zinc/Carbon).
  - Pesquisa em tempo real dentro do conteúdo extraído.
  - Alternador entre Código Markdown e Modo Leitura Formatada.
  - Estimativa de tokens para modelos de linguagem (LLMs) e botão de cópia formatada para prompts de IA.

---

## 🛠️ Instalação e Execução

### Pré-requisitos
- Python 3.10 ou superior
- Navegador Chromium (instalado automaticamente via Playwright)

### Passo a Passo

1. **Clone o repositório:**
   ```bash
   git clone https://github.com/Caveira765/OmniCrawler.git
   cd OmniCrawler
   ```

2. **Crie e ative um ambiente virtual:**
   ```bash
   python -m venv .venv
   # No Windows:
   .\.venv\Scripts\activate
   # No Linux/macOS:
   source .venv/bin/activate
   ```

3. **Instale as dependências:**
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

4. **Inicie a aplicação:**
   ```bash
   python app.py
   ```
   Ou no Windows, execute diretamente o arquivo `Iniciar_OmniCrawler.bat`.

5. **Acesse no navegador:**
   [http://127.0.0.1:8765](http://127.0.0.1:8765)

---

## 📂 Estrutura do Projeto

```
OmniCrawler/
├── app.py                  # Servidor FastAPI com rotas de API e entrega estática
├── crawler_engines.py      # Implementação dos motores de busca e extração DOM
├── storage_manager.py      # Gestor de gravação seletiva, banco SQLite e compactação ZIP
├── requirements.txt        # Dependências do projeto
├── Iniciar_OmniCrawler.bat # Script de inicialização rápida no Windows
└── static/
    ├── index.html          # Interface gráfica (HTML5/CSS3/Vanilla JS)
    └── spider_sim.js       # Motor gráfico da aranha e animação física em Canvas
```

---

## 📄 Licença

Este projeto é disponibilizado sob a licença [MIT](LICENSE).
