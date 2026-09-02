"""行動計画（ActionState）の表現と解釈。状態設計書 第8章。

行動計画の形式は公式で確定している 〔書式〕【確定】:
    - 外側の配列＝エージェント（`agents` と同順）、内側＝そのエージェントの行動列
    - `-1` 以下 = 待機（**絶対値がステップ数**）
    - `0`〜`5` = 移動方向
    - 各エージェントの行動計画は1日のステップ数と一致する必要がある
"""

from __future__ import annotations

from dataclasses import dataclass

from .grid import NUM_DIRECTIONS, HexGrid
from .terrain import RoadStatus, Terrain, move_cost

# 1エージェント分の行動計画 / 全エージェント分
AgentPlan = list[int]
TeamPlan = list[AgentPlan]


def is_wait(value: int) -> bool:
    """待機コマンドか。`-1` 以下が待機。〔書式〕【確定】"""
    return value <= -1


def is_move(value: int) -> bool:
    """移動コマンドか。`0`〜`5` が移動方向。〔書式〕【確定】"""
    return 0 <= value < NUM_DIRECTIONS


def wait_steps(value: int) -> int:
    """待機コマンドの消費ステップ数。絶対値がステップ数。〔書式〕【確定】

    待機は地形に影響されない 〔Q49〕【確定】。燃料も消費しない 〔Q16〕【確定】。
    """
    return -value


@dataclass
class WalkStep:
    """行動計画を1コマンド展開した結果（検証・ログ用）。"""

    index: int  # 行動列内の位置
    value: int  # コマンドの値
    from_cell: int
    to_cell: int  # 待機なら from_cell と同じ
    steps: int  # 消費ステップ数
    fuel: int  # 消費燃料（待機・補給車は 0）
    completes_at: int  # 完了するステップ番号（累積）


class PlanError(Exception):
    """行動計画が不正。回答全体をリジェクトする。〔書式〕【確定】"""

    def __init__(self, message: str, *, team_id: int | None = None, agent_id: int | None = None):
        super().__init__(message)
        self.message = message
        self.team_id = team_id
        self.agent_id = agent_id

    def __str__(self) -> str:
        where = []
        if self.team_id is not None:
            where.append(f"チーム{self.team_id}")
        if self.agent_id is not None:
            where.append(f"エージェント{self.agent_id}")
        prefix = "/".join(where)
        return f"[{prefix}] {self.message}" if prefix else self.message


def walk_plan(
    plan: AgentPlan,
    start: int,
    grid: HexGrid,
    traffics: dict[int, RoadStatus],
    *,
    is_patrol: bool,
) -> list[WalkStep]:
    """行動計画を先頭から展開し、各コマンドの位置・ステップ・燃料を返す。

    **他のエージェントに依存しない**検査（盤外・池・非隣接・ステップ合計）に使う。
    燃料の充足判定は補給の有無に依存するため、ここでは消費量を記録するだけで
    判定しない（判定は validation.py の dry-run が行う）。

    盤外・池への移動を見つけた時点で PlanError を送出する。
    """
    out: list[WalkStep] = []
    pos = start
    elapsed = 0
    for index, value in enumerate(plan):
        if not isinstance(value, int) or isinstance(value, bool):
            raise PlanError(f"行動計画の値は整数である必要があります: {value!r}")
        if is_wait(value):
            steps = wait_steps(value)
            elapsed += steps
            out.append(WalkStep(index, value, pos, pos, steps, 0, elapsed))
            continue
        if not is_move(value):
            raise PlanError(f"行動計画の値は -1 以下 または 0〜5 です: {value}")

        terrain = grid.terrain_at(pos)
        if terrain == Terrain.POND:
            # 池には進入できないので、池から出発する状況自体が起こり得ない
            raise PlanError(f"池のセル {pos} からは移動できません")
        steps, fuel = move_cost(terrain, traffics.get(pos))
        target = grid.neighbor(pos, value)
        if target is None:
            raise PlanError(
                f"マップ外への移動です（セル {pos} から方向 {value}）"
            )
        if grid.terrain_at(target) == Terrain.POND:
            raise PlanError(f"池への移動です（セル {pos} → {target}）")

        elapsed += steps
        out.append(
            WalkStep(index, value, pos, target, steps, fuel if is_patrol else 0, elapsed)
        )
        pos = target
    return out


def total_steps(walk: list[WalkStep]) -> int:
    """行動計画が消費するステップ数の合計。"""
    return walk[-1].completes_at if walk else 0
