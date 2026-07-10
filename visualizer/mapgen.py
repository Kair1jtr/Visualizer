"""サンプル試合用のマップ自動生成。

- 道路: 盤面を横断する曲がりくねった経路を2〜3本
- 山地・池: シード位置から成長させるブロブ
- 生成後、最大の連結成分以外の可移動セルを池に変換して
  「エージェントが到達できない平地・山地・道路はない」ルールを保証する
"""

import heapq
from collections import deque

from .hexgrid import neighbors

SERIES_NAMES = ["かけ", "ぶっかけ", "釜玉", "ざる", "肉うどん", "カレー", "しっぽく", "湯だめ"]


def _weighted_path(start: int, goal: int, width: int, height: int, weight) -> list[int]:
    """ランダム重み付き Dijkstra。道路を自然に蛇行させるために使う。"""
    dist = {start: 0.0}
    prev: dict[int, int] = {}
    heap = [(0.0, start)]
    done: set[int] = set()
    while heap:
        d, cell = heapq.heappop(heap)
        if cell in done:
            continue
        done.add(cell)
        if cell == goal:
            break
        for nb in neighbors(cell, width, height):
            nd = d + weight[nb]
            if nd < dist.get(nb, float("inf")):
                dist[nb] = nd
                prev[nb] = cell
                heapq.heappush(heap, (nd, nb))
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def _grow_blob(rng, terrain: list[str], kind: str, size: int, width: int, height: int) -> None:
    n = width * height
    seed = None
    for _ in range(60):
        cand = rng.randrange(n)
        if terrain[cand] == "plain":
            seed = cand
            break
    if seed is None:
        return
    terrain[seed] = kind
    blob = [seed]
    while len(blob) < size:
        frontier = [
            nb
            for b in blob
            for nb in neighbors(b, width, height)
            if terrain[nb] == "plain"
        ]
        if not frontier:
            break
        cell = rng.choice(frontier)
        terrain[cell] = kind
        blob.append(cell)


def _ensure_connectivity(terrain: list[str], width: int, height: int) -> set[int]:
    """最大連結成分以外の可移動セルを池にし、成分セル集合を返す。"""
    n = width * height
    unvisited = {i for i in range(n) if terrain[i] != "pond"}
    best: set[int] = set()
    while unvisited:
        start = next(iter(unvisited))
        comp = {start}
        queue = deque([start])
        unvisited.discard(start)
        while queue:
            cur = queue.popleft()
            for nb in neighbors(cur, width, height):
                if nb in unvisited:
                    unvisited.discard(nb)
                    comp.add(nb)
                    queue.append(nb)
        if len(comp) > len(best):
            best = comp
    for i in range(n):
        if terrain[i] != "pond" and i not in best:
            terrain[i] = "pond"
    return best


def generate_map(rng, width: int, height: int, num_spots: int, num_series: int, num_agents: int):
    """terrain / spots / series 名 / エージェント初期位置を生成して返す。"""
    n = width * height
    terrain = ["plain"] * n

    # 道路
    for _ in range(rng.randint(2, 3)):
        if rng.random() < 0.5:
            a = rng.randrange(width)
            b = n - width + rng.randrange(width)
        else:
            a = rng.randrange(height) * width
            b = rng.randrange(height) * width + width - 1
        weight = [rng.uniform(1.0, 6.0) for _ in range(n)]
        for cell in _weighted_path(a, b, width, height, weight):
            terrain[cell] = "road"

    # 山地・池
    for _ in range(rng.randint(2, 4)):
        _grow_blob(rng, terrain, "mountain", rng.randint(3, 7), width, height)
    for _ in range(rng.randint(2, 4)):
        _grow_blob(rng, terrain, "pond", rng.randint(3, 6), width, height)

    component = _ensure_connectivity(terrain, width, height)

    plains = sorted(c for c in component if terrain[c] == "plain")
    rng.shuffle(plains)

    num_spots = min(num_spots, max(1, len(plains) - num_agents))
    num_series = max(1, min(num_series, num_spots))

    spot_cells = sorted(plains[:num_spots])
    spots = []
    for i, cell in enumerate(spot_cells):
        spots.append(
            {
                "cell": cell,
                "series": i % num_series,  # 系列を巡回割当てで満遍なく
                "maxStock": rng.randint(1, min(3, num_agents)),
            }
        )

    start_pool = [c for c in plains[num_spots:]]
    starts = sorted(start_pool[:num_agents])

    return {
        "width": width,
        "height": height,
        "terrain": terrain,
        "spots": spots,
        "series": SERIES_NAMES[:num_series],
        "starts": starts,
    }
