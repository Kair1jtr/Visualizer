"""参照戦略（貪欲法）。シミュレーター本体の**外側**の層。

`docs/状態設計書.md` 第18.3節の3層構造で言えば、ここは第1層（ルール）ではなく
その上に乗る攻略側である。ルールはいっさい定義せず、`engine` が受け付ける
行動計画を組み立てるだけ。

`compare.Strategy` と同じシグネチャ `(GameState, team_id) -> TeamPlan` なので、
`compare.run_with_strategies()` にもそのまま渡せる。

方針:
    - 巡回車は「まだ取っていない系列 > 今日まだ取っていない系列 > 取得済み系列」の
      順に価値をつけ、価値 ÷ 距離が最大のスポットへ向かう。勝敗判定が
      ①種類数 ②日ごと種類数の累積 ③玉数 の順である〔要項〕【確定】ため、
      玉数より系列の網羅を優先する。
    - 補給車は、燃料が最も少ない巡回車の到達予定地へ向かう。
    - **燃料は補給を当てにせず**に見積もる。補給が起きれば余裕が増えるだけなので、
      この見積もりで組んだ計画が燃料不足でリジェクトされることはない。
"""

from __future__ import annotations

from .actions import TeamPlan
from .pathfinding import dijkstra, directions_from
from .state import AgentState, GameState, TeamState
from .terrain import Terrain, move_cost

# スポットの価値。勝敗判定①②が系列（brand）で決まる〔要項〕【確定】ことに対応する。
_VALUE_NEW_BRAND = 6  # まだ1度も取っていない系列
_VALUE_NEW_TODAY = 3  # 今日まだ取っていない系列
_VALUE_REPEAT = 1  # 今日すでに取った系列（玉数だけ増える）


def _team_of(state: GameState, team_id: int) -> TeamState:
    return next(t for t in state.teams if t.team_id == team_id)


def _spot_value(state: GameState, team: TeamState, brand: int) -> int:
    if brand not in team.brands_all:
        return _VALUE_NEW_BRAND
    if brand not in team.brands_today:
        return _VALUE_NEW_TODAY
    return _VALUE_REPEAT


class _Walker:
    """1エージェント分の計画を、ステップ数と燃料を見ながら積み上げる。

    `emit()` が False を返したらそれ以上は進めない（その日のステップ数を超える、
    または燃料が足りない）。最後に `finish()` で余りを待機で埋める。
    """

    def __init__(self, state: GameState, agent: AgentState, day_steps: int):
        self.state = state
        self.grid = state.grid
        self.road = state.traffic.road_status
        self.limit = day_steps
        self.pos = agent.pos
        self.fuel = agent.fuel
        self.is_patrol = agent.is_patrol
        self.used = 0
        self.plan: list[int] = []

    def emit(self, direction: int) -> bool:
        terrain = self.grid.terrain_at(self.pos)
        if terrain == Terrain.POND:
            return False
        steps, fuel = move_cost(terrain, self.road.get(self.pos))
        target = self.grid.neighbor(self.pos, direction)
        if target is None or self.grid.terrain_at(target) == Terrain.POND:
            return False
        if self.used + steps > self.limit:
            return False
        cost = fuel if self.is_patrol else 0  # 補給車は燃料を使わない〔要項〕【確定】
        if self.is_patrol and self.fuel < cost:
            return False
        self.plan.append(direction)
        self.used += steps
        self.fuel -= cost
        self.pos = target
        return True

    def walk(self, directions: list[int]) -> bool:
        """方向列を最後まで歩けたら True。途中で止まったら False。"""
        for direction in directions:
            if not self.emit(direction):
                return False
        return True

    def finish(self) -> list[int]:
        """余ったステップを待機で埋めて計画を確定する。

        行動計画は1日のステップ数と一致する必要がある〔書式〕【確定】。
        """
        if self.used < self.limit:
            self.plan.append(-(self.limit - self.used))
        return self.plan


def _plan_patrol(
    state: GameState,
    team: TeamState,
    agent: AgentState,
    claimed: set[int],
    day_steps: int,
) -> list[int]:
    """巡回車1体の行動計画。価値の高いスポットを順に回る。"""
    walker = _Walker(state, agent, day_steps)
    # 1巡回車が1スポットから1日に取れるのは1玉まで〔要項〕【確定】
    visited = set(agent.acquired_spots_today)

    # 出発地点がスポットなら、その日の1ステップ目に自動で獲得する〔Q7〕【確定】。
    # 改めて向かう必要はないので、対象から外しておく。
    here = state.spot_at(walker.pos)
    if here is not None and here.pos not in visited:
        visited.add(here.pos)
        claimed.add(here.pos)

    while True:
        dist, prev = dijkstra(state.grid, state.traffic.road_status, walker.pos)
        best = None
        best_score = 0.0
        for spot in state.spots:
            if spot.pos in visited or spot.pos in claimed:
                continue
            if team.spot_stocks.get(spot.pos, 0) <= 0:
                continue  # 在庫0なら到着しても獲得できない〔要項〕【確定】
            d = dist.get(spot.pos)
            if d is None or d == 0:
                continue
            if walker.used + d > walker.limit:
                continue  # その日のうちに着けない
            score = _spot_value(state, team, spot.brand) / d
            if score > best_score:
                best, best_score = spot, score
        if best is None:
            break
        directions = directions_from(prev, walker.pos, best.pos)
        if directions is None or not walker.walk(directions):
            break  # ステップ数か燃料が尽きた
        visited.add(best.pos)
        claimed.add(best.pos)

    return walker.finish()


def _plan_supply(
    state: GameState,
    team: TeamState,
    agent: AgentState,
    patrol_goals: dict[int, int],
    day_steps: int,
) -> list[int]:
    """補給車1体の行動計画。最も燃料が少ない巡回車の到達予定地へ向かう。

    補給は反映フェーズ4のタイミングで同一セルにいれば成立する〔Q22〕【確定】ので、
    行き先を合わせておけば、そこで合流した時点で満タンになる。
    """
    walker = _Walker(state, agent, day_steps)
    patrols = sorted(team.patrols(), key=lambda a: (a.fuel, a.agent_id))
    for target in patrols:
        goal = patrol_goals.get(target.agent_id, target.pos)
        directions = route_to(state, walker.pos, goal)
        if directions is None:
            continue
        walker.walk(directions)
        break
    return walker.finish()


def route_to(state: GameState, start: int, goal: int) -> list[int] | None:
    """その日の道路状態で `start` → `goal` の最短経路を方向コード列にする。"""
    _dist, prev = dijkstra(state.grid, state.traffic.road_status, start)
    return directions_from(prev, start, goal)


def end_position(state: GameState, agent: AgentState, plan: list[int]) -> int:
    """行動計画を歩き切ったときの到達セル（補給車の行き先決めに使う）。"""
    pos = agent.pos
    for value in plan:
        if value < 0:
            continue
        nxt = state.grid.neighbor(pos, value)
        if nxt is None:
            break
        pos = nxt
    return pos


def greedy_team_plan(state: GameState, team_id: int) -> TeamPlan:
    """1チーム分の行動計画を貪欲法で組み立てる。`compare.Strategy` 互換。

    `engine.begin_day()` 済みの状態を受け取る前提。その日の道路状態が
    決まっていないと移動コストを計算できないため。
    """
    team = _team_of(state, team_id)
    day_steps = state.steps_today
    plans: dict[int, list[int]] = {}

    # 巡回車を先に決める。同じスポットへ全員が向かわないよう claimed で予約する。
    claimed: set[int] = set()
    goals: dict[int, int] = {}
    for agent in sorted(team.patrols(), key=lambda a: a.agent_id):
        plan = _plan_patrol(state, team, agent, claimed, day_steps)
        plans[agent.agent_id] = plan
        goals[agent.agent_id] = end_position(state, agent, plan)

    # 補給車は巡回車の行き先を見てから決める。
    for agent in sorted(team.supplies(), key=lambda a: a.agent_id):
        plans[agent.agent_id] = _plan_supply(state, team, agent, goals, day_steps)

    return [plans[a.agent_id] for a in team.agents]


def stay_team_plan(state: GameState, team_id: int) -> TeamPlan:
    """全エージェントがその日ずっと待機する計画（比較の基準線）。"""
    team = _team_of(state, team_id)
    return [[-state.steps_today] for _ in team.agents]


STRATEGIES = {
    "greedy": greedy_team_plan,
    "stay": stay_team_plan,
}
