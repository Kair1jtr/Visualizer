"""参照戦略。シミュレーター本体の**外側**の層。

`docs/状態設計書.md` 第18.3節の3層構造で言えば、ここは第1層（ルール）ではなく
その上に乗る攻略側である。ルールはいっさい定義せず、`engine` が受け付ける
行動計画を組み立てるだけ。

## 自分の戦略を追加する

`SpotScoreStrategy` を継承して `score_spot()` を書き、`@register` を付けるだけでよい。
パラメータは `params` に宣言すれば、API（`GET /api/sim/strategies`）と
ブラウザの設定ダイアログが自動でフォームを組み立てる。

    @register
    class MyStrategy(SpotScoreStrategy):
        name = "mine"
        label = "自作戦略"
        description = "在庫が多いスポットを優先する"
        params = SpotScoreStrategy.params + (
            Param("stock_weight", "在庫の重み", "float", 1.0, minimum=0.0, maximum=10.0),
        )

        def score_spot(self, state, team, spot, dist):
            return spot.stocks * self.p["stock_weight"] / dist

`score_spot()` の戻り値が大きいスポットから順に回る。0以下は「狙わない」。
`dist` はその日の道路状態を織り込んだ所要ステップ数。

歩き方（ステップ数を数え、燃料が尽きる前に止める）と補給車の動きは基底クラスが
持っているので、通常は `score_spot()` だけを書けばよい。1日の組み立てごと
変えたい場合は `plan()` を上書きする（`StayStrategy` がその例）。

## 共通の方針

- 補給車は、燃料が最も少ない巡回車の到達予定地へ向かう。
- **燃料は補給を当てにせず**に見積もる。補給が起きれば余裕が増えるだけなので、
  この見積もりで組んだ計画が燃料不足でリジェクトされることはない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .actions import TeamPlan
from .pathfinding import dijkstra, directions_from
from .state import AgentState, HexaUdon, SpotDef, TeamState
from .terrain import Terrain, move_cost

# 戦略として使えるもの: begin_day 済みの状態とチームIDを受け取り、
# 1日分の行動計画を返す。`compare.Strategy` と同じ形。
StrategyFn = Callable[[HexaUdon, int], TeamPlan]


class StrategyError(Exception):
    """戦略名やパラメータの指定が不正。"""


# ---------------------------------------------------------------------------
# パラメータの宣言
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    """戦略が公開する調整つまみ1つぶんの宣言。

    ここに書いた内容がそのまま `GET /api/sim/strategies` のスキーマになり、
    ブラウザ側の設定ダイアログがフォーム部品を組み立てる。
    """

    name: str
    label: str  # 画面に出す名前
    kind: str  # "int" | "float" | "bool" | "choice"
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()
    description: str = ""

    def to_schema(self) -> dict:
        schema = {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "default": self.default,
            "description": self.description,
        }
        if self.minimum is not None:
            schema["min"] = self.minimum
        if self.maximum is not None:
            schema["max"] = self.maximum
        if self.step is not None:
            schema["step"] = self.step
        if self.choices:
            schema["choices"] = list(self.choices)
        return schema

    def replace_default(self, default: Any) -> "Param":
        """既定値だけを差し替えた同じ宣言を返す（継承で重みを変えるときに使う）。"""
        return Param(
            self.name,
            self.label,
            self.kind,
            default,
            self.minimum,
            self.maximum,
            self.step,
            self.choices,
            self.description,
        )

    def coerce(self, value: Any) -> Any:
        """外部から来た値を、この宣言に合う型・範囲に直す。範囲外は StrategyError。"""
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)

        if self.kind == "choice":
            text = str(value)
            if text not in self.choices:
                raise StrategyError(
                    f"{self.name}: {text!r} は選べません（{list(self.choices)}）"
                )
            return text

        try:
            number = int(value) if self.kind == "int" else float(value)
        except (TypeError, ValueError):
            raise StrategyError(f"{self.name}: 数値ではありません: {value!r}") from None
        if self.minimum is not None and number < self.minimum:
            raise StrategyError(
                f"{self.name}: {number} は下限 {self.minimum} を下回ります"
            )
        if self.maximum is not None and number > self.maximum:
            raise StrategyError(
                f"{self.name}: {number} は上限 {self.maximum} を超えます"
            )
        return number


def override_defaults(params: tuple[Param, ...], **defaults: Any) -> tuple[Param, ...]:
    """親クラスの params の既定値だけを差し替える（継承で使う）。"""
    known = {p.name for p in params}
    unknown = set(defaults) - known
    if unknown:
        raise StrategyError(f"知らないパラメータの既定値です: {sorted(unknown)}")
    return tuple(
        p.replace_default(defaults[p.name]) if p.name in defaults else p for p in params
    )


# ---------------------------------------------------------------------------
# 計画の組み立て部品（全戦略で共通）
# ---------------------------------------------------------------------------


class _Walker:
    """1エージェント分の計画を、ステップ数と燃料を見ながら積み上げる。

    `emit()` が False を返したらそれ以上は進めない（その日のステップ数を超える、
    または燃料が足りない）。最後に `finish()` で余りを待機で埋める。
    """

    def __init__(self, state: HexaUdon, agent: AgentState, day_steps: int):
        self.map = state.map
        self.road = state.traffic.traffics
        self.limit = day_steps
        self.pos = agent.pos
        self.fuel = agent.fuel
        self.is_patrol = agent.is_patrol
        self.used = 0
        self.plan: list[int] = []

    def emit(self, direction: int) -> bool:
        terrain = self.map.terrain_at(self.pos)
        if terrain == Terrain.POND:
            return False
        steps, fuel = move_cost(terrain, self.road.get(self.pos))
        target = self.map.neighbor(self.pos, direction)
        if target is None or self.map.terrain_at(target) == Terrain.POND:
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


def route_to(state: HexaUdon, start: int, goal: int) -> list[int] | None:
    """その日の道路状態で `start` → `goal` の最短経路を方向コード列にする。"""
    _dist, prev = dijkstra(state.map, state.traffic.traffics, start)
    return directions_from(prev, start, goal)


def end_position(state: HexaUdon, agent: AgentState, plan: list[int]) -> int:
    """行動計画を歩き切ったときの到達セル（補給車の行き先決めに使う）。"""
    pos = agent.pos
    for value in plan:
        if value < 0:
            continue
        nxt = state.map.neighbor(pos, value)
        if nxt is None:
            break
        pos = nxt
    return pos


def team_of(state: HexaUdon, team_id: int) -> TeamState:
    return next(t for t in state.teams if t.id == team_id)


# ---------------------------------------------------------------------------
# 戦略の基底クラス
# ---------------------------------------------------------------------------


class Strategy:
    """戦略の基底。`plan()` を実装すれば戦略として使える。

    インスタンスは `(state, team_id)` で呼び出せるので、`compare.Strategy` と
    同じ関数として扱える（`compare.run_with_strategies()` にそのまま渡せる）。
    """

    name: str = ""
    label: str = ""
    description: str = ""
    params: tuple[Param, ...] = ()

    def __init__(self, **values: Any):
        declared = {p.name: p for p in self.params}
        unknown = set(values) - set(declared)
        if unknown:
            raise StrategyError(
                f"{self.name}: 知らないパラメータです: {sorted(unknown)}"
                f"（あるのは {sorted(declared)}）"
            )
        self.p: dict[str, Any] = {
            param.name: (
                param.coerce(values[param.name])
                if param.name in values
                else param.default
            )
            for param in self.params
        }

    # ----- 戦略として呼ばれる入口 -----

    def __call__(self, state: HexaUdon, team_id: int) -> TeamPlan:
        return self.plan(state, team_id)

    def plan(self, state: HexaUdon, team_id: int) -> TeamPlan:
        raise NotImplementedError

    # ----- 記録用 -----

    def settings(self) -> dict:
        """この戦略の設定内容（観戦データに載せて後から確認できるようにする）。"""
        return {"strategy": self.name, "params": dict(self.p)}

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "name": cls.name,
            "label": cls.label or cls.name,
            "description": cls.description,
            "params": [p.to_schema() for p in cls.params],
        }


class SpotScoreStrategy(Strategy):
    """「スポットを評価して、高い順に回る」型の戦略の共通部分。

    継承先は `score_spot()` だけを書けばよい。1日の組み立て・歩き方・
    補給車の動きはここが持つ。
    """

    params: tuple[Param, ...] = (
        Param(
            "distance_power",
            "距離の効き方",
            "float",
            1.0,
            minimum=0.0,
            maximum=3.0,
            step=0.1,
            description="大きいほど近場を優先する。0 にすると距離を無視する",
        ),
        Param(
            "max_targets",
            "1日に狙うスポット数",
            "int",
            8,
            minimum=1,
            maximum=64,
            description="この数だけ回ったら、残りのステップは待機する",
        ),
        Param(
            "reserve_spots",
            "スポットを重複して狙わない",
            "bool",
            True,
            description="切ると複数の巡回車が同じスポットへ向かう",
        ),
        Param(
            "supply_follow",
            "補給車を巡回車に追従させる",
            "bool",
            True,
            description="切ると補給車はその場で待機する",
        ),
    )

    # ----- 継承先が実装する部分 -----

    def score_spot(
        self, state: HexaUdon, team: TeamState, spot: SpotDef, dist: int
    ) -> float:
        """スポットの評価値。大きいほど優先。0以下は「狙わない」。"""
        raise NotImplementedError

    # ----- 共通の組み立て -----

    def _distance_factor(self, dist: int) -> float:
        power = self.p["distance_power"]
        if power == 0:
            return 1.0
        if power == 1:
            return 1.0 / dist
        return 1.0 / (dist**power)

    def plan(self, state: HexaUdon, team_id: int) -> TeamPlan:
        team = team_of(state, team_id)
        day_steps = state.steps_today
        plans: dict[int, list[int]] = {}
        claimed: set[int] = set()
        goals: dict[int, int] = {}

        # 巡回車を先に決める。同じスポットへ全員が向かわないよう claimed で予約する。
        for agent in sorted(team.patrols(), key=lambda a: a.agent_id):
            agent_plan = self._plan_patrol(state, team, agent, claimed, day_steps)
            plans[agent.agent_id] = agent_plan
            goals[agent.agent_id] = end_position(state, agent, agent_plan)

        # 補給車は巡回車の行き先を見てから決める。
        for agent in sorted(team.supplies(), key=lambda a: a.agent_id):
            plans[agent.agent_id] = self._plan_supply(
                state, team, agent, goals, day_steps
            )

        return [plans[a.agent_id] for a in team.agents]

    def _plan_patrol(
        self,
        state: HexaUdon,
        team: TeamState,
        agent: AgentState,
        claimed: set[int],
        day_steps: int,
    ) -> list[int]:
        walker = _Walker(state, agent, day_steps)
        # 1巡回車が1スポットから1日に取れるのは1玉まで〔要項〕【確定】
        visited = set(agent.acquired_spots_today)
        reserve = self.p["reserve_spots"]

        # 出発地点がスポットなら、その日の1ステップ目に自動で獲得する〔Q7〕【確定】。
        # 改めて向かう必要はないので、対象から外しておく。
        here = state.spot_at(walker.pos)
        if here is not None and here.pos not in visited:
            visited.add(here.pos)
            if reserve:
                claimed.add(here.pos)

        for _ in range(self.p["max_targets"]):
            dist, prev = dijkstra(state.map, state.traffic.traffics, walker.pos)
            best = None
            best_score = 0.0
            for spot in state.spots:
                if spot.pos in visited:
                    continue
                if reserve and spot.pos in claimed:
                    continue
                if team.spot_stocks.get(spot.pos, 0) <= 0:
                    continue  # 在庫0なら到着しても獲得できない〔要項〕【確定】
                d = dist.get(spot.pos)
                if d is None or d == 0:
                    continue
                if walker.used + d > walker.limit:
                    continue  # その日のうちに着けない
                score = self.score_spot(state, team, spot, d)
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
        self,
        state: HexaUdon,
        team: TeamState,
        agent: AgentState,
        patrol_goals: dict[int, int],
        day_steps: int,
    ) -> list[int]:
        """補給車の行動計画。最も燃料が少ない巡回車の到達予定地へ向かう。

        補給は反映フェーズ4のタイミングで同一セルにいれば成立する〔Q22〕【確定】ので、
        行き先を合わせておけば、そこで合流した時点で満タンになる。
        """
        walker = _Walker(state, agent, day_steps)
        if not self.p["supply_follow"]:
            return walker.finish()
        for target in sorted(team.patrols(), key=lambda a: (a.fuel, a.agent_id)):
            goal = patrol_goals.get(target.agent_id, target.pos)
            directions = route_to(state, walker.pos, goal)
            if directions is None:
                continue
            walker.walk(directions)
            break
        return walker.finish()


# ---------------------------------------------------------------------------
# 具体的な戦略
# ---------------------------------------------------------------------------

STRATEGY_CLASSES: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    """戦略クラスを一覧に登録する。API と UI が自動で拾うようになる。"""
    if not cls.name:
        raise StrategyError(f"{cls.__name__}: name が設定されていません")
    if cls.name in STRATEGY_CLASSES:
        raise StrategyError(f"戦略名が重複しています: {cls.name}")
    STRATEGY_CLASSES[cls.name] = cls
    return cls


@register
class GreedyStrategy(SpotScoreStrategy):
    """系列の価値 ÷ 距離が最大のスポットへ向かう。

    勝敗判定が ①種類数 ②日ごと種類数の累積 ③玉数 の順である〔要項〕【確定】ため、
    未取得の系列を高く、取得済みの系列を低く見積もる。既定値はその重みづけ。
    """

    name = "greedy"
    label = "貪欲法"
    description = "系列の価値÷距離が最大のスポットへ。既取得系列も玉数のために拾う"
    params = SpotScoreStrategy.params + (
        Param(
            "new_brand_value",
            "新規系列の価値",
            "float",
            6.0,
            minimum=0.0,
            maximum=100.0,
            step=0.5,
            description="まだ1度も取っていない系列（勝敗①に効く）",
        ),
        Param(
            "new_today_value",
            "本日未取得系列の価値",
            "float",
            3.0,
            minimum=0.0,
            maximum=100.0,
            step=0.5,
            description="取得済みだが今日まだ取っていない系列（勝敗②に効く）",
        ),
        Param(
            "repeat_value",
            "取得済み系列の価値",
            "float",
            1.0,
            minimum=0.0,
            maximum=100.0,
            step=0.5,
            description="今日すでに取った系列。玉数だけ増える（勝敗③）。0 で狙わなくなる",
        ),
        Param(
            "stock_bonus",
            "在庫の重み",
            "float",
            0.0,
            minimum=0.0,
            maximum=10.0,
            step=0.1,
            description="在庫が多いスポットを優先する度合い。0 で在庫を無視する",
        ),
    )

    def score_spot(self, state, team, spot, dist):
        if spot.brand not in team.brands_all:
            value = self.p["new_brand_value"]
        elif spot.brand not in team.brands_today:
            value = self.p["new_today_value"]
        else:
            value = self.p["repeat_value"]
        if value <= 0:
            return 0.0
        value += self.p["stock_bonus"] * team.spot_stocks.get(spot.pos, 0)
        return value * self._distance_factor(dist)


@register
class BrandFirstStrategy(GreedyStrategy):
    """新規系列だけを狙う。既取得系列は無視して勝敗①②に全振りする。

    評価式は `GreedyStrategy` と同じで、重みの既定値だけを振り切っている。
    UI からは重みを戻せるので、継承は「別の初期設定」を用意しているだけ。
    """

    name = "brand"
    label = "系列優先"
    description = "まだ取っていない系列だけを狙い、既取得系列は無視する"
    params = override_defaults(
        GreedyStrategy.params,
        new_brand_value=100.0,
        new_today_value=1.0,
        repeat_value=0.0,
    )


@register
class NearestStrategy(SpotScoreStrategy):
    """系列を見ず、ただ近いスポットから順に回る（玉数狙いの基準線）。"""

    name = "nearest"
    label = "最近傍"
    description = "系列を見ず、ただ近いスポットから順に回る（玉数狙い）"

    def score_spot(self, state, team, spot, dist):
        return self._distance_factor(dist)


@register
class StayStrategy(Strategy):
    """全エージェントがその日ずっと待機する（比較の基準線）。

    スポットの評価という枠に収まらないので `plan()` ごと上書きしている。
    """

    name = "stay"
    label = "待機"
    description = "その日ずっと動かない（比較の基準線）"

    def plan(self, state: HexaUdon, team_id: int) -> TeamPlan:
        team = team_of(state, team_id)
        return [[-state.steps_today] for _ in team.agents]


DEFAULT_STRATEGY = GreedyStrategy.name


# ---------------------------------------------------------------------------
# 生成と一覧
# ---------------------------------------------------------------------------


def create(name: str, params: dict | None = None) -> Strategy:
    """戦略名とパラメータからインスタンスを作る。"""
    cls = STRATEGY_CLASSES.get(name)
    if cls is None:
        raise StrategyError(
            f"未知の戦略です: {name}（選べるのは {sorted(STRATEGY_CLASSES)}）"
        )
    return cls(**(params or {}))


def schemas() -> list[dict]:
    """選べる戦略とパラメータの一覧（UI がフォームを組み立てるのに使う）。"""
    return [cls.to_schema() for cls in STRATEGY_CLASSES.values()]


# 既定パラメータのまま使う場合の呼び出し口（`compare` などから使う）。
STRATEGIES: dict[str, StrategyFn] = {
    name: cls() for name, cls in STRATEGY_CLASSES.items()
}

STRATEGY_INFO: dict[str, str] = {
    name: cls.description for name, cls in STRATEGY_CLASSES.items()
}

greedy_team_plan = STRATEGIES["greedy"]
brand_team_plan = STRATEGIES["brand"]
nearest_team_plan = STRATEGIES["nearest"]
stay_team_plan = STRATEGIES["stay"]
