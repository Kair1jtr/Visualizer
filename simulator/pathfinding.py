"""六角グリッド上の最短経路。その日の道路状態を反映する。

移動に要するステップ数は **移動元セルの地形** で決まる〔要項〕〔Q10〕〔Q25〕【確定】
ため、辺の重みは「出発セルのコスト」になる（到着セルではない）。
道路は日ごとに状態が変わり〔要項〕【確定】、状態によってステップ数が変わるので、
経路探索には必ずその日の `traffics` を渡すこと。

このモジュールは**ルールを追加しない**。`terrain.move_cost()` と
`grid.HexGrid.neighbor()` が定めた通りに歩けるかどうかを探索するだけである。
"""

from __future__ import annotations

import heapq

from .grid import NUM_DIRECTIONS, HexGrid
from .terrain import RoadStatus, Terrain, move_cost


def leave_cost(
    grid: HexGrid, traffics: dict[int, RoadStatus], cell: int
) -> tuple[int, int] | None:
    """セル `cell` を出発するときの (ステップ数, 消費燃料)。池なら None。

    池は進入不可〔要項〕【確定】なので出発地にもなり得ない。

    `traffics` にその道路セルが載っていない場合は順調とみなす。
    エンジンは日開始時に全道路セルの状態を決める〔要項〕【確定】ので通常は起きないが、
    経路探索は部分的な道路状態でも呼べるようにしておく（1日目は全て順調
    〔要項〕【確定】なので、未知＝順調は既定として妥当）。
    """
    terrain = grid.terrain_at(cell)
    if terrain == Terrain.POND:
        return None
    if terrain == Terrain.ROAD:
        return move_cost(terrain, traffics.get(cell, RoadStatus.SMOOTH))
    return move_cost(terrain, None)


def dijkstra(
    grid: HexGrid, traffics: dict[int, RoadStatus], start: int
) -> tuple[dict[int, int], dict[int, tuple[int, int]]]:
    """`start` から各セルへの最短ステップ数を求める。

    戻り値: (dist, prev)
        dist[cell] = start からそのセルへ到達するのに要するステップ数
        prev[cell] = (直前のセル, そこから来た方向コード)

    到達できないセル（池・分断）は dist に現れない。
    """
    dist: dict[int, int] = {start: 0}
    prev: dict[int, tuple[int, int]] = {}
    if grid.terrain_at(start) == Terrain.POND:
        return dist, prev

    queue: list[tuple[int, int]] = [(0, start)]
    while queue:
        d, cell = heapq.heappop(queue)
        if d > dist.get(cell, d + 1):
            continue
        cost = leave_cost(grid, traffics, cell)
        if cost is None:
            continue
        steps, _fuel = cost
        for direction in range(NUM_DIRECTIONS):
            nxt = grid.neighbor(cell, direction)
            if nxt is None or grid.terrain_at(nxt) == Terrain.POND:
                continue
            nd = d + steps
            if nd < dist.get(nxt, nd + 1):
                dist[nxt] = nd
                prev[nxt] = (cell, direction)
                heapq.heappush(queue, (nd, nxt))
    return dist, prev


def directions_from(prev: dict[int, tuple[int, int]], start: int, goal: int) -> list[int] | None:
    """`dijkstra` の `prev` から `start` → `goal` の方向コード列を復元する。

    到達できない場合は None。`start == goal` なら空リスト。
    """
    if goal == start:
        return []
    out: list[int] = []
    cell = goal
    while cell != start:
        entry = prev.get(cell)
        if entry is None:
            return None
        cell, direction = entry
        out.append(direction)
    out.reverse()
    return out


def route(
    grid: HexGrid, traffics: dict[int, RoadStatus], start: int, goal: int
) -> list[int] | None:
    """`start` から `goal` への最短経路を方向コード列で返す。到達不能なら None。"""
    _dist, prev = dijkstra(grid, traffics, start)
    return directions_from(prev, start, goal)


def path_cells(grid: HexGrid, start: int, directions: list[int]) -> list[int]:
    """方向コード列を通過セル列に変換する（先頭は `start`）。盤外で打ち切る。"""
    cells = [start]
    pos = start
    for direction in directions:
        nxt = grid.neighbor(pos, direction)
        if nxt is None:
            break
        pos = nxt
        cells.append(pos)
    return cells
