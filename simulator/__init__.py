"""ヘキサうどん 競技シミュレーター。

`docs/状態設計書.md` の状態モデルと、`docs/ルール説明書.md` に整理した公式ルールを
そのまま実装したもの。公式資料に無いルールを推測で追加しない方針で作られている。

未確定仕様（U-1 セル番号 / U-3 交通量の除算 / U-4 2日目の除数 /
U-5 反映順序 / U-6 燃料消費タイミング）は `policies.py` に集約してあり、
公式仕様が確定した際はそこだけを変更すればよい。

    from simulator import scenarios, engine, tracing

    state, plans = scenarios.official_supplement_scenario()
    tracer = tracing.Tracer()
    engine.begin_day(state, tracer)
    ...
"""

from .actions import PlanError
from .engine import (
    action_phase,
    begin_day,
    compute_road_status,
    create_game,
    end_day,
    reflection_phase,
    run_day,
    run_match,
    set_plans,
    simulate_day_steps,
    traffic_volume,
)
from .grid import HexGrid, build_grid
from .policies import (
    DEFAULT_POLICIES,
    AgentOrder,
    CellIndexing,
    FuelTiming,
    Policies,
    RowOffset,
    SecondDayDivisor,
    TrafficDivision,
)
from .state import (
    AgentState,
    GameState,
    MatchConfig,
    ReservedAction,
    SpotDef,
    TeamState,
    TrafficState,
)
from .terrain import AgentKind, RoadStatus, Terrain, move_cost
from .tracing import Tracer, map_ascii, snapshot
from .validation import validate_all, validate_kinds, validate_team_plan

__all__ = [
    "PlanError",
    "action_phase",
    "begin_day",
    "compute_road_status",
    "create_game",
    "end_day",
    "reflection_phase",
    "run_day",
    "run_match",
    "set_plans",
    "simulate_day_steps",
    "traffic_volume",
    "HexGrid",
    "build_grid",
    "DEFAULT_POLICIES",
    "AgentOrder",
    "CellIndexing",
    "FuelTiming",
    "Policies",
    "RowOffset",
    "SecondDayDivisor",
    "TrafficDivision",
    "AgentState",
    "GameState",
    "MatchConfig",
    "ReservedAction",
    "SpotDef",
    "TeamState",
    "TrafficState",
    "AgentKind",
    "RoadStatus",
    "Terrain",
    "move_cost",
    "Tracer",
    "map_ascii",
    "snapshot",
    "validate_all",
    "validate_kinds",
    "validate_team_plan",
]
