"""戦略比較の実行基盤。実装指示書 第10章。

同じ初期状態から複数の行動計画（戦略）を走らせ、
日ごとの道路状態・交通量・得点を並べて比較する。

「今日この道路を使う／避ける」判断が数日後にどう効くかを見られるように、
各日の **道路状態と交通量** を必ず記録する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import engine
from .actions import TeamPlan
from .state import GameState
from .terrain import ROAD_STATUS_LABEL, RoadStatus
from .tracing import Tracer

# 戦略: その日の開始状態（begin_day 済み）とチームIDを受け取り、行動計画を返す。
# 日開始時点の道路状態を見て判断できるようにこの形にしている。
Strategy = Callable[[GameState, int], TeamPlan]


# ---------------------------------------------------------------------------
# 記録
# ---------------------------------------------------------------------------


@dataclass
class TeamDayScore:
    team_id: int
    brand_count: int
    daily_cumulative: int
    total_udon: int
    rejected: str | None


@dataclass
class DayRecord:
    """1日分の記録。日開始時の道路状態と、その日に発生した滞在数を持つ。"""

    day: int
    road_status: dict[int, RoadStatus]
    volume_used: dict[int, float]  # その日の道路状態を決めた交通量
    stay: dict[int, int]  # その日に発生した滞在数（全チーム合算）
    scores: list[TeamDayScore]

    def road_summary(self) -> str:
        if not self.road_status:
            return "（道路なし）"
        return " ".join(
            f"c{cell}:{ROAD_STATUS_LABEL[st]}" for cell, st in sorted(self.road_status.items())
        )


@dataclass
class RunResult:
    """1回のシミュレーション結果。"""

    label: str
    days: list[DayRecord] = field(default_factory=list)
    final_state: GameState | None = None
    tracer: Tracer | None = None

    def final_scores(self) -> list[TeamDayScore]:
        return self.days[-1].scores if self.days else []

    def winner(self) -> int | None:
        if self.final_state is None:
            return None
        ranking = self.final_state.ranking()
        return ranking[0].team_id if ranking else None

    def report(self) -> str:
        lines = [f"=== {self.label} ==="]
        for rec in self.days:
            lines.append(f"[{rec.day + 1}日目] 道路: {rec.road_summary()}")
            if rec.volume_used:
                vol = " ".join(f"c{c}:{v:g}" for c, v in sorted(rec.volume_used.items()))
                lines.append(f"          交通量: {vol}")
            stay = " ".join(f"c{c}:{v}" for c, v in sorted(rec.stay.items()))
            lines.append(f"          滞在数: {stay}")
            for s in rec.scores:
                mark = f"  ※リジェクト: {s.rejected}" if s.rejected else ""
                lines.append(
                    f"          T{s.team_id}: 種類{s.brand_count} "
                    f"累積{s.daily_cumulative} 玉{s.total_udon}{mark}"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------


def run_with_strategies(
    state: GameState,
    strategies: dict[int, Strategy],
    *,
    label: str = "run",
    trace: bool = False,
) -> RunResult:
    """戦略に従って全日程を進め、日ごとの記録を残す。

    戦略は `begin_day` 済みの状態を受け取るため、**その日の道路状態を見てから**
    行動計画を決められる。
    """
    tracer = Tracer() if trace else None
    result = RunResult(label=label, tracer=tracer)

    while not state.finished:
        engine.begin_day(state, tracer)
        road_status = dict(state.traffic.road_status)
        volume = {c: engine.traffic_volume(state, c) for c in road_status}

        plans: dict[int, TeamPlan] = {}
        for team_id, strategy in strategies.items():
            plans[team_id] = strategy(state, team_id)

        day_index = state.day
        rejections = engine.run_day_body(state, plans, tracer)

        result.days.append(
            DayRecord(
                day=day_index,
                road_status=road_status,
                volume_used=volume,
                stay=dict(state.traffic.stay_prev1),  # 直前に shift されている
                scores=[
                    TeamDayScore(
                        team_id=t.team_id,
                        brand_count=t.brand_count,
                        daily_cumulative=t.daily_brand_cumulative,
                        total_udon=t.total_udon,
                        rejected=str(rejections[t.team_id]) if rejections.get(t.team_id) else None,
                    )
                    for t in state.teams
                ],
            )
        )

    result.final_state = state
    return result


def compare(
    make_state: Callable[[], GameState],
    variants: dict[str, dict[int, Strategy]],
    *,
    trace: bool = False,
) -> dict[str, RunResult]:
    """同じ初期状態から複数の戦略パターンを走らせて結果を集める。

    `make_state` は毎回まっさらな初期状態を返すファクトリ。
    """
    return {
        label: run_with_strategies(make_state(), strategies, label=label, trace=trace)
        for label, strategies in variants.items()
    }


def comparison_report(results: dict[str, RunResult]) -> str:
    """比較結果を並べた表を作る。"""
    lines: list[str] = []
    for label, result in results.items():
        lines.append(result.report())
        lines.append("")

    lines.append("=== 最終結果の比較 ===")
    header = f"{'パターン':<24}" + "".join(
        f"{'T' + str(s.team_id):>22}" for s in next(iter(results.values())).final_scores()
    )
    lines.append(header)
    for label, result in results.items():
        cells = "".join(
            f"{'種類' + str(s.brand_count) + ' 累積' + str(s.daily_cumulative) + ' 玉' + str(s.total_udon):>22}"
            for s in result.final_scores()
        )
        lines.append(f"{label:<24}{cells}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 汎用の戦略部品
# ---------------------------------------------------------------------------


def always_wait() -> Strategy:
    """全エージェントがその日ずっと待機する戦略。比較の基準線に使う。"""

    def strategy(state: GameState, team_id: int) -> TeamPlan:
        n = state.steps_today
        team = next(t for t in state.teams if t.team_id == team_id)
        return [[-n] for _ in team.agents]

    return strategy


def fixed_plans(plans_by_day: list[TeamPlan]) -> Strategy:
    """あらかじめ決めた行動計画を日ごとに返す戦略。"""

    def strategy(state: GameState, team_id: int) -> TeamPlan:
        if state.day < len(plans_by_day):
            return [list(p) for p in plans_by_day[state.day]]
        n = state.steps_today
        team = next(t for t in state.teams if t.team_id == team_id)
        return [[-n] for _ in team.agents]

    return strategy


def from_callable(fn: Callable[[GameState, int], TeamPlan]) -> Strategy:
    """任意の関数を戦略として使う（AI を差し込むための入口）。"""
    return fn


def build_plan(
    state: GameState,
    agent,
    prefix: list[int] | None = None,
    cycle: list[int] | None = None,
) -> list[int]:
    """`prefix` を1回、続けて `cycle` を繰り返し、収まる分だけ詰めた計画を作る。

    **その日の道路状態で移動コストを計算する**ため、道路が混雑・渋滞すると
    同じ方向列でも実行できる回数が減る。余りは末尾の待機で埋めるので、
    合計ステップ数は必ずその日のステップ数と一致する〔書式〕【確定】。

    燃料は考慮しないので、燃料が足りない設定で使うと検証で弾かれる。
    """
    from .terrain import Terrain, move_cost

    grid = state.grid
    road = state.traffic.road_status
    limit = state.steps_today

    pos = agent.pos
    used = 0
    plan: list[int] = []

    def try_move(direction: int) -> bool:
        nonlocal pos, used
        terrain = grid.terrain_at(pos)
        if terrain == Terrain.POND:
            return False
        steps, _fuel = move_cost(terrain, road.get(pos))
        target = grid.neighbor(pos, direction)
        if target is None or grid.terrain_at(target) == Terrain.POND:
            return False
        if used + steps > limit:
            return False
        plan.append(direction)
        used += steps
        pos = target
        return True

    for direction in prefix or ():
        if not try_move(direction):
            break

    if cycle:
        i = 0
        while try_move(cycle[i % len(cycle)]):
            i += 1

    if used < limit:
        plan.append(-(limit - used))
    return plan


def straight_line_directions(state: GameState, start: int, goal: int) -> list[int]:
    """1行マップ用: `start` から `goal` へ左右移動だけで向かう方向列。

    デモ・テスト用の簡易ヘルパー。一般のマップには経路探索が別途必要。
    """
    row_start, col_start = state.grid.to_rc(start)
    row_goal, col_goal = state.grid.to_rc(goal)
    if row_start != row_goal:
        raise ValueError("この補助関数は同じ行の移動にのみ使えます")
    step = 2 if col_goal > col_start else 5  # 2=右, 5=左
    return [step] * abs(col_goal - col_start)
