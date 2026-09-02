"""状態遷移の追跡ログ。実装指示書 第9章。

「このステップでなぜこの状態になったのか」を後から追えるように、
反映フェーズの各処理が起こした変化をイベントとして記録する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .grid import DIRECTION_LABEL
from .state import AgentState, HexaUdon, SpotDef, TeamState
from .terrain import AGENT_KIND_LABEL, ROAD_STATUS_LABEL, TERRAIN_LABEL, Terrain


@dataclass
class TraceEvent:
    """1件の状態変化。"""

    day: int
    step: int
    phase: str  # "day_begin" / "reflection.1_fuel" など
    team_id: int | None
    agent_id: int | None
    message: str

    def format(self) -> str:
        who = ""
        if self.team_id is not None:
            who = f"T{self.team_id}"
            if self.agent_id is not None:
                who += f"/A{self.agent_id}"
            who = f" {who:<8}"
        return f"[D{self.day} S{self.step:>3}] {self.phase:<22}{who} {self.message}"


@dataclass
class Tracer:
    """イベントを溜めるだけの単純なトレーサ。

    `enabled=False` にすると記録しない（大量シミュレーション時のオーバーヘッド回避）。
    """

    enabled: bool = True
    events: list[TraceEvent] = field(default_factory=list)

    def _add(self, state: HexaUdon, phase: str, message: str, team=None, agent=None) -> None:
        if not self.enabled:
            return
        self.events.append(
            TraceEvent(
                day=state.day,
                step=state.step,
                phase=phase,
                team_id=team.id if team is not None else None,
                agent_id=agent.agent_id if agent is not None else None,
                message=message,
            )
        )

    # ----- 日 -----

    def day_begun(self, state: HexaUdon) -> None:
        jam = sum(1 for s in state.traffic.traffics.values() if s.value == 2)
        con = sum(1 for s in state.traffic.traffics.values() if s.value == 1)
        self._add(
            state,
            "day_begin",
            f"{state.day + 1}日目開始 ステップ数={state.steps_today} "
            f"道路: 混雑{con} 渋滞{jam} / 在庫補充・当日集合リセット",
        )

    def day_ended(self, state: HexaUdon) -> None:
        parts = [
            f"T{t.id}: 種類{t.brand_count} 累積{t.daily_brand_cumulative} 玉{t.total_udon}"
            for t in state.teams
        ]
        self._add(state, "day_end", f"{state.day + 1}日目終了 " + " | ".join(parts))

    def plan_rejected(self, state: HexaUdon, team: TeamState, error: Exception) -> None:
        self._add(
            state,
            "plan_rejected",
            f"回答リジェクト → 全エージェント最終ステップまで待機: {error}",
            team=team,
        )

    # ----- 反映フェーズ -----

    def fuel_consumed(self, state, team, agent, before, after, cost) -> None:
        self._add(
            state, "reflection.1_fuel", f"燃料消費 {before}→{after} (-{cost})", team, agent
        )

    def moved(self, state, team, agent, before, after, direction) -> None:
        d = DIRECTION_LABEL[direction] if direction is not None else "?"
        self._add(
            state, "reflection.2_move", f"移動 セル{before}→{after} ({d})", team, agent
        )

    def wait_finished(self, state, team, agent) -> None:
        self._add(state, "reflection.2_move", f"待機完了 セル{agent.pos}", team, agent)

    def acquired(self, state, team, agent, spot: SpotDef, left: int) -> None:
        self._add(
            state,
            "reflection.3_udon",
            f"うどん獲得 セル{spot.pos} 系列{spot.brand} 残在庫{left}",
            team,
            agent,
        )

    def refueled(self, state, team, agent, before, after) -> None:
        self._add(
            state, "reflection.4_refuel", f"燃料補給 {before}→{after}", team, agent
        )

    def traffic_updated(self, state: HexaUdon) -> None:
        if not self.enabled:
            return
        cells = sorted(state.traffic.stay_today.items())
        summary = " ".join(f"c{c}:{v}" for c, v in cells)
        self._add(state, "reflection.5_traffic", f"滞在数(当日累積) {summary}")

    # ----- アクションフェーズ -----

    def reserved_move(self, state, team, agent, target, direction, steps, fuel) -> None:
        self._add(
            state,
            "action.reserve",
            f"移動予約 セル{agent.pos}→{target} ({DIRECTION_LABEL[direction]}) "
            f"所要{steps}ステップ 燃料{fuel}",
            team,
            agent,
        )

    def reserved_wait(self, state, team, agent, steps) -> None:
        self._add(state, "action.reserve", f"待機予約 {steps}ステップ", team, agent)

    # ----- 出力 -----

    def dump(self, *, phase_prefix: str | None = None) -> str:
        events = self.events
        if phase_prefix:
            events = [e for e in events if e.phase.startswith(phase_prefix)]
        return "\n".join(e.format() for e in events)

    def for_agent(self, team_id: int, agent_id: int) -> str:
        """特定エージェントに絞って追跡する。"""
        return "\n".join(
            e.format()
            for e in self.events
            if e.team_id == team_id and e.agent_id == agent_id
        )


# ---------------------------------------------------------------------------
# 状態スナップショット
# ---------------------------------------------------------------------------


def snapshot(state: HexaUdon) -> str:
    """現在の状態を人が読める形にまとめる。実装指示書 第9章。"""
    lines: list[str] = []
    lines.append(f"=== Day {state.day + 1}/{state.config.num_days}  Step {state.step}/{state.steps_today} ===")

    for team in state.teams:
        lines.append(
            f"[チーム{team.id}] 種類数={team.brand_count} "
            f"日別累積={team.daily_brand_cumulative} 玉数={team.total_udon} "
            f"日別={team.daily_brand_counts}"
        )
        for agent in team.agents:
            kind = AGENT_KIND_LABEL[agent.kind]
            fuel = f"燃料{agent.fuel}" if agent.is_patrol else "燃料—"
            if agent.reserved is None:
                act = "（予約なし）"
            elif agent.reserved.is_move:
                act = f"移動中→セル{agent.reserved.target} 残{agent.reserved.remaining_steps}"
            else:
                act = f"待機中 残{agent.reserved.remaining_steps}"
            got = sorted(agent.acquired_spots_today)
            lines.append(
                f"   A{agent.agent_id} {kind} セル{agent.pos} {fuel} {act} 当日取得{got}"
            )
        stocks = {c: v for c, v in sorted(team.spot_stocks.items())}
        lines.append(f"   スポット在庫: {stocks}")

    if state.traffic.traffics:
        status = {
            c: ROAD_STATUS_LABEL[s] for c, s in sorted(state.traffic.traffics.items())
        }
        lines.append(f"道路状態: {status}")
    lines.append(f"当日の滞在数: {dict(sorted(state.traffic.stay_today.items()))}")
    lines.append(f"前日の滞在数: {dict(sorted(state.traffic.stay_prev1.items()))}")
    lines.append(f"前々日の滞在数: {dict(sorted(state.traffic.stay_prev2.items()))}")
    return "\n".join(lines)


def map_ascii(state: HexaUdon) -> str:
    """マップと現在のエージェント位置を簡易表示する。"""
    grid = state.map
    occupants: dict[int, list[str]] = {}
    for team in state.teams:
        for agent in team.agents:
            occupants.setdefault(agent.pos, []).append(f"{team.id}{agent.agent_id}")
    spot_cells = {s.pos for s in state.spots}

    glyph = {Terrain.PLAIN: "・", Terrain.ROAD: "＝", Terrain.MOUNTAIN: "▲", Terrain.POND: "≈"}
    lines = []
    for row in range(grid.height):
        indent = "  " if row % 2 == 0 else ""  # 偶数行が右にずれる 〔Q1〕
        cells = []
        for col in range(grid.width):
            cell = grid.to_cell(row, col)
            mark = glyph[grid.terrain_at(cell)]
            if cell in spot_cells:
                mark = "◎"
            if cell in occupants:
                mark = "*" + ",".join(occupants[cell])
            cells.append(f"{mark:<4}")
        lines.append(indent + "".join(cells))
    return "\n".join(lines)
