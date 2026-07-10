// SVG 盤面レンダラー。地形・スポット・エージェントの描画と
// パン/ズーム・ホバー/クリックのインタラクションを担当する。

import { hexCenter, hexPointsAttr, boardSize } from './hex.js';
import { teamColor, seriesColor, ROAD_STATE_COLOR } from './palette.js';

const HEX = 26; // 六角形の中心-頂点距離(px)
const SVG_NS = 'http://www.w3.org/2000/svg';

function el(name, attrs = {}) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

export class BoardRenderer {
  constructor(svg, tooltip, callbacks = {}) {
    this.svg = svg;
    this.tooltip = tooltip;
    this.onSelect = callbacks.onSelect ?? (() => {});
    this.getTooltip = callbacks.getTooltip ?? (() => null);
    this.replay = null;
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this._bindPanZoom();
  }

  setMatch(replay) {
    this.replay = replay;
    this.svg.innerHTML = '';
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;

    const { w, h } = boardSize(replay.width, replay.height, HEX);
    this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);

    this.viewport = el('g', { id: 'viewport' });
    this.svg.appendChild(this.viewport);

    this.layers = {
      terrain: el('g', { id: 'layer-terrain' }),
      roads: el('g', { id: 'layer-roads' }),
      spots: el('g', { id: 'layer-spots' }),
      effects: el('g', { id: 'layer-effects' }),
      agents: el('g', { id: 'layer-agents' }),
    };
    Object.values(this.layers).forEach((l) => this.viewport.appendChild(l));

    this._buildTerrain();
    this._buildSpots();
    this._buildAgents();
    this._applyTransform();
  }

  // ----- 静的レイヤー -----

  _buildTerrain() {
    const { width, height, terrain } = this.replay;
    this.cellEls = [];
    for (let id = 0; id < width * height; id++) {
      const { x, y } = hexCenter(id, width, HEX);
      const poly = el('polygon', {
        points: hexPointsAttr(x, y, HEX - 0.6),
        class: `cell cell-${terrain[id]}`,
        'data-cell': id,
      });
      poly.addEventListener('mousemove', (e) => this._showTooltip(e, id));
      poly.addEventListener('mouseleave', () => this._hideTooltip());
      poly.addEventListener('click', () => this.onSelect({ type: 'cell', cell: id }));
      this.layers.terrain.appendChild(poly);
      this.cellEls.push(poly);

      // 地形の補助グリフ（色以外のチャンネル）
      if (terrain[id] === 'mountain' || terrain[id] === 'pond') {
        const glyph = el('text', {
          x, y: y + 4,
          class: 'terrain-glyph',
          'text-anchor': 'middle',
        });
        glyph.textContent = terrain[id] === 'mountain' ? '▲' : '≈';
        this.layers.terrain.appendChild(glyph);
      }
    }
  }

  // 道路の混雑状態オーバーレイ（日ごとに更新）
  setRoadStates(roadStates) {
    this.layers.roads.innerHTML = '';
    const { width } = this.replay;
    for (const [cell, state] of roadStates) {
      if (state === 'smooth') continue;
      const { x, y } = hexCenter(cell, width, HEX);
      const ring = el('polygon', {
        points: hexPointsAttr(x, y, HEX - 4),
        class: `road-ring road-ring-${state}`,
        stroke: ROAD_STATE_COLOR[state],
      });
      this.layers.roads.appendChild(ring);
      const mark = el('text', {
        x, y: y - 8,
        class: 'road-mark',
        fill: ROAD_STATE_COLOR[state],
        'text-anchor': 'middle',
      });
      mark.textContent = state === 'jammed' ? '×' : '!';
      this.layers.roads.appendChild(mark);
    }
  }

  _buildSpots() {
    const { width, spots, seriesNames } = this.replay;
    this.spotEls = new Map();
    for (const [pos, spot] of spots) {
      const { x, y } = hexCenter(pos, width, HEX);
      const g = el('g', { class: 'spot', 'data-spot': pos });
      const color = seriesColor(spot.brand);
      g.appendChild(el('circle', { cx: x, cy: y - 2, r: 9.5, class: 'spot-badge', stroke: color }));
      const label = el('text', {
        x, y: y + 1.5,
        class: 'spot-label',
        'text-anchor': 'middle',
      });
      label.textContent = (seriesNames[spot.brand] ?? String.fromCharCode(65 + spot.brand))[0];
      g.appendChild(label);
      const pips = el('g', { class: 'spot-pips' });
      g.appendChild(pips);
      this.layers.spots.appendChild(g);
      this.spotEls.set(pos, { g, pips, x, y, max: spot.stocks });
    }
  }

  _buildAgents() {
    const { width, numTeams, agentKinds, teamNames } = this.replay;
    this.agentEls = [];
    for (let ti = 0; ti < numTeams; ti++) {
      const teamEls = [];
      agentKinds[ti].forEach((kind, ai) => {
        const g = el('g', { class: `agent agent-${kind}`, 'data-team': ti, 'data-agent': ai });
        const color = teamColor(ti);
        let shape;
        if (kind === 'patrol') {
          shape = el('circle', { cx: 0, cy: 0, r: 7, class: 'agent-shape' });
        } else {
          shape = el('rect', {
            x: -6.4, y: -6.4, width: 12.8, height: 12.8, rx: 2,
            transform: 'rotate(45)', class: 'agent-shape',
          });
        }
        shape.style.fill = color;
        g.appendChild(shape);
        const num = el('text', { x: 0, y: 3.2, class: 'agent-num', 'text-anchor': 'middle' });
        num.textContent = ai + 1;
        g.appendChild(num);

        let fuelFg = null;
        if (kind === 'patrol') {
          g.appendChild(el('rect', { x: -8, y: 9, width: 16, height: 3, rx: 1.5, class: 'fuel-bg' }));
          fuelFg = el('rect', { x: -8, y: 9, width: 16, height: 3, rx: 1.5, class: 'fuel-fg' });
          g.appendChild(fuelFg);
        }
        g.addEventListener('click', (e) => {
          e.stopPropagation();
          this.onSelect({ type: 'agent', team: ti, agent: ai });
        });
        g.addEventListener('mousemove', (e) => this._showTooltip(e, null, { team: ti, agent: ai }));
        g.addEventListener('mouseleave', () => this._hideTooltip());
        this.layers.agents.appendChild(g);
        teamEls.push({ g, shape, fuelFg, kind, color });
      });
      this.agentEls.push(teamEls);
    }
  }

  // ----- フレーム更新 -----

  // frame: replay.days[d].frames[f] / stocks: Map pos->残在庫 (注目チーム視点)
  update(frame, stocks, focusTeam) {
    const { width, numTeams, fuelLimits } = this.replay;
    frame.forEach((agents, ti) => {
      agents.forEach((state, ai) => {
        const elc = this.agentEls[ti][ai];
        const { x, y } = hexCenter(state.cell, width, HEX);
        // 同一セルに重なった時に見分けられるよう、チーム/番号で定位置にずらす
        const slot = ti * this.agentEls[0].length + ai;
        const angle = (2 * Math.PI * slot) / (numTeams * this.agentEls[0].length);
        const ox = Math.cos(angle) * 7;
        const oy = Math.sin(angle) * 7;
        elc.g.style.transform = `translate(${x + ox}px, ${y + oy}px)`;
        elc.g.classList.toggle('agent-dim', focusTeam !== null && ti !== focusTeam);
        if (elc.fuelFg) {
          const ratio = Math.max(0, state.fuel / fuelLimits);
          elc.fuelFg.setAttribute('width', (16 * ratio).toFixed(2));
          elc.fuelFg.setAttribute(
            'fill',
            ratio > 0.5 ? '#0ca30c' : ratio > 0.25 ? '#fab219' : '#d03b3b'
          );
        }
      });
    });

    for (const [pos, spotEl] of this.spotEls) {
      const left = stocks.get(pos) ?? spotEl.max;
      spotEl.g.classList.toggle('spot-empty', left <= 0);
      spotEl.pips.innerHTML = '';
      for (let i = 0; i < Math.min(left, 5); i++) {
        spotEl.pips.appendChild(
          el('circle', { cx: spotEl.x - (Math.min(left, 5) - 1) * 2.6 / 2 + i * 2.6, cy: spotEl.y + 9.5, r: 1.6, class: 'spot-pip' })
        );
      }
    }
  }

  // 獲得エフェクト（リングを一瞬表示）
  pulse(cell, color) {
    const { width } = this.replay;
    const { x, y } = hexCenter(cell, width, HEX);
    const ring = el('circle', { cx: x, cy: y, r: 6, class: 'pulse-ring', stroke: color });
    this.layers.effects.appendChild(ring);
    setTimeout(() => ring.remove(), 700);
  }

  refreshColors() {
    // ライト/ダーク切替時に JS 指定色を更新
    this.agentEls?.forEach((teamEls, ti) =>
      teamEls.forEach((e) => (e.shape.style.fill = teamColor(ti)))
    );
    if (this.replay) {
      for (const [pos, spotEl] of this.spotEls) {
        const spot = this.replay.spots.get(pos);
        spotEl.g.querySelector('.spot-badge').setAttribute('stroke', seriesColor(spot.brand));
      }
    }
  }

  // ----- ツールチップ・パンズーム -----

  _showTooltip(evt, cell, agentRef = null) {
    const html = this.getTooltip(cell, agentRef);
    if (!html) return this._hideTooltip();
    this.tooltip.innerHTML = html;
    this.tooltip.classList.remove('hidden');
    const wrap = this.svg.parentElement.getBoundingClientRect();
    let tx = evt.clientX - wrap.left + 14;
    let ty = evt.clientY - wrap.top + 10;
    const tw = this.tooltip.offsetWidth;
    if (tx + tw > wrap.width - 8) tx = evt.clientX - wrap.left - tw - 10;
    this.tooltip.style.left = `${tx}px`;
    this.tooltip.style.top = `${ty}px`;
  }

  _hideTooltip() {
    this.tooltip.classList.add('hidden');
  }

  _applyTransform() {
    if (this.viewport) {
      this.viewport.setAttribute(
        'transform',
        `translate(${this.panX} ${this.panY}) scale(${this.zoom})`
      );
    }
  }

  zoomBy(factor) {
    this.zoom = Math.min(6, Math.max(0.4, this.zoom * factor));
    this._applyTransform();
  }

  resetView() {
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this._applyTransform();
  }

  _bindPanZoom() {
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    this.svg.addEventListener('pointerdown', (e) => {
      dragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
      this.svg.setPointerCapture(e.pointerId);
    });
    this.svg.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      // 画面px→viewBox座標系の変換係数
      const rect = this.svg.getBoundingClientRect();
      const vb = this.svg.viewBox.baseVal;
      if (!vb || !vb.width) return;
      const scale = vb.width / rect.width;
      this.panX += (e.clientX - lastX) * scale;
      this.panY += (e.clientY - lastY) * scale;
      lastX = e.clientX;
      lastY = e.clientY;
      this._applyTransform();
    });
    const stop = (e) => {
      dragging = false;
    };
    this.svg.addEventListener('pointerup', stop);
    this.svg.addEventListener('pointercancel', stop);
    this.svg.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        this.zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12);
      },
      { passive: false }
    );
  }
}
