// Hex-grid math for the "ヘキサうどん" map.
//
// Cells are numbered row-major, 0..(width*height-1), matching the contest
// PDF's coordinate example (図1). Rows are laid out as pointy-top hexagons
// using the "odd-r" horizontal offset scheme (odd rows shifted right by
// half a hex), which matches the staggered rows shown in the map diagram.

export function idToRowCol(id, width) {
  return { row: Math.floor(id / width), col: id % width };
}

export function rowColToId(row, col, width) {
  return row * width + col;
}

export function offsetToAxial(row, col) {
  const q = col - (row - (row & 1)) / 2;
  const r = row;
  return { q, r };
}

export function axialToOffset(q, r) {
  const col = q + (r - (r & 1)) / 2;
  const row = r;
  return { row, col };
}

const AXIAL_DIRECTIONS = [
  [1, 0],
  [1, -1],
  [0, -1],
  [-1, 0],
  [-1, 1],
  [0, 1],
];

// Returns the (up to 6) valid neighbor cell ids for a given cell id.
export function neighborsOf(id, width, height) {
  const { row, col } = idToRowCol(id, width);
  const { q, r } = offsetToAxial(row, col);
  const result = [];
  for (const [dq, dr] of AXIAL_DIRECTIONS) {
    const nq = q + dq;
    const nr = r + dr;
    const { row: nrow, col: ncol } = axialToOffset(nq, nr);
    if (nrow >= 0 && nrow < height && ncol >= 0 && ncol < width) {
      result.push(rowColToId(nrow, ncol, width));
    }
  }
  return result;
}

// Pixel center of a cell for a pointy-top hex layout, given a hex "size"
// (center-to-corner radius).
export function hexCenter(id, width, size) {
  const { row, col } = idToRowCol(id, width);
  const x = size * Math.sqrt(3) * (col + 0.5 * (row & 1));
  const y = size * 1.5 * row;
  return { x, y };
}

// Corner points of a pointy-top hexagon centered at (cx, cy).
export function hexCorners(cx, cy, size) {
  const points = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i - 30);
    points.push([cx + size * Math.cos(angle), cy + size * Math.sin(angle)]);
  }
  return points;
}

export function hexPointsAttr(cx, cy, size) {
  return hexCorners(cx, cy, size)
    .map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`)
    .join(' ');
}
