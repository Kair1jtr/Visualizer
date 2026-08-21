"""貪欲法アルゴリズム: 試合状況（公式フォーマット）→ 行動計画（公式フォーマット）。

`examples/client.py`（ライブ対戦のサンプルクライアント）と `/algorithm`
ルート（サーバー内で試合状況から行動計画を計算して表示するテンプレート）の
両方から共有される、通信を含まない純粋なロジック部分。

戦略:
- 巡回車: 最寄りの未予約スポットへ Dijkstra 経路で向かい、届く限り連鎖。
  行き切れない分は途中まで前進し、残りステップは待機。
- 補給車: 燃料が最も少ない巡回車の到達予定地点へ向かう。
※ 燃料切れの巡回車は待機扱いになるだけなので、戦略の改良は自由。
"""

from .hexgrid import direction_code
from .pathfinding import FUEL_COST, STEP_COST, dijkstra, reconstruct_path, terrain_key

TERRAIN = {0: "plain", 1: "road", 2: "mountain", 3: "pond"}
ROAD = {0: "smooth", 1: "congested", 2: "jammed"}


def make_key_of(match: dict, info: dict):
    """セル番号 → STEP_COST/FUEL_COST のキー（池は None）。"""
    width = match["map"]["width"]
    cells = match["map"]["cells"]
    road = {t["pos"]: ROAD[t["status"]] for t in info["traffics"]}

    def key_of(cell: int):
        r, c = divmod(cell, width)
        return terrain_key(TERRAIN[cells[r][c]], road.get(cell, "smooth"))

    return key_of


def walk_prefix(path, key_of, step_budget, fuel_budget, width):
    """経路の先頭から、ステップ・燃料の予算内で進める分だけ方向コード化する。"""
    plan, steps, fuel = [], 0, 0
    cell = path[0]
    for nxt in path[1:]:
        key = key_of(cell)
        if steps + STEP_COST[key] > step_budget:
            break
        if fuel_budget is not None and fuel + FUEL_COST[key] > fuel_budget:
            break
        plan.append(direction_code(cell, nxt, width))
        steps += STEP_COST[key]
        fuel += FUEL_COST[key]
        cell = nxt
    return plan, steps, fuel, cell


def plan_patrol(start, fuel, spots, claimed, key_of, day_steps, width, height):
    """最寄りスポットを連鎖訪問する巡回車の1日分の行動計画。"""
    plan, used, cell = [], 0, start
    visited = set()
    while True:
        dist, prev = dijkstra(cell, width, height, key_of)
        cands = sorted(
            (s for s in spots if s != cell and s not in visited
             and s not in claimed and s in dist),
            key=lambda s: dist[s],
        )
        progressed = False
        for target in cands:
            path = reconstruct_path(prev, cell, target)
            part, steps, spent, reached = walk_prefix(
                path, key_of, day_steps - used, fuel, width
            )
            if not part:
                continue
            plan += part
            used += steps
            fuel -= spent
            cell = reached
            if reached == target:  # スポット到着（翌スポットへ連鎖）
                visited.add(target)
                claimed.add(target)
            progressed = reached == target
            break
        if not progressed:
            break
    if used < day_steps:
        plan.append(-(day_steps - used))
    return plan, cell


def plan_supply(start, goal, key_of, day_steps, width, height):
    """目標セルへ向かう補給車の1日分の行動計画（燃料消費なし）。"""
    plan, used, cell = [], 0, start
    if goal is not None and goal != start:
        dist, prev = dijkstra(start, width, height, key_of)
        if goal in dist:
            path = reconstruct_path(prev, start, goal)
            plan, used, _, cell = walk_prefix(path, key_of, day_steps, None, width)
    if used < day_steps:
        plan.append(-(day_steps - used))
    return plan, cell


def build_plans(match: dict, info: dict, kinds: list[int]) -> list[list[int]]:
    """試合開始前のマップ構成 + 各日開始時の試合情報 → 行動計画の回答フォーマット。"""
    width = match["map"]["width"]
    height = match["map"]["height"]
    day_steps = match["daySteps"][info["day"]]
    key_of = make_key_of(match, info)
    spots = [s["pos"] for s in match["spots"]]

    claimed: set[int] = set()
    plans: list = [None] * len(kinds)
    ends: list[tuple[int, int, int]] = []  # (fuel, agent_idx, end_cell)

    # 先に巡回車の計画を立て、到達予定地点を補給車の目標にする
    for ai, agent in enumerate(info["agents"]):
        if kinds[ai] == 0:
            plan, end = plan_patrol(
                agent["pos"], agent["fuel"], spots, claimed,
                key_of, day_steps, width, height,
            )
            plans[ai] = plan
            ends.append((agent["fuel"], ai, end))
    goal = min(ends)[2] if ends else None  # 燃料最少の巡回車の到達予定地点
    for ai, agent in enumerate(info["agents"]):
        if kinds[ai] == 1:
            plans[ai], _ = plan_supply(
                agent["pos"], goal, key_of, day_steps, width, height
            )
    return plans
