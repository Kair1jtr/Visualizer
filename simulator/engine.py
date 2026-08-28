"""状態遷移エンジン。公式が定めた処理順序を厳密に再現する。

処理順序（〔Q6〕〔補足〕【確定】。**入れ替え禁止**）:

    反映フェーズ（0ステップ目は実行しない）
        1. 燃料の消費
        2. 移動の反映
        3. うどんの獲得
        4. 燃料の補給
        5. 交通量の更新
    アクションフェーズ（最終ステップは実行しない）
        6. 次のアクション（移動 or 待機）の予約

    0 step      : アクションのみ
    1..N-1 step : 反映 → アクション
    N step      : 反映のみ
"""

from __future__ import annotations

from .actions import PlanError, TeamPlan, is_move, is_wait, wait_steps
from .grid import HexGrid
from .policies import AgentOrder, FuelTiming, SecondDayDivisor, TrafficDivision
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
from .tracing import Tracer

# ---------------------------------------------------------------------------
# 交通量 → 道路状態（U-3 / U-4 の隔離場所）
# ---------------------------------------------------------------------------


def _volume_at_least(total: int, divisor: int, threshold: int, division: TrafficDivision) -> bool:
    """交通量 (total ÷ divisor) が threshold 以上かを、丸め方式に従って判定する。

    U-3【未確認】。閾値は正の整数〔Q30〕【確定】なので、
    **EXACT と FLOOR は数学的に同値**（floor(x) >= t ⟺ x >= t）。
    浮動小数点誤差を避けるため、すべて整数演算で比較する。
    """
    if divisor <= 0:
        raise ValueError(f"交通量の除数は正である必要があります: {divisor}")
    if division is TrafficDivision.EXACT:
        return total >= threshold * divisor
    if division is TrafficDivision.FLOOR:
        return total // divisor >= threshold
    if division is TrafficDivision.CEIL:
        return -((-total) // divisor) >= threshold
    if division is TrafficDivision.ROUND_HALF_UP:
        return (2 * total + divisor) // (2 * divisor) >= threshold
    raise ValueError(f"未知の除算方式: {division}")


def traffic_divisor(state: GameState) -> int:
    """交通量の除数。U-4【未確認】の隔離場所。

    〔要項〕の定義は「チーム数で割った値」。2日目は前々日が存在しないが、
    除数を調整するという記述はないため、既定では常にチーム数とする。
    """
    policy = state.config.policies.second_day_divisor
    if policy is SecondDayDivisor.TEAMS:
        return state.config.num_teams
    # TEAMS_TIMES_DAYS: 参照した日数を掛ける（2日目は1日、3日目以降は2日）
    ref_days = 1 if state.day == 1 else 2
    return state.config.num_teams * ref_days


def compute_road_status(state: GameState) -> dict[int, RoadStatus]:
    """その日の道路状態を決定する。日開始時に1回だけ呼ぶ。〔要項〕【確定】

    1日目      : すべて順調
    2日目      : 1日目の交通量のみ
    3日目以降  : 前日 + 前々日の交通量
    """
    grid = state.grid
    cfg = state.config
    division = cfg.policies.traffic_division
    status: dict[int, RoadStatus] = {}

    for cell in grid.road_cells():
        if state.day == 0:
            status[cell] = RoadStatus.SMOOTH  # 1日目は全て順調 〔要項〕【確定】
            continue
        total = state.traffic.stay_prev1.get(cell, 0) + state.traffic.stay_prev2.get(cell, 0)
        divisor = traffic_divisor(state)
        if _volume_at_least(total, divisor, cfg.jammed_threshold, division):
            status[cell] = RoadStatus.JAMMED
        elif _volume_at_least(total, divisor, cfg.busy_threshold, division):
            status[cell] = RoadStatus.CONGESTED
        else:
            status[cell] = RoadStatus.SMOOTH
    return status


def traffic_volume(state: GameState, cell: int) -> float:
    """その日の道路状態を決めるのに使われた交通量（表示・検証用の導出値）。

    状態としては保持しない（状態設計書 第9.3節）。
    """
    total = state.traffic.stay_prev1.get(cell, 0) + state.traffic.stay_prev2.get(cell, 0)
    return total / traffic_divisor(state)


# ---------------------------------------------------------------------------
# エージェントの処理順序（U-5 の隔離場所）
# ---------------------------------------------------------------------------


def ordered_agents(state: GameState) -> list[tuple[TeamState, AgentState]]:
    """反映フェーズでエージェントを処理する順序。U-5【未確認】の隔離場所。

    既定はエージェントID昇順（うどん獲得の競合が ID 順である〔Q26〕【確定】に整合）。
    なお補給の判定は全エージェントの移動を反映し終えた後に行うため、
    この順序は補給の結果を変えない。
    """
    pairs = [(team, agent) for team in state.teams for agent in team.agents]
    if state.config.policies.agent_order is AgentOrder.REVERSED_ID:
        return sorted(pairs, key=lambda ta: (ta[0].team_id, -ta[1].agent_id))
    return sorted(pairs, key=lambda ta: (ta[0].team_id, ta[1].agent_id))


# ---------------------------------------------------------------------------
# 燃料消費タイミング（U-6 の隔離場所）
# ---------------------------------------------------------------------------


def _should_consume_now(reserved: ReservedAction, timing: FuelTiming) -> bool:
    """この反映フェーズで燃料を消費すべきか。U-6 の隔離場所。

    既定 ON_ARRIVAL は〔補足〕の状態遷移表から確認済み（docs/実装ノート.md 参照）。
    """
    if not reserved.is_move or reserved.fuel_consumed:
        return False
    if timing is FuelTiming.ON_ARRIVAL:
        return reserved.remaining_steps == 1  # この反映で移動が完了する
    if timing is FuelTiming.ON_FIRST_REFLECTION:
        return True
    return False  # ON_RESERVATION はアクションフェーズで処理済み


# ---------------------------------------------------------------------------
# 反映フェーズ
# ---------------------------------------------------------------------------


class InsufficientFuel(PlanError):
    """燃料が不足する移動。回答全体をリジェクトする。〔要項〕〔Q6〕【確定】"""


def reflection_phase(state: GameState, tracer: Tracer | None = None) -> None:
    """反映フェーズ。1〜5 の順序は公式が定めたもので入れ替えてはならない。"""
    cfg = state.config
    grid = state.grid
    pairs = ordered_agents(state)

    # ---- 1. 燃料の消費 〔Q6〕【確定】 ----
    for team, agent in pairs:
        r = agent.reserved
        if r is None or not agent.is_patrol:
            continue
        if not _should_consume_now(r, cfg.policies.fuel_timing):
            continue
        if agent.fuel < r.fuel_cost:
            # 〔要項〕「燃料が足りない場合に移動の命令を与えた場合は，無効な回答」
            # 検証を通った計画ならここには来ない。到達した場合は入力が不正なので
            # 必ず送出する（黙って燃料を負にしない）。
            raise InsufficientFuel(
                f"燃料不足の移動です（セル {agent.pos} から必要燃料 {r.fuel_cost}、"
                f"保有 {agent.fuel}）",
                team_id=team.team_id,
                agent_id=agent.agent_id,
            )
        before = agent.fuel
        agent.fuel -= r.fuel_cost
        r.fuel_consumed = True
        if tracer:
            tracer.fuel_consumed(state, team, agent, before, agent.fuel, r.fuel_cost)

    # ---- 2. 移動の反映 〔Q6〕【確定】 ----
    for team, agent in pairs:
        r = agent.reserved
        if r is None:
            continue
        r.remaining_steps -= 1
        if r.remaining_steps > 0:
            continue
        if r.is_move:
            before = agent.pos
            agent.pos = r.target
            if tracer:
                tracer.moved(state, team, agent, before, agent.pos, r.direction)
        elif tracer:
            tracer.wait_finished(state, team, agent)
        agent.reserved = None

    # ---- 3. うどんの獲得 〔Q6〕【確定】 ----
    # 在庫を超える同時到着は「リスト内の順番が若いエージェントが先」〔Q26〕【確定】
    for team in state.teams:
        for agent in sorted(team.agents, key=lambda a: a.agent_id):
            if not agent.is_patrol:
                continue
            spot = state.spot_at(agent.pos)
            if spot is None:
                continue
            if team.spot_stocks.get(spot.pos, 0) <= 0:
                continue
            if spot.pos in agent.acquired_spots_today:
                continue  # 1巡回車1スポット1日1玉 〔要項〕【確定】
            team.spot_stocks[spot.pos] -= 1
            agent.acquired_spots_today.add(spot.pos)
            team.total_udon += 1
            team.brands_all.add(spot.brand)
            team.brands_today.add(spot.brand)
            if tracer:
                tracer.acquired(state, team, agent, spot, team.spot_stocks[spot.pos])

    # ---- 4. 燃料の補給 〔Q6〕〔Q22〕【確定】 ----
    # 「同じセルに1ステップ以上いた場合」＝ このタイミングで同じセルにいた場合〔Q22〕
    for team in state.teams:
        supply_cells = {a.pos for a in team.agents if a.kind == AgentKind.SUPPLY}
        if not supply_cells:
            continue
        for agent in team.agents:
            if not agent.is_patrol or agent.pos not in supply_cells:
                continue
            if agent.fuel == cfg.fuel_limits:
                continue
            before = agent.fuel
            agent.fuel = cfg.fuel_limits  # 最大積載量まで補給 〔要項〕【確定】
            if tracer:
                tracer.refueled(state, team, agent, before, agent.fuel)

    # ---- 5. 交通量の更新 〔Q6〕〔Q27〕【確定】 ----
    # 移動反映後のセルを対象とする〔Q27〕。全エージェント（補給車を含む）が
    # ちょうど1セルにカウントされる（〔補足〕の滞在数で検証済み）。
    for _team, agent in pairs:
        state.traffic.stay_today[agent.pos] = state.traffic.stay_today.get(agent.pos, 0) + 1
    if tracer:
        tracer.traffic_updated(state)


# ---------------------------------------------------------------------------
# アクションフェーズ
# ---------------------------------------------------------------------------


def action_phase(state: GameState, tracer: Tracer | None = None) -> None:
    """アクションフェーズ。次の行動（移動 or 待機）を予約する。〔Q6〕【確定】

    移動を予約した時点では燃料を消費しない〔Q6〕【確定】
    （既定 ON_ARRIVAL の場合。U-6 の他の選択肢では挙動が変わる）。
    """
    cfg = state.config
    grid = state.grid
    for team, agent in ordered_agents(state):
        if agent.reserved is not None:
            continue  # 移動中／待機中は新たな命令を出せない 〔Q23〕〔Q24〕【確定】
        if agent.plan_cursor >= len(agent.plan):
            # 日がまだ終わっていないのに計画を消化しきった＝ステップ合計が
            # その日のステップ数に満たない。〔書式〕により不正な回答なので送出する
            # （黙って待機で埋めない）。
            raise PlanError(
                f"行動計画のステップ合計がその日のステップ数に足りません"
                f"（{state.step} ステップ目で計画を消化しきった、"
                f"その日は {state.steps_today} ステップ）",
                team_id=team.team_id,
                agent_id=agent.agent_id,
            )
        value = agent.plan[agent.plan_cursor]
        agent.plan_cursor += 1

        if is_wait(value):
            agent.reserved = ReservedAction(is_move=False, remaining_steps=wait_steps(value))
            if tracer:
                tracer.reserved_wait(state, team, agent, wait_steps(value))
            continue

        if not is_move(value):
            raise PlanError(
                f"行動計画の値は -1 以下 または 0〜5 です: {value}",
                team_id=team.team_id,
                agent_id=agent.agent_id,
            )

        terrain = grid.terrain_at(agent.pos)
        steps, fuel = move_cost(terrain, state.traffic.status_of(agent.pos))
        target = grid.neighbor(agent.pos, value)
        if target is None or grid.terrain_at(target) == Terrain.POND:
            raise PlanError(
                f"移動できないセルへの移動です（セル {agent.pos} から方向 {value}）",
                team_id=team.team_id,
                agent_id=agent.agent_id,
            )
        cost = fuel if agent.is_patrol else 0  # 補給車は燃料を使わない 〔要項〕【確定】
        reserved = ReservedAction(
            is_move=True,
            remaining_steps=steps,
            target=target,
            fuel_cost=cost,
            direction=value,
        )
        # U-6 が ON_RESERVATION の場合のみ、ここで消費する（〔Q6〕には反する対照用）
        if agent.is_patrol and cfg.policies.fuel_timing is FuelTiming.ON_RESERVATION:
            if agent.fuel < cost:
                raise InsufficientFuel(
                    f"燃料不足の移動です（セル {agent.pos} から必要燃料 {cost}、保有 {agent.fuel}）",
                    team_id=team.team_id,
                    agent_id=agent.agent_id,
                )
            agent.fuel -= cost
            reserved.fuel_consumed = True
        agent.reserved = reserved
        if tracer:
            tracer.reserved_move(state, team, agent, target, value, steps, cost)


# ---------------------------------------------------------------------------
# 日の開始・終了
# ---------------------------------------------------------------------------


def begin_day(state: GameState, tracer: Tracer | None = None) -> None:
    """日開始処理。状態設計書 第11.2節。〔要項〕【確定】"""
    state.step = 0
    state.traffic.road_status = compute_road_status(state)
    state.traffic.stay_today = {}
    for team in state.teams:
        # スポット在庫は各日の開始時に最大在庫数まで補充される 〔要項〕【確定】
        team.spot_stocks = {s.pos: s.stocks for s in state.spots}
        team.brands_today = set()
        for agent in team.agents:
            agent.acquired_spots_today = set()
            agent.reserved = None
            agent.plan = ()
            agent.plan_cursor = 0
    if tracer:
        tracer.day_begun(state)


def end_day(state: GameState, tracer: Tracer | None = None) -> None:
    """日終了処理。状態設計書 第11.2節。〔要項〕【確定】

    事後条件として、各エージェントが行動計画を過不足なく消化したことを確認する。
    〔書式〕「各エージェントの行動計画は1日のステップ数と一致する必要がある」【確定】
    に反する計画は、次のいずれかの形で検出できる:

      - 日終了時に**未完了の行動が残っている**（最後の移動が日をはみ出した）
      - 日終了時に**未消化のコマンドが残っている**（合計が日のステップ数を超えた）

    （逆に合計が足りない場合は、アクションフェーズで計画を消化しきった時点で
    検出される。engine.action_phase() 参照）
    """
    for team in state.teams:
        for agent in team.agents:
            if agent.reserved is not None:
                raise PlanError(
                    f"行動計画のステップ合計がその日のステップ数を超えています"
                    f"（日終了時に未完了の行動が残った、その日は {state.steps_today} ステップ）",
                    team_id=team.team_id,
                    agent_id=agent.agent_id,
                )
            if agent.plan_cursor < len(agent.plan):
                raise PlanError(
                    f"行動計画のステップ合計がその日のステップ数を超えています"
                    f"（{len(agent.plan) - agent.plan_cursor} 個のコマンドが未消化のまま日が終わった、"
                    f"その日は {state.steps_today} ステップ）",
                    team_id=team.team_id,
                    agent_id=agent.agent_id,
                )

    for team in state.teams:
        team.daily_brand_counts.append(len(team.brands_today))
    state.traffic.shift_days()
    if tracer:
        tracer.day_ended(state)
    state.day += 1
    if state.day >= state.config.num_days:
        state.finished = True


def all_wait_plans(state: GameState) -> dict[int, TeamPlan]:
    """有効な回答が無かった場合の既定行動。

    「各エージェントは前日終了時（初日なら初期位置）のセルで、
    最終ステップまで待機」〔書式〕〔Q55〕【確定】
    """
    n = state.steps_today
    return {
        team.team_id: [[-n] for _ in team.agents]
        for team in state.teams
    }


def set_plans(state: GameState, plans_by_team: dict[int, TeamPlan]) -> None:
    """検証済みの行動計画を各エージェントに設定する。"""
    for team in state.teams:
        plan = plans_by_team.get(team.team_id)
        if plan is None:
            continue
        for agent, agent_plan in zip(team.agents, plan):
            agent.plan = tuple(agent_plan)
            agent.plan_cursor = 0
            agent.reserved = None


# ---------------------------------------------------------------------------
# 1日 / 1試合
# ---------------------------------------------------------------------------


def simulate_day_steps(state: GameState, tracer: Tracer | None = None) -> None:
    """begin_day と set_plans が済んだ状態で、その日の全ステップを進める。"""
    n = state.steps_today
    for step in range(n + 1):
        state.step = step
        if step > 0:
            reflection_phase(state, tracer)
        if step < n:
            action_phase(state, tracer)


def run_day(
    state: GameState,
    plans_by_team: dict[int, TeamPlan],
    tracer: Tracer | None = None,
    *,
    validate: bool = True,
) -> dict[int, PlanError | None]:
    """1日を進める。日開始 → 検証 → 実行 → 日終了 まで行う。

    戻り値: チームID → リジェクト理由（有効なら None）。
    リジェクトされたチームは全エージェントが最終ステップまで待機になる
    〔書式〕〔Q55〕【確定】。
    """
    begin_day(state, tracer)
    return run_day_body(state, plans_by_team, tracer, validate=validate)


def run_day_body(
    state: GameState,
    plans_by_team: dict[int, TeamPlan],
    tracer: Tracer | None = None,
    *,
    validate: bool = True,
) -> dict[int, PlanError | None]:
    """`begin_day` 済みの状態から、検証 → 実行 → 日終了 を行う。

    日開始時の状態（道路状態など）を見てから行動計画を決めたい場合に、
    `begin_day` と分けて呼ぶ。
    """
    from .validation import validate_team_plan  # 循環 import 回避

    results: dict[int, PlanError | None] = {}
    effective: dict[int, TeamPlan] = {}
    fallback = all_wait_plans(state)

    for team in state.teams:
        plan = plans_by_team.get(team.team_id)
        if plan is None:
            results[team.team_id] = PlanError("回答が提出されていません", team_id=team.team_id)
            effective[team.team_id] = fallback[team.team_id]
            continue
        if not validate:
            results[team.team_id] = None
            effective[team.team_id] = plan
            continue
        error = validate_team_plan(state, team, plan)
        results[team.team_id] = error
        effective[team.team_id] = fallback[team.team_id] if error else plan
        if error and tracer:
            tracer.plan_rejected(state, team, error)

    set_plans(state, effective)
    simulate_day_steps(state, tracer)
    end_day(state, tracer)
    return results


def run_match(
    state: GameState,
    plans_by_day: list[dict[int, TeamPlan]],
    tracer: Tracer | None = None,
) -> list[dict[int, PlanError | None]]:
    """全日程を進める。`plans_by_day[day][team_id]` が各日の回答。"""
    out = []
    while not state.finished:
        day_plans = plans_by_day[state.day] if state.day < len(plans_by_day) else {}
        out.append(run_day(state, day_plans, tracer))
    return out


# ---------------------------------------------------------------------------
# 初期状態の構築
# ---------------------------------------------------------------------------


def create_game(
    grid: HexGrid,
    config: MatchConfig,
    spots: list[SpotDef],
    agent_starts: list[int],
    kinds_by_team: list[list[int]],
) -> GameState:
    """試合開始時の状態を構築する。状態設計書 第2章・第13.1節。

    - エージェント初期位置は全チーム共通 〔Q38〕【確定】
    - 1日目の巡回車の燃料は上限と同じ値 〔要項〕【確定】
    - 種別が未提出（None）なら全エージェントが巡回車 〔書式〕〔Q53〕【確定】
    """
    if len(kinds_by_team) != config.num_teams:
        raise ValueError(
            f"種別の指定数がチーム数と一致しません: {len(kinds_by_team)} != {config.num_teams}"
        )
    teams: list[TeamState] = []
    for team_id, kinds in enumerate(kinds_by_team):
        if kinds is None:
            kinds = [int(AgentKind.PATROL)] * len(agent_starts)
        if len(kinds) != len(agent_starts):
            raise ValueError(
                f"チーム{team_id} の種別要素数がエージェント数と一致しません: "
                f"{len(kinds)} != {len(agent_starts)}"
            )
        agents = []
        for agent_id, (kind, start) in enumerate(zip(kinds, agent_starts)):
            if kind not in (0, 1):
                raise ValueError(f"エージェント種別は 0 か 1 です: {kind}")
            agents.append(
                AgentState(
                    agent_id=agent_id,
                    kind=AgentKind(kind),
                    pos=start,
                    fuel=config.fuel_limits,  # 1日目は上限と同じ 〔要項〕【確定】
                )
            )
        teams.append(
            TeamState(
                team_id=team_id,
                agents=agents,
                spot_stocks={s.pos: s.stocks for s in spots},
            )
        )
    return GameState(
        config=config,
        grid=grid,
        spots=tuple(spots),
        teams=teams,
        traffic=TrafficState(),
    )
