"""地形コストを考慮した Dijkstra 経路探索。

ルール上、移動のステップ数・燃料は「移動命令を受けた時点での地形」
（=出発セルの地形）で決まるため、辺のコストは出発側セルに紐づく。
"""

import heapq

from .hexgrid import neighbors

# 地形キー → 移動ステップ数（表1）
STEP_COST = {
    "plain": 2,
    "mountain": 3,
    "road_smooth": 1,
    "road_congested": 2,
    "road_jammed": 4,
}

# 地形キー → 消費燃料（表1）
FUEL_COST = {
    "plain": 1,
    "mountain": 2,
    "road_smooth": 2,
    "road_congested": 2,
    "road_jammed": 2,
}

ROAD_STATES = ("smooth", "congested", "jammed")


def terrain_key(terrain: str, road_state: str = "smooth") -> str | None:
    """セルの地形と道路状態から STEP_COST/FUEL_COST のキーを返す。池は None。"""
    if terrain == "pond":
        return None
    if terrain == "road":
        return f"road_{road_state}"
    return terrain


def dijkstra(start: int, width: int, height: int, key_of) -> tuple[dict, dict]:
    """start から全セルへの最短ステップ数を返す。

    key_of(cell) はそのセルの地形キー（terrain_key の戻り値、池は None）。
    戻り値は (dist, prev)。到達不能セルは dist に含まれない。
    """
    dist = {start: 0}
    prev: dict[int, int] = {}
    heap = [(0, start)]
    done: set[int] = set()
    while heap:
        d, cell = heapq.heappop(heap)
        if cell in done:
            continue
        done.add(cell)
        key = key_of(cell)
        if key is None:
            continue
        leave_cost = STEP_COST[key]
        for nb in neighbors(cell, width, height):
            if key_of(nb) is None:
                continue
            nd = d + leave_cost
            if nd < dist.get(nb, 1 << 30):
                dist[nb] = nd
                prev[nb] = cell
                heapq.heappush(heap, (nd, nb))
    return dist, prev


def reconstruct_path(prev: dict, start: int, goal: int) -> list[int] | None:
    """dijkstra の prev から start→goal の経路（両端含む）を復元する。"""
    if goal == start:
        return [start]
    if goal not in prev:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def path_step_cost(path: list[int], key_of) -> int:
    return sum(STEP_COST[key_of(c)] for c in path[:-1])


def path_fuel_cost(path: list[int], key_of) -> int:
    return sum(FUEL_COST[key_of(c)] for c in path[:-1])
