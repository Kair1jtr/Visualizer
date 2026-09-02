"""行動計画の受付時検証。状態設計書 第8.2節。

公式は「回答受付時に行動のステップ数の判定と移動の可否の判定を行う」と定めており、
各ステップの処理ではすべての移動が可能である前提で進行する 〔Q6〕【確定】。

リジェクト対象（すべて〔要項〕〔書式〕〔Q6〕【確定】）:
    - 行動計画のステップ合計がその日のステップ数と一致しない
    - 池への移動 / マップ外への移動 / 隣接していないセルへの移動
    - 燃料が不足する移動
    - 残りステップ数では完了できない移動
    - 種別の要素数不一致 / {0,1} 以外の値

**1エージェントでも不正なら、全エージェントの行動計画を不正としてリジェクトする**
〔書式〕【確定】。部分採用はされない。
"""

from __future__ import annotations

from .actions import PlanError, TeamPlan, walk_plan
from .state import HexaUdon, TeamState
from .terrain import AgentKind


def validate_kinds(kinds: list[int], num_agents: int) -> PlanError | None:
    """エージェント種別の回答を検証する。〔書式〕【確定】

    要素数がエージェント数と異なる、または {0,1} 以外が指定されていれば不正。
    """
    if not isinstance(kinds, (list, tuple)):
        return PlanError("種別の回答は配列である必要があります")
    if len(kinds) != num_agents:
        return PlanError(
            f"種別の要素数がエージェント数と一致しません: {len(kinds)} != {num_agents}"
        )
    for i, k in enumerate(kinds):
        if not isinstance(k, int) or isinstance(k, bool) or k not in (0, 1):
            return PlanError(f"種別は 0 か 1 です（エージェント{i}: {k!r}）")
    return None


def validate_team_plan(
    state: HexaUdon, team: TeamState, plan: TeamPlan
) -> PlanError | None:
    """1チーム分の行動計画を検証する。有効なら None、不正なら PlanError を返す。

    検証は3段階で行う:
        1. 構造（配列の形・値域）
        2. エージェントごとの歩行（盤外・池・ステップ合計）— 他チームに依存しない
        3. dry-run（燃料の充足）— 同一チーム内の補給に依存するため実際に走らせる

    燃料の判定タイミングは U-6（燃料消費タイミング）に従う。既定 ON_ARRIVAL では
    **移動が完了する時点**で足りていればよい（〔補足〕巡回車A が、移動予約時点では
    燃料1しかないが途中で補給を受けて有効となる例）。
    """
    # ---- 1. 構造 ----
    if not isinstance(plan, (list, tuple)):
        return PlanError("行動計画は配列である必要があります", team_id=team.id)
    if len(plan) != len(team.agents):
        return PlanError(
            f"行動計画の要素数がエージェント数と一致しません: {len(plan)} != {len(team.agents)}",
            team_id=team.id,
        )
    for agent, agent_plan in zip(team.agents, plan):
        if not isinstance(agent_plan, (list, tuple)):
            return PlanError(
                "各エージェントの行動計画は配列である必要があります",
                team_id=team.id,
                agent_id=agent.agent_id,
            )

    # ---- 2. エージェントごとの歩行 ----
    day_steps = state.steps_today
    for agent, agent_plan in zip(team.agents, plan):
        try:
            walk = walk_plan(
                list(agent_plan),
                agent.pos,
                state.map,
                state.traffic.traffics,
                is_patrol=agent.is_patrol,
            )
        except PlanError as exc:
            exc.team_id = team.id
            exc.agent_id = agent.agent_id
            return exc

        total = walk[-1].completes_at if walk else 0
        if total != day_steps:
            # 途中で日をはみ出した場合も「一致しない」に含まれる
            over = next((w for w in walk if w.completes_at > day_steps), None)
            if over is not None and over.value >= 0:
                return PlanError(
                    f"残りステップ数では完了できない移動です"
                    f"（{over.index}番目の移動が {over.completes_at} ステップ目に完了、"
                    f"その日は {day_steps} ステップ）",
                    team_id=team.id,
                    agent_id=agent.agent_id,
                )
            return PlanError(
                f"行動計画のステップ合計がその日のステップ数と一致しません: "
                f"{total} != {day_steps}",
                team_id=team.id,
                agent_id=agent.agent_id,
            )

    # ---- 3. dry-run（燃料） ----
    # 燃料と在庫はチーム内で完結する（在庫はチームごとに独立〔要項〕、補給は自チームの
    # 補給車のみ）ため、他チームを待機させたまま当該チームだけ走らせれば十分。
    from .engine import all_wait_plans, set_plans, simulate_day_steps

    probe = state.clone()
    plans = all_wait_plans(probe)
    plans[team.id] = [list(p) for p in plan]
    set_plans(probe, plans)
    try:
        simulate_day_steps(probe, None)
    except PlanError as exc:
        if exc.team_id is None:
            exc.team_id = team.id
        return exc
    return None


def validate_all(
    state: HexaUdon, plans_by_team: dict[int, TeamPlan]
) -> dict[int, PlanError | None]:
    """全チーム分をまとめて検証する。"""
    return {
        team.id: (
            validate_team_plan(state, team, plans_by_team[team.id])
            if team.id in plans_by_team
            else PlanError("回答が提出されていません", team_id=team.id)
        )
        for team in state.teams
    }
