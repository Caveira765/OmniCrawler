/**
 * OmniCrawler Studio — Realistic Delta-Time (Hz-Synced) Arachnid & Item-by-Item Page Harvester v2.0
 *
 * Enhancements:
 * - Frame-rate independent physics (Delta-Time in seconds) synced to any monitor refresh rate (60Hz–240Hz+).
 * - Interactive playback controls: Pause/Resume, Step Next Item, Speed Multiplier, and Click-to-Harvest any DOM node.
 * - Hover inspection of DOM nodes on the scrollable page surface + vertical page mini-map rail.
 * - Footstep surface micro-ripples, vibrating catenary silk trunk lines, and radial silk wraps on harvested items.
 */

class PhysicalSpiderEngine {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.mode = options.mode || 'side'; // 'stage' | 'side'
    this.running = true;
    this.paused = false;

    // Callbacks for item-by-item harvesting
    this.onItemHarvested = options.onItemHarvested || null;
    this.onAllHarvested = options.onAllHarvested || null;
    this.onNodeClicked = options.onNodeClicked || null;

    // Timing & Monitor Hz detection
    this.lastTime = performance.now();
    this.fpsSamples = [];
    this.detectedHz = 60;
    this.speedMultiplier = options.speedMultiplier || 1.0;

    // Virtual scrollable page dimensions
    this.viewW = 480;
    this.viewH = 340;
    this.virtualPageH = 900;
    this.cameraY = 0;
    this.targetCameraY = 0;
    this.manualScrollOverrideTimer = 0;

    // Spider physical state (in world/page coordinates)
    this.x = 220;
    this.y = 95;
    this.vx = 0;
    this.vy = 0;
    this.angle = Math.PI * 0.5;
    this.targetX = 220;
    this.targetY = 120;
    this.baseWalkSpeed = options.baseWalkSpeed || 132; // pixels per second
    this.scale = options.scale || 1.0;

    // State Machine: 'PRE_CONNECT' | 'WALKING_TO_ITEM' | 'GRABBING_ITEM' | 'COMPLETED_IDLE'
    this.state = 'PRE_CONNECT';
    this.grabTimer = 0;
    this.grabDuration = 0.80; // seconds spent physically grabbing each item

    // Subtle monochrome/metallic accent palette
    this.accentColor = '#f4f4f5';
    this.accentGlow = '#38bdf8';
    this.engineLabel = 'AUTO';

    // 8 Articulated Arachnid Legs
    this.legs = [];
    this.initLegs();

    // Silk web network, cocoons & flying extracted packets
    this.silkLines = [];
    this.lastAnchor = { x: this.x, y: this.y };
    this.flyingPackets = [];
    this.ripples = [];

    // Page items to harvest one by one
    this.harvestNodes = [];
    this.currentNodeIndex = 0;
    this.hoveredNodeIndex = -1;
    this.harvestedCount = 0;
    this.bgImage = null;
    this.followMouse = false;
    this.mouseWorldPos = null;
    this.statusText = 'Pronto — Aguardando página ou comando';

    this.resize();
    this.loadPlaceholderPageNodes();
    this.bindEvents();
    requestAnimationFrame((t) => this.loop(t));
  }

  initLegs() {
    this.legs = [];
    const mountAngles = [-0.50, -0.96, -1.50, -2.16, 0.50, 0.96, 1.50, 2.16];
    const reachDistances = [45, 53, 51, 47, 45, 53, 51, 47];

    for (let i = 0; i < 8; i++) {
      const a = mountAngles[i];
      const d = reachDistances[i] * this.scale;
      const fx = this.x + Math.cos(this.angle + a) * d;
      const fy = this.y + Math.sin(this.angle + a) * d;
      this.legs.push({
        index: i,
        side: i < 4 ? -1 : 1,
        pair: i % 4,
        mountAngle: a,
        reach: d,
        footX: fx,
        footY: fy,
        startX: fx,
        startY: fy,
        nextX: fx,
        nextY: fy,
        stepProgress: 1.0,
        stepDuration: 0.155,
      });
    }
  }

  setEngineTheme(engineId) {
    const id = (engineId || 'auto').toLowerCase();
    this.engineLabel = id.toUpperCase();
    if (id === 'crawl4ai') {
      this.accentGlow = '#38bdf8';
    } else if (id === 'playwright') {
      this.accentGlow = '#a78bfa';
    } else if (id === 'trafilatura') {
      this.accentGlow = '#34d399';
    } else if (id === 'scrapy_parsel') {
      this.accentGlow = '#fbbf24';
    } else {
      this.accentGlow = '#38bdf8';
    }
  }

  setSpeedMultiplier(mult) {
    this.speedMultiplier = Math.max(0.25, Math.min(3.5, parseFloat(mult) || 1.0));
  }

  togglePause() {
    this.paused = !this.paused;
    return this.paused;
  }

  stepToNextItem() {
    if (!this.harvestNodes.length) return;
    const cur = this.harvestNodes[this.currentNodeIndex];
    if (cur && !cur.collected) {
      cur.collected = true;
      cur.webWrapProgress = 1.0;
      this.harvestedCount++;
      if (this.isRealData && this.onItemHarvested) {
        this.onItemHarvested(cur, this.harvestedCount, this.harvestNodes.length);
      }
    }
    const nextIdx = this.harvestNodes.findIndex((n) => !n.collected);
    if (nextIdx !== -1) {
      this.currentNodeIndex = nextIdx;
      this.state = 'WALKING_TO_ITEM';
      this.aimAtNode(nextIdx);
      this.x = this.targetX;
      this.y = this.targetY;
      this.initLegs();
    } else {
      this.completeAllImmediately();
    }
  }

  setPageScreenshot(base64Data) {
    if (!base64Data) {
      this.bgImage = null;
      return;
    }
    const img = new Image();
    img.onload = () => {
      this.bgImage = img;
    };
    img.src = base64Data.startsWith('data:') ? base64Data : `data:image/png;base64,${base64Data}`;
  }

  resize() {
    if (!this.canvas) return;
    const rect = this.canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(Math.floor(rect.width || 480), 280);
    const h = Math.max(Math.floor(rect.height || 320), 220);

    this.viewW = w;
    this.viewH = h;
    this.canvas.width = w * dpr;
    this.canvas.height = h * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    this.recomputeNodeCoordinates();
  }

  loadPlaceholderPageNodes(urlLabel = 'https://pagina-alvo.com') {
    this.bgImage = null;
    const rawNodes = [
      { index: 1, category: 'meta', tag: '<head>', title: 'Handshake & Cabeçalho HTTP', snippet: urlLabel, x_pct: 0.48, y_pct: 0.08 },
      { index: 2, category: 'heading', tag: '<h1>', title: 'Estrutura de Títulos (H1–H3)', snippet: 'Mapeando hierarquia semântica...', x_pct: 0.34, y_pct: 0.22 },
      { index: 3, category: 'paragraph', tag: '<article>', title: 'Extração de Corpo de Texto', snippet: 'Filtrando menus e convertendo para Markdown...', x_pct: 0.65, y_pct: 0.38 },
      { index: 4, category: 'media', tag: '<img>', title: 'Mapeamento de Ativos de Mídia', snippet: 'Inspecionando imagens e documentos...', x_pct: 0.32, y_pct: 0.55 },
      { index: 5, category: 'table', tag: '<table>', title: 'Tabelas & Dados Estruturados', snippet: 'Normalizando linhas e cabeçalhos...', x_pct: 0.68, y_pct: 0.72 },
      { index: 6, category: 'link', tag: '<a href>', title: 'Grafo de Links Internos/Externos', snippet: 'Resolvendo URLs absolutas...', x_pct: 0.45, y_pct: 0.88 },
    ];
    this.setHarvestQueue(rawNodes, false);
  }

  startConnectingPhase(targetUrl, engineId) {
    this.paused = false;
    this.setEngineTheme(engineId);
    this.loadPlaceholderPageNodes(targetUrl);
    this.state = 'WALKING_TO_ITEM';
    this.statusText = `Conectando a ${targetUrl}...`;
  }

  startRealHarvestSequence(job) {
    if (!job || !job.pages || !job.pages[0]) return;
    this.paused = false;
    const page = job.pages[0];
    this.setEngineTheme(job.engine_used || 'auto');
    if (page.screenshot_b64) {
      this.setPageScreenshot(page.screenshot_b64);
    } else {
      this.bgImage = null;
    }

    const rawNodes = (page.harvest_nodes && page.harvest_nodes.length > 0)
      ? page.harvest_nodes
      : [
          { index: 1, category: 'meta', tag: '<title>', title: page.title || job.domain, snippet: page.description || page.url, x_pct: 0.45, y_pct: 0.1 },
          { index: 2, category: 'paragraph', tag: '<markdown>', title: `${job.summary?.total_words || 0} palavras extraídas`, snippet: (page.plain_text || '').slice(0, 120), x_pct: 0.35, y_pct: 0.4 },
          { index: 3, category: 'link', tag: '<a>', title: `${job.summary?.total_internal_links || 0} links internos`, snippet: page.url, x_pct: 0.65, y_pct: 0.75 },
        ];

    this.setHarvestQueue(rawNodes, true);
  }

  setHarvestQueue(rawNodes, isRealData = true) {
    this.isRealData = isRealData;
    const count = rawNodes.length;
    this.virtualPageH = Math.max(this.viewH - 36, Math.max(520, count * 70 + 95));

    this.harvestNodes = rawNodes.map((n, i) => ({
      ...n,
      id: i + 1,
      collected: false,
      webWrapProgress: 0,
      cardW: Math.min(260, Math.max(170, (this.viewW - 44) * 0.46)),
      cardH: 54,
      worldX: 0,
      worldY: 0,
    }));

    this.recomputeNodeCoordinates();
    this.silkLines = [];
    this.flyingPackets = [];
    this.ripples = [];
    this.currentNodeIndex = 0;
    this.harvestedCount = 0;
    this.cameraY = 0;
    this.targetCameraY = 0;

    this.x = this.viewW * 0.5;
    this.y = 54;
    this.lastAnchor = { x: this.x, y: this.y };
    this.initLegs();

    if (this.harvestNodes.length > 0) {
      this.state = 'WALKING_TO_ITEM';
      this.aimAtNode(0);
    } else {
      this.state = 'COMPLETED_IDLE';
    }
  }

  recomputeNodeCoordinates() {
    if (!this.harvestNodes || !this.harvestNodes.length) return;
    const usableW = Math.max(250, this.viewW - 42);
    this.harvestNodes.forEach((n, i) => {
      n.cardW = Math.min(265, Math.max(165, Math.floor(usableW * 0.46)));
      n.cardH = 54;
      const col = i % 2;
      const leftMargin = 18;
      const rightMargin = this.viewW - n.cardW - 22;
      n.boxX = col === 0 ? leftMargin : Math.max(leftMargin, rightMargin);
      n.boxY = 54 + i * 68;
      n.worldX = n.boxX + n.cardW * (col === 0 ? 0.74 : 0.26);
      n.worldY = n.boxY + n.cardH * 0.52;
    });
    this.virtualPageH = Math.max(this.viewH - 36, 85 + this.harvestNodes.length * 68);
  }

  aimAtNode(idx) {
    const node = this.harvestNodes[idx];
    if (!node) return;
    this.targetX = node.worldX;
    this.targetY = node.worldY;
    this.statusText = `Navegando até [${idx + 1}/${this.harvestNodes.length}] ${node.tag} — ${node.title}`;
  }

  completeAllImmediately() {
    this.harvestNodes.forEach((n) => {
      if (!n.collected) {
        n.collected = true;
        n.webWrapProgress = 1.0;
        this.harvestedCount++;
        if (this.isRealData && this.onItemHarvested) {
          this.onItemHarvested(n, this.harvestedCount, this.harvestNodes.length);
        }
      }
    });
    this.state = 'COMPLETED_IDLE';
    this.statusText = `Coleta concluída (${this.harvestNodes.length}/${this.harvestNodes.length} itens capturados)`;
    if (this.isRealData && this.onAllHarvested) {
      this.onAllHarvested();
    }
  }

  bindEvents() {
    this.canvas.addEventListener('mousedown', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      if (sy <= 34) return;
      const worldX = sx;
      const worldY = sy - 34 + this.cameraY;

      const clickedIdx = this.harvestNodes.findIndex(
        (n) => worldX >= n.boxX && worldX <= n.boxX + n.cardW && worldY >= n.boxY && worldY <= n.boxY + n.cardH
      );

      if (clickedIdx !== -1) {
        this.paused = false;
        this.currentNodeIndex = clickedIdx;
        this.state = 'WALKING_TO_ITEM';
        this.aimAtNode(clickedIdx);
        if (this.onNodeClicked) {
          this.onNodeClicked(this.harvestNodes[clickedIdx]);
        }
      } else {
        this.silkLines.push({
          x1: this.lastAnchor.x,
          y1: this.lastAnchor.y,
          x2: this.x,
          y2: this.y,
        });
        this.lastAnchor = { x: this.x, y: this.y };
        this.targetX = Math.max(24, Math.min(this.viewW - 28, worldX));
        this.targetY = Math.max(44, Math.min(this.virtualPageH - 28, worldY));
        this.ripples.push({ x: this.targetX, y: this.targetY, r: 3, maxR: 28, alpha: 0.9 });
      }
    });

    this.canvas.addEventListener('mousemove', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const worldX = sx;
      const worldY = Math.max(40, sy - 34 + this.cameraY);
      this.mouseWorldPos = { x: worldX, y: worldY };

      // Check hovered node
      this.hoveredNodeIndex = this.harvestNodes.findIndex(
        (n) => worldX >= n.boxX && worldX <= n.boxX + n.cardW && worldY >= n.boxY && worldY <= n.boxY + n.cardH
      );
      this.canvas.style.cursor = this.hoveredNodeIndex !== -1 ? 'pointer' : 'crosshair';

      if (this.followMouse && this.mouseWorldPos) {
        this.targetX = this.mouseWorldPos.x;
        this.targetY = this.mouseWorldPos.y;
      }
    });

    this.canvas.addEventListener('mouseleave', () => {
      this.hoveredNodeIndex = -1;
    });

    this.canvas.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        const maxCam = Math.max(0, this.virtualPageH - (this.viewH - 34));
        this.targetCameraY = Math.max(0, Math.min(maxCam, this.targetCameraY + e.deltaY * 0.65));
        this.manualScrollOverrideTimer = 2.5; // let user inspect scrolled area for 2.5s before auto-follow resumes
      },
      { passive: false }
    );
  }

  update(dt) {
    if (this.manualScrollOverrideTimer > 0) {
      this.manualScrollOverrideTimer = Math.max(0, this.manualScrollOverrideTimer - dt);
    }

    const viewportContentH = Math.max(180, this.viewH - 34);
    const maxCam = Math.max(0, this.virtualPageH - viewportContentH);
    if (this.manualScrollOverrideTimer <= 0 && (this.state === 'WALKING_TO_ITEM' || this.state === 'GRABBING_ITEM')) {
      const desiredCam = this.y - viewportContentH * 0.45;
      this.targetCameraY = Math.max(0, Math.min(maxCam, desiredCam));
    }
    this.cameraY += (this.targetCameraY - this.cameraY) * Math.min(1, dt * 6.5);

    if (this.paused) return;

    const effDt = dt * this.speedMultiplier;
    const dx = this.targetX - this.x;
    const dy = this.targetY - this.y;
    const dist = Math.hypot(dx, dy);

    if (this.state === 'GRABBING_ITEM' && !this.followMouse) {
      this.vx = 0;
      this.vy = 0;
      this.grabTimer += effDt;

      const node = this.harvestNodes[this.currentNodeIndex];
      if (node) {
        const cardCenterX = node.boxX + node.cardW * 0.5;
        const cardCenterY = node.boxY + node.cardH * 0.5;
        const targetAng = Math.atan2(cardCenterY - this.y, cardCenterX - this.x);
        let diff = targetAng - this.angle;
        while (diff > Math.PI) diff -= Math.PI * 2;
        while (diff < -Math.PI) diff += Math.PI * 2;
        this.angle += diff * Math.min(1, effDt * 10);

        node.webWrapProgress = Math.min(1.0, this.grabTimer / this.grabDuration);
      }

      if (this.grabTimer >= this.grabDuration) {
        if (node && !node.collected) {
          node.collected = true;
          node.webWrapProgress = 1.0;
          this.harvestedCount++;

          this.silkLines.push({
            x1: this.lastAnchor.x,
            y1: this.lastAnchor.y,
            x2: this.x,
            y2: this.y,
          });
          if (this.silkLines.length > 36) this.silkLines.shift();
          this.lastAnchor = { x: this.x, y: this.y };

          this.ripples.push({ x: this.x, y: this.y, r: 4, maxR: 38, alpha: 0.9 });
          this.flyingPackets.push({
            x: this.x,
            y: this.y - this.cameraY + 34,
            targetX: this.viewW - 48,
            targetY: 16,
            progress: 0,
            label: node.tag,
          });

          if (this.isRealData && this.onItemHarvested) {
            this.onItemHarvested(node, this.harvestedCount, this.harvestNodes.length);
          }
        }

        const nextIdx = this.harvestNodes.findIndex((n, idx) => idx > this.currentNodeIndex && !n.collected);
        if (nextIdx !== -1) {
          this.currentNodeIndex = nextIdx;
          this.state = 'WALKING_TO_ITEM';
          this.aimAtNode(nextIdx);
        } else {
          const anyUncollected = this.harvestNodes.findIndex((n) => !n.collected);
          if (anyUncollected !== -1) {
            this.currentNodeIndex = anyUncollected;
            this.state = 'WALKING_TO_ITEM';
            this.aimAtNode(anyUncollected);
          } else if (!this.isRealData && this.harvestNodes.length > 0) {
            this.harvestNodes.forEach((n) => {
              n.collected = false;
              n.webWrapProgress = 0;
            });
            this.currentNodeIndex = 0;
            this.state = 'WALKING_TO_ITEM';
            this.aimAtNode(0);
          } else {
            this.state = 'COMPLETED_IDLE';
            this.statusText = `Varredura concluída — ${this.harvestedCount} itens capturados`;
            if (this.isRealData && this.onAllHarvested) {
              this.onAllHarvested();
            }
          }
        }
      }
    } else {
      if (dist > 5) {
        const desiredAngle = Math.atan2(dy, dx);
        let diff = desiredAngle - this.angle;
        while (diff > Math.PI) diff -= Math.PI * 2;
        while (diff < -Math.PI) diff += Math.PI * 2;

        const maxTurn = 7.5 * effDt;
        this.angle += Math.max(-maxTurn, Math.min(maxTurn, diff * 8.0 * effDt));

        const slowDown = Math.min(1.0, dist / 28);
        const currentSpeed = this.baseWalkSpeed * (0.35 + 0.65 * slowDown);
        const stepLen = Math.min(dist, currentSpeed * effDt);

        this.vx = Math.cos(this.angle) * currentSpeed;
        this.vy = Math.sin(this.angle) * currentSpeed;
        this.x += Math.cos(this.angle) * stepLen;
        this.y += Math.sin(this.angle) * stepLen;
      } else {
        this.vx = 0;
        this.vy = 0;
        if (this.state === 'WALKING_TO_ITEM' && !this.followMouse) {
          this.state = 'GRABBING_ITEM';
          this.grabTimer = 0;
          const node = this.harvestNodes[this.currentNodeIndex];
          if (node) {
            this.statusText = `Capturando [${this.currentNodeIndex + 1}/${this.harvestNodes.length}] ${node.tag}: ${node.title}`;
          }
        }
      }
    }

    // Update 8 articulated legs with tetrapod gait (strictly delta-time driven)
    let groupAMoving = false;
    let groupBMoving = false;
    this.legs.forEach((leg) => {
      if (leg.stepProgress < 1.0) {
        const isA = leg.index === 0 || leg.index === 2 || leg.index === 5 || leg.index === 7;
        if (isA) groupAMoving = true;
        else groupBMoving = true;
      }
    });

    this.legs.forEach((leg) => {
      const worldMountAngle = this.angle + leg.mountAngle;
      const leadX = this.vx * 0.085;
      const leadY = this.vy * 0.085;
      const restX = this.x + Math.cos(worldMountAngle) * leg.reach + leadX;
      const restY = this.y + Math.sin(worldMountAngle) * leg.reach + leadY;

      if (leg.stepProgress < 1.0) {
        leg.stepProgress = Math.min(1.0, leg.stepProgress + effDt / leg.stepDuration);
        const t = leg.stepProgress;
        const ease = t * t * (3 - 2 * t);
        leg.footX = leg.startX + (leg.nextX - leg.startX) * ease;
        leg.footY = leg.startY + (leg.nextY - leg.startY) * ease;

        // Subtle footstep contact ring when foot touches down
        if (leg.stepProgress >= 1.0) {
          this.ripples.push({ x: leg.footX, y: leg.footY, r: 1.5, maxR: 7, alpha: 0.32 });
        }
      } else {
        if (this.state === 'GRABBING_ITEM' && (leg.index === 0 || leg.index === 4)) {
          const tapWave = Math.sin(this.grabTimer * 18 + leg.index) * 5 * this.scale;
          leg.footX = restX + Math.cos(this.angle) * (6 + tapWave);
          leg.footY = restY + Math.sin(this.angle) * (6 + tapWave);
          return;
        }

        const drift = Math.hypot(restX - leg.footX, restY - leg.footY);
        const isA = leg.index === 0 || leg.index === 2 || leg.index === 5 || leg.index === 7;
        const gaitAllowed = isA ? !groupBMoving : !groupAMoving;

        if (drift > 17 * this.scale && (gaitAllowed || drift > 32 * this.scale)) {
          leg.startX = leg.footX;
          leg.startY = leg.footY;
          leg.nextX = restX;
          leg.nextY = restY;
          leg.stepProgress = 0.0;
          if (isA) groupAMoving = true;
          else groupBMoving = true;
        }
      }
    });

    for (let i = this.ripples.length - 1; i >= 0; i--) {
      const rp = this.ripples[i];
      rp.r += 46 * dt;
      rp.alpha -= 1.5 * dt;
      if (rp.alpha <= 0) this.ripples.splice(i, 1);
    }

    for (let i = this.flyingPackets.length - 1; i >= 0; i--) {
      const pk = this.flyingPackets[i];
      pk.progress += dt * 2.2;
      if (pk.progress >= 1.0) {
        this.flyingPackets.splice(i, 1);
      }
    }
  }

  draw(ctx) {
    const w = this.viewW;
    const h = this.viewH;
    const topBarH = 34;

    ctx.clearRect(0, 0, w, h);

    ctx.fillStyle = '#09090b';
    ctx.fillRect(0, 0, w, h);

    ctx.save();
    ctx.beginPath();
    ctx.rect(0, topBarH, w, h - topBarH);
    ctx.clip();
    ctx.translate(0, topBarH - this.cameraY);

    if (this.bgImage) {
      ctx.save();
      ctx.globalAlpha = 0.22;
      const ratio = this.bgImage.width / Math.max(1, this.bgImage.height);
      const drawH = Math.max(this.virtualPageH, w / ratio);
      ctx.drawImage(this.bgImage, 0, 0, w, drawH);
      ctx.restore();
    }

    // Subtle technical grid
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.028)';
    ctx.lineWidth = 1;
    const startGridY = Math.floor(this.cameraY / 32) * 32;
    for (let gx = 0; gx < w; gx += 32) {
      ctx.beginPath();
      ctx.moveTo(gx, this.cameraY);
      ctx.lineTo(gx, this.cameraY + h);
      ctx.stroke();
    }
    for (let gy = startGridY; gy < this.cameraY + h; gy += 32) {
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.lineTo(w, gy);
      ctx.stroke();
    }

    // Draw Page Harvest Items (DOM Nodes on the Page)
    this.harvestNodes.forEach((node, idx) => {
      if (node.boxY + node.cardH < this.cameraY - 24 || node.boxY > this.cameraY + h + 24) return;

      const isTarget = (idx === this.currentNodeIndex && (this.state === 'WALKING_TO_ITEM' || this.state === 'GRABBING_ITEM'));
      const isHovered = (idx === this.hoveredNodeIndex);
      ctx.save();

      ctx.fillStyle = node.collected
        ? 'rgba(24, 24, 27, 0.88)'
        : isTarget
        ? 'rgba(39, 39, 42, 0.95)'
        : isHovered
        ? 'rgba(30, 30, 34, 0.92)'
        : 'rgba(17, 17, 19, 0.84)';
      ctx.strokeStyle = node.collected
        ? 'rgba(161, 161, 170, 0.32)'
        : isTarget
        ? this.accentGlow
        : isHovered
        ? '#a1a1aa'
        : 'rgba(63, 63, 70, 0.6)';
      ctx.lineWidth = isTarget || isHovered ? 1.5 : 1;

      ctx.beginPath();
      ctx.roundRect(node.boxX, node.boxY, node.cardW, node.cardH, 6);
      ctx.fill();
      ctx.stroke();

      // Left status stripe
      ctx.fillStyle = node.collected ? '#71717a' : isTarget ? this.accentGlow : '#3f3f46';
      ctx.beginPath();
      ctx.roundRect(node.boxX, node.boxY, 3.5, node.cardH, [6, 0, 0, 6]);
      ctx.fill();

      // Tag & index pill
      ctx.fillStyle = node.collected ? '#a1a1aa' : '#f4f4f5';
      ctx.font = '600 10px "JetBrains Mono", Consolas, monospace';
      ctx.fillText(`#${node.id} ${node.tag}`, node.boxX + 10, node.boxY + 16);

      ctx.font = '600 9.5px "Segoe UI", sans-serif';
      if (node.collected) {
        ctx.fillStyle = '#a1a1aa';
        ctx.fillText('✓ COLETADO', node.boxX + node.cardW - 68, node.boxY + 15);
      } else if (isTarget && this.state === 'GRABBING_ITEM') {
        ctx.fillStyle = this.accentGlow;
        ctx.fillText('EXTRAINDO...', node.boxX + node.cardW - 72, node.boxY + 15);
      } else if (isHovered) {
        ctx.fillStyle = '#d4d4d8';
        ctx.fillText('IR AQUI →', node.boxX + node.cardW - 58, node.boxY + 15);
      }

      // Item Title & Snippet
      ctx.fillStyle = node.collected ? '#71717a' : '#e4e4e7';
      ctx.font = '500 11px "Segoe UI", sans-serif';
      const maxChars = Math.floor((node.cardW - 20) / 6.2);
      ctx.fillText((node.title || '').slice(0, maxChars), node.boxX + 10, node.boxY + 32);

      ctx.fillStyle = '#52525b';
      ctx.font = '10px "Segoe UI", sans-serif';
      ctx.fillText((node.snippet || '').slice(0, maxChars + 4), node.boxX + 10, node.boxY + 46);

      // Geometric Silk Web Wrap on harvested/grabbing item
      if (node.webWrapProgress > 0) {
        ctx.strokeStyle = `rgba(228, 228, 231, ${0.22 * node.webWrapProgress})`;
        ctx.lineWidth = 0.9;
        const cornerX = node.boxX + node.cardW - 14;
        const cornerY = node.boxY + node.cardH - 12;
        const spokes = 5;
        for (let s = 0; s < spokes; s++) {
          const ang = (s / spokes) * Math.PI * 2;
          const r = 16 * node.webWrapProgress;
          ctx.beginPath();
          ctx.moveTo(cornerX, cornerY);
          ctx.lineTo(cornerX + Math.cos(ang) * r, cornerY + Math.sin(ang) * r);
          ctx.stroke();
        }
        if (node.webWrapProgress > 0.4) {
          ctx.beginPath();
          ctx.arc(cornerX, cornerY, 7 * node.webWrapProgress, 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      ctx.restore();
    });

    this.drawSilkWebs(ctx);
    this.drawRealisticSpider(ctx);

    ctx.restore();

    // Flying Harvested Data Packets
    this.flyingPackets.forEach((pk) => {
      const t = pk.progress;
      const ease = 1 - Math.pow(1 - t, 3);
      const cx = pk.x + (pk.targetX - pk.x) * ease;
      const cy = pk.y + (pk.targetY - pk.y) * ease - Math.sin(t * Math.PI) * 32;

      ctx.save();
      ctx.strokeStyle = 'rgba(228, 228, 231, 0.32)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pk.x, pk.y);
      ctx.lineTo(cx, cy);
      ctx.stroke();

      ctx.fillStyle = '#f4f4f5';
      ctx.shadowColor = this.accentGlow;
      ctx.shadowBlur = 8;
      ctx.beginPath();
      ctx.ellipse(cx, cy, 5.5, 3.8, -0.4, 0, Math.PI * 2);
      ctx.fill();

      ctx.shadowBlur = 0;
      ctx.fillStyle = '#e4e4e7';
      ctx.font = '600 9.5px "JetBrains Mono", Consolas, monospace';
      ctx.fillText(pk.label, cx + 8, cy + 3);
      ctx.restore();
    });

    // Right Edge Mini-Map Rail (shows all nodes & spider position along page)
    const contentH = h - topBarH;
    const railX = w - 8;
    ctx.fillStyle = 'rgba(24, 24, 27, 0.75)';
    ctx.fillRect(railX - 2, topBarH + 4, 6, contentH - 8);

    this.harvestNodes.forEach((n, idx) => {
      const ny = topBarH + 8 + ((n.boxY / Math.max(1, this.virtualPageH)) * (contentH - 16));
      ctx.fillStyle = n.collected ? '#71717a' : idx === this.currentNodeIndex ? this.accentGlow : '#3f3f46';
      ctx.beginPath();
      ctx.arc(railX + 1, ny, 2, 0, Math.PI * 2);
      ctx.fill();
    });

    if (this.virtualPageH > contentH) {
      const thumbH = Math.max(24, (contentH / this.virtualPageH) * contentH);
      const maxCam = Math.max(1, this.virtualPageH - contentH);
      const thumbY = topBarH + (this.cameraY / maxCam) * (contentH - thumbH);
      ctx.strokeStyle = 'rgba(228, 228, 231, 0.35)';
      ctx.lineWidth = 1.2;
      ctx.strokeRect(railX - 2.5, thumbY, 7, thumbH);
    }

    // Top Telemetry Bar
    ctx.fillStyle = '#111113';
    ctx.fillRect(0, 0, w, topBarH);
    ctx.strokeStyle = '#27272a';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, topBarH);
    ctx.lineTo(w, topBarH);
    ctx.stroke();

    ctx.fillStyle = this.paused ? '#f59e0b' : this.state === 'COMPLETED_IDLE' ? '#10b981' : this.accentGlow;
    ctx.beginPath();
    ctx.arc(14, 17, 3.5, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = '#d4d4d8';
    ctx.font = '500 11px "Segoe UI", sans-serif';
    const maxStatusW = w - 175;
    let st = this.paused ? `[PAUSADO] ${this.statusText}` : this.statusText;
    if (ctx.measureText(st).width > maxStatusW) {
      while (st.length > 10 && ctx.measureText(st + '...').width > maxStatusW) {
        st = st.slice(0, -2);
      }
      st += '...';
    }
    ctx.fillText(st, 25, 21);

    const counterLabel = `${this.harvestedCount}/${this.harvestNodes.length} itens • ${this.detectedHz}Hz`;
    ctx.fillStyle = '#a1a1aa';
    ctx.font = '600 10px "JetBrains Mono", Consolas, monospace';
    const cw = ctx.measureText(counterLabel).width;
    ctx.fillText(counterLabel, w - cw - 12, 21);
  }

  drawSilkWebs(ctx) {
    ctx.save();
    this.silkLines.forEach((s) => {
      ctx.strokeStyle = 'rgba(228, 228, 231, 0.20)';
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      const midX = (s.x1 + s.x2) * 0.5;
      const midY = (s.y1 + s.y2) * 0.5 + 6;
      ctx.moveTo(s.x1, s.y1);
      ctx.quadraticCurveTo(midX, midY, s.x2, s.y2);
      ctx.stroke();

      ctx.fillStyle = 'rgba(228, 228, 231, 0.42)';
      ctx.beginPath();
      ctx.arc(s.x1, s.y1, 1.8, 0, Math.PI * 2);
      ctx.fill();
    });

    const spinneretX = this.x - Math.cos(this.angle) * (21 * this.scale);
    const spinneretY = this.y - Math.sin(this.angle) * (21 * this.scale);
    ctx.strokeStyle = 'rgba(228, 228, 231, 0.48)';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(this.lastAnchor.x, this.lastAnchor.y);
    ctx.lineTo(spinneretX, spinneretY);
    ctx.stroke();

    if (this.state === 'GRABBING_ITEM') {
      const node = this.harvestNodes[this.currentNodeIndex];
      if (node) {
        const cx = node.boxX + node.cardW * 0.5;
        const cy = node.boxY + node.cardH * 0.5;
        ctx.strokeStyle = 'rgba(244, 244, 245, 0.55)';
        ctx.lineWidth = 1.1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(this.x + Math.cos(this.angle) * 14, this.y + Math.sin(this.angle) * 14);
        ctx.lineTo(cx, cy);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    this.ripples.forEach((rp) => {
      ctx.strokeStyle = `rgba(228, 228, 231, ${Math.max(0, rp.alpha * 0.55)})`;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(rp.x, rp.y, rp.r, 0, Math.PI * 2);
      ctx.stroke();
    });

    ctx.restore();
  }

  drawRealisticSpider(ctx) {
    const s = this.scale;
    ctx.save();

    // 1. Optical Scanner Cone ahead of eyes
    ctx.save();
    ctx.translate(this.x, this.y);
    ctx.rotate(this.angle);
    const coneGrad = ctx.createRadialGradient(10 * s, 0, 2 * s, 10 * s, 0, 68 * s);
    coneGrad.addColorStop(0, 'rgba(228, 228, 231, 0.14)');
    coneGrad.addColorStop(1, 'rgba(228, 228, 231, 0)');
    ctx.fillStyle = coneGrad;
    ctx.beginPath();
    ctx.moveTo(8 * s, 0);
    ctx.arc(8 * s, 0, 68 * s, -0.45, 0.45);
    ctx.closePath();
    ctx.fill();
    ctx.restore();

    // 2. Ambient Body Shadow
    ctx.fillStyle = 'rgba(0, 0, 0, 0.55)';
    ctx.beginPath();
    ctx.ellipse(this.x + 3, this.y + 6, 17 * s, 13 * s, this.angle, 0, Math.PI * 2);
    ctx.fill();

    // 3. 8 Realistic 3-Segment Arachnid Legs
    this.legs.forEach((leg) => {
      const mountDir = this.angle + (leg.side * Math.PI * 0.36) + (leg.pair - 1.5) * 0.21 * leg.side;
      const coxaX = this.x + Math.cos(mountDir) * (7.5 * s);
      const coxaY = this.y + Math.sin(mountDir) * (7.5 * s);

      const lift = leg.stepProgress < 1.0 ? Math.sin(leg.stepProgress * Math.PI) * 11 * s : 0;

      const dx = leg.footX - coxaX;
      const dy = leg.footY - coxaY;
      const legSpan = Math.hypot(dx, dy);
      const legAngle = Math.atan2(dy, dx);
      const archCurve = leg.side * (leg.pair < 2 ? -0.48 : 0.48);

      const kneeX = coxaX + Math.cos(legAngle + archCurve) * (legSpan * 0.52);
      const kneeY = coxaY + Math.sin(legAngle + archCurve) * (legSpan * 0.52) - 8 * s - lift * 0.85;

      const ankleX = coxaX + dx * 0.82;
      const ankleY = coxaY + dy * 0.82 - 3.5 * s - lift * 0.5;

      const tipX = leg.footX;
      const tipY = leg.footY - lift * 0.35;

      // Ground shadow cast by lifted leg
      ctx.strokeStyle = 'rgba(0, 0, 0, 0.42)';
      ctx.lineWidth = 2.8 * s;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(coxaX + 2, coxaY + 4);
      ctx.lineTo(kneeX + 3 + lift * 0.25, kneeY + 8 + lift * 0.5);
      ctx.lineTo(ankleX + 2 + lift * 0.15, ankleY + 5 + lift * 0.35);
      ctx.lineTo(leg.footX + 1, leg.footY + 2);
      ctx.stroke();

      // Femur
      ctx.strokeStyle = '#27272a';
      ctx.lineWidth = 3.4 * s;
      ctx.beginPath();
      ctx.moveTo(coxaX, coxaY);
      ctx.lineTo(kneeX, kneeY);
      ctx.stroke();

      // Femur highlight
      ctx.strokeStyle = '#52525b';
      ctx.lineWidth = 1.1 * s;
      ctx.beginPath();
      ctx.moveTo(coxaX, coxaY - 0.8);
      ctx.lineTo(kneeX, kneeY - 0.8);
      ctx.stroke();

      // Patella & Tibia
      ctx.strokeStyle = '#3f3f46';
      ctx.lineWidth = 2.4 * s;
      ctx.beginPath();
      ctx.moveTo(kneeX, kneeY);
      ctx.lineTo(ankleX, ankleY);
      ctx.stroke();

      // Metatarsus & Tarsus
      ctx.strokeStyle = leg.stepProgress < 1.0 ? '#e4e4e7' : '#71717a';
      ctx.lineWidth = 1.4 * s;
      ctx.beginPath();
      ctx.moveTo(ankleX, ankleY);
      ctx.lineTo(tipX, tipY);
      ctx.stroke();

      // Patella joint
      ctx.fillStyle = '#a1a1aa';
      ctx.beginPath();
      ctx.arc(kneeX, kneeY, 1.7 * s, 0, Math.PI * 2);
      ctx.fill();

      // Tarsal tip
      ctx.fillStyle = leg.stepProgress < 1.0 ? this.accentGlow : '#a1a1aa';
      ctx.beginPath();
      ctx.arc(tipX, tipY, 1.3 * s, 0, Math.PI * 2);
      ctx.fill();
    });

    // 4. Spider Body (Opisthosoma, Prosoma, Chelicerae & Pedipalps)
    ctx.translate(this.x, this.y);
    ctx.rotate(this.angle);

    const isGrabbing = this.state === 'GRABBING_ITEM';
    const pinchAnim = isGrabbing ? Math.sin(this.grabTimer * 24) * 0.28 : Math.sin(performance.now() * 0.004) * 0.06;

    // Pedipalps
    [-1, 1].forEach((side) => {
      ctx.strokeStyle = '#52525b';
      ctx.lineWidth = 1.8 * s;
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(8 * s, side * 4.2 * s);
      ctx.quadraticCurveTo(
        15 * s,
        side * (8.5 - pinchAnim * 6) * s,
        19 * s,
        side * (4.2 - pinchAnim * 5) * s
      );
      ctx.stroke();
    });

    // Chelicerae & Fangs
    [-1, 1].forEach((side) => {
      ctx.fillStyle = '#18181b';
      ctx.strokeStyle = isGrabbing ? this.accentGlow : '#71717a';
      ctx.lineWidth = 1.2 * s;
      ctx.beginPath();
      ctx.moveTo(9 * s, side * 1.5 * s);
      ctx.lineTo((14.5 + (isGrabbing ? 1.5 : 0)) * s, side * (3.2 - pinchAnim * 3.5) * s);
      ctx.lineTo(11 * s, side * 3.5 * s);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    });

    // Pedicel
    ctx.fillStyle = '#18181b';
    ctx.beginPath();
    ctx.ellipse(-3 * s, 0, 3 * s, 2.2 * s, 0, 0, Math.PI * 2);
    ctx.fill();

    // Opisthosoma (Abdomen)
    const abdBreath = 1 + Math.sin(performance.now() * 0.005) * 0.025;
    const abdGrad = ctx.createRadialGradient(-11 * s, -2 * s, 2 * s, -12 * s, 0, 13 * s);
    abdGrad.addColorStop(0, '#3f3f46');
    abdGrad.addColorStop(0.55, '#18181b');
    abdGrad.addColorStop(1, '#09090b');

    ctx.fillStyle = abdGrad;
    ctx.strokeStyle = '#3f3f46';
    ctx.lineWidth = 1.2 * s;
    ctx.beginPath();
    ctx.ellipse(-12.5 * s, 0, 11.5 * s * abdBreath, 8.8 * s * abdBreath, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // Dorsal chevrons
    ctx.strokeStyle = 'rgba(161, 161, 170, 0.28)';
    ctx.lineWidth = 1.1 * s;
    [-8, -12, -16].forEach((cxOffset, i) => {
      const span = (5.2 - i * 0.9) * s;
      ctx.beginPath();
      ctx.moveTo((cxOffset - 1.8) * s, -span);
      ctx.lineTo((cxOffset + 1.2) * s, 0);
      ctx.lineTo((cxOffset - 1.8) * s, span);
      ctx.stroke();
    });

    // Spinnerets
    ctx.fillStyle = '#52525b';
    ctx.beginPath();
    ctx.arc(-24 * s, -1.3 * s, 1.2 * s, 0, Math.PI * 2);
    ctx.arc(-24 * s, 1.3 * s, 1.2 * s, 0, Math.PI * 2);
    ctx.fill();

    // Prosoma (Cephalothorax)
    const cephGrad = ctx.createRadialGradient(4 * s, -1.5 * s, 1 * s, 3 * s, 0, 8.5 * s);
    cephGrad.addColorStop(0, '#52525b');
    cephGrad.addColorStop(0.6, '#27272a');
    cephGrad.addColorStop(1, '#09090b');

    ctx.fillStyle = cephGrad;
    ctx.strokeStyle = '#52525b';
    ctx.lineWidth = 1.2 * s;
    ctx.beginPath();
    ctx.ellipse(3.2 * s, 0, 7.6 * s, 6.2 * s, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // Fovea groove
    ctx.strokeStyle = '#09090b';
    ctx.lineWidth = 1 * s;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(4 * s, 0);
    ctx.stroke();

    // 8 Ocelli (Spider Eyes)
    const eyes = [
      [9.2, -1.6, 1.35],
      [9.2, 1.6, 1.35],
      [8.2, -3.4, 0.95],
      [8.2, 3.4, 0.95],
      [6.8, -4.2, 0.85],
      [6.8, 4.2, 0.85],
    ];
    eyes.forEach(([ex, ey, er], idx) => {
      ctx.fillStyle = idx < 2 ? (isGrabbing ? this.accentGlow : '#f4f4f5') : '#a1a1aa';
      ctx.beginPath();
      ctx.arc(ex * s, ey * s, er * s, 0, Math.PI * 2);
      ctx.fill();
    });

    ctx.restore();
  }

  loop(now) {
    const rawDt = (now - this.lastTime) / 1000;
    this.lastTime = now;

    if (rawDt > 0.001 && rawDt < 0.1) {
      this.fpsSamples.push(1 / rawDt);
      if (this.fpsSamples.length > 30) this.fpsSamples.shift();
      const avgFps = this.fpsSamples.reduce((a, b) => a + b, 0) / this.fpsSamples.length;
      this.detectedHz = Math.round(avgFps);
    }

    const dt = Math.min(0.05, Math.max(0.001, rawDt));

    if (this.running) {
      this.update(dt);
      this.draw(this.ctx);
    }
    requestAnimationFrame((t) => this.loop(t));
  }
}

window.PhysicalSpiderEngine = PhysicalSpiderEngine;
