// 六角形グリッド描画用の座標計算（even-r オフセット / pointy-top）。
// セル番号は行優先 0〜(縦×横-1) で、募集要項 図1 の座標例と一致する。
// 偶数行が右に半セルずれる（公式Q&Aその1 Q1/A1で確定）。

export function idToRowCol(id, width) {
  return { row: Math.floor(id / width), col: id % width };
}

// セル中心のピクセル座標。size は中心から頂点までの距離。
export function hexCenter(id, width, size) {
  const { row, col } = idToRowCol(id, width);
  const x = size * Math.sqrt(3) * (col + 0.5 * ((row + 1) & 1)) + size;
  const y = size * 1.5 * row + size;
  return { x, y };
}

// pointy-top 六角形の頂点列（SVG polygon の points 属性用）。
export function hexPointsAttr(cx, cy, size) {
  const pts = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i - 30);
    pts.push(
      `${(cx + size * Math.cos(angle)).toFixed(2)},${(cy + size * Math.sin(angle)).toFixed(2)}`
    );
  }
  return pts.join(' ');
}

// 公式回答フォーマットの方向コード → axial 方向。
// 0:左上, 1:右上, 以降時計回り (2:右, 3:右下, 4:左下, 5:左)
const DIRECTION_CODES = [
  [0, -1], // 0 左上
  [1, -1], // 1 右上
  [1, 0],  // 2 右
  [0, 1],  // 3 右下
  [-1, 1], // 4 左下
  [-1, 0], // 5 左
];

function toAxial(cell, width) {
  const row = Math.floor(cell / width);
  const col = cell % width;
  return [col - (row + (row & 1)) / 2, row];
}

// セル cell から方向コード code へ1セル移動した先。盤外は null。
export function applyDirection(cell, code, width, height) {
  const [q, r] = toAxial(cell, width);
  const [dq, dr] = DIRECTION_CODES[code];
  const nq = q + dq;
  const nr = r + dr;
  if (nr < 0 || nr >= height) return null;
  const nc = nq + (nr + (nr & 1)) / 2;
  if (nc < 0 || nc >= width) return null;
  return nr * width + nc;
}

// 盤面全体のピクセルサイズ。
export function boardSize(width, height, size) {
  return {
    w: size * Math.sqrt(3) * (width + 0.5) + size,
    h: size * 1.5 * (height - 1) + size * 2 + size,
  };
}
