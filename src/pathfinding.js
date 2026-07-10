import { neighborsOf } from './hex.js';

// Step-time (ステップ) and fuel (燃料) cost per terrain, per the rules table.
export const TERRAIN_STEP_COST = {
  plain: 2,
  mountain: 3,
  road_smooth: 1,
  road_congested: 2,
  road_jammed: 4,
};

export const TERRAIN_FUEL_COST = {
  plain: 1,
  mountain: 2,
  road_smooth: 2,
  road_congested: 2,
  road_jammed: 2,
};

export function isWalkable(terrain) {
  return terrain !== 'pond';
}

// Resolves a cell's effective terrain key (folding road state into the
// terrain type) so cost tables can be looked up with a single key.
export function terrainKey(cellTerrain, roadState) {
  if (cellTerrain !== 'road') return cellTerrain;
  return `road_${roadState || 'smooth'}`;
}

export function stepCost(cellTerrain, roadState) {
  return TERRAIN_STEP_COST[terrainKey(cellTerrain, roadState)];
}

export function fuelCost(cellTerrain, roadState) {
  return TERRAIN_FUEL_COST[terrainKey(cellTerrain, roadState)];
}

// Dijkstra shortest path (by step-time) from `startId` to `targetId` over
// the walkable cells of the map. `roadStateOf(cellId)` resolves the current
// road congestion state for a cell. Returns an array of cell ids from
// start to target inclusive, or null if unreachable.
export function findPath(startId, targetId, { width, height, terrainOf, roadStateOf }) {
  if (startId === targetId) return [startId];

  const dist = new Map([[startId, 0]]);
  const prev = new Map();
  const visited = new Set();

  // Simple array-backed priority queue; grids here are small (<=1024 cells).
  const queue = [[0, startId]];

  while (queue.length) {
    queue.sort((a, b) => a[0] - b[0]);
    const [d, id] = queue.shift();
    if (visited.has(id)) continue;
    visited.add(id);
    if (id === targetId) break;

    // Step time (and fuel, elsewhere) to leave a cell is determined by the
    // terrain of the cell being departed, not the destination — per the
    // rules ("移動命令を受けた時点での地形に応じた燃料を消費します").
    const originTerrain = terrainOf(id);
    const cost = stepCost(originTerrain, roadStateOf ? roadStateOf(id) : 'smooth');
    for (const nb of neighborsOf(id, width, height)) {
      const nbTerrain = terrainOf(nb);
      if (!isWalkable(nbTerrain)) continue;
      const nd = d + cost;
      if (!dist.has(nb) || nd < dist.get(nb)) {
        dist.set(nb, nd);
        prev.set(nb, id);
        queue.push([nd, nb]);
      }
    }
  }

  if (!dist.has(targetId)) return null;

  const path = [targetId];
  let cur = targetId;
  while (cur !== startId) {
    cur = prev.get(cur);
    path.push(cur);
  }
  path.reverse();
  return path;
}
