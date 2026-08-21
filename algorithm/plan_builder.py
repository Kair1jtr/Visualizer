"""JSON（方向コード・待機の負数）を直接書かずに、move()/wait()/goto() の
関数呼び出しで1日分の行動計画を組み立てるビルダー。

使い方:

    from algorithm.plan_builder import DayPlan

    plan = DayPlan(match, info, kinds)
    plan.agent(0).goto(43)          # セル43まで、届く分だけ経路に沿って移動
    plan.agent(1).move(2)           # 方向2(右)へ1マス移動
    plan.agent(1).move(2)           # さらに1マス
    plan.agent(2).wait(5)           # 5ステップ待機
    # agent(3) は何も呼ばなければ自動的に全ステップ待機になる

    plans_json = plan.to_json()     # そのまま POST /api/actions の body

`move()`/`goto()` は、燃料不足・ステップ予算不足・池や盤外への移動など
実行できない操作をすると `ValueError` を投げる。`can_move()` で事前に
確認することもできる。
"""

from visualizer.hexgrid import apply_direction, direction_code
from visualizer.pathfinding import FUEL_COST, STEP_COST, dijkstra, reconstruct_path

from .template import TERRAIN, make_key_of, walk_prefix


class AgentPlan:
    """1エージェント分の1日の行動計画を組み立てる。"""

    def __init__(self, match: dict, info: dict, cell: int, fuel: int, kind: str):
        self._match = match
        self._key_of = make_key_of(match, info)
        self._width = match["map"]["width"]
        self._height = match["map"]["height"]
        self._day_steps = match["daySteps"][info["day"]]
        self.cell = cell
        self.fuel = fuel  # kind=="supply" の場合は参照しない（燃料の概念なし）
        self.kind = kind  # "patrol" | "supply"
        self._codes: list[int] = []
        self._used = 0
        self._done = False

    @property
    def remaining(self) -> int:
        """残りステップ数。"""
        return self._day_steps - self._used

    def _terrain_at(self, cell: int) -> str:
        width = self._width
        r, c = divmod(cell, width)
        return TERRAIN[self._match["map"]["cells"][r][c]]

    def _cost_here(self) -> tuple[int, int]:
        """現在地（出発セル）の地形で決まる (ステップ数, 燃料) を返す。"""
        key = self._key_of(self.cell)
        return STEP_COST[key], FUEL_COST[key]

    def can_move(self, direction: int) -> bool:
        """direction 方向へ今すぐ1マス移動できるか。"""
        if not 0 <= direction <= 5:
            return False
        target = apply_direction(self.cell, direction, self._width, self._height)
        if target is None or self._terrain_at(target) == "pond":
            return False
        step_cost, fuel_cost = self._cost_here()
        if step_cost > self.remaining:
            return False
        if self.kind == "patrol" and fuel_cost > self.fuel:
            return False
        return True

    def move(self, direction: int) -> int:
        """direction 方向（0〜5、0:左上→時計回り）へ1マス移動する。

        戻り値: 移動後のセル番号。
        移動できない場合（盤外/池、ステップ予算不足、巡回車の燃料不足）は
        ValueError を投げる。
        """
        if not 0 <= direction <= 5:
            raise ValueError(f"direction は 0〜5 の整数（{direction} は不正）")
        target = apply_direction(self.cell, direction, self._width, self._height)
        if target is None:
            raise ValueError("盤外への移動です")
        if self._terrain_at(target) == "pond":
            raise ValueError("池への移動です")
        step_cost, fuel_cost = self._cost_here()
        if step_cost > self.remaining:
            raise ValueError(
                f"残り{self.remaining}ステップでは移動できません（必要{step_cost}ステップ）"
            )
        if self.kind == "patrol" and fuel_cost > self.fuel:
            raise ValueError(f"燃料不足です（必要{fuel_cost}／残り{self.fuel}）")

        self._codes.append(direction)
        self._used += step_cost
        if self.kind == "patrol":
            self.fuel -= fuel_cost
        self.cell = target
        return self.cell

    def move_to(self, target_cell: int) -> int:
        """target_cell が現在地の隣接セルなら move() する（direction を自動計算）。"""
        for d in range(6):
            if apply_direction(self.cell, d, self._width, self._height) == target_cell:
                return self.move(d)
        raise ValueError(f"セル{target_cell}は現在地({self.cell})に隣接していません")

    def goto(self, target_cell: int) -> int:
        """target_cell へ最短経路で向かう。ステップ・燃料予算が尽きたら
        そこで止まる（残りは次回以降の move()/goto() 呼び出しで続けられる）。

        戻り値: 実際に到達したセル番号（予算不足で途中の場合もある）。
        """
        dist, prev = dijkstra(self.cell, self._width, self._height, self._key_of)
        if target_cell not in dist:
            raise ValueError(f"セル{target_cell}へ到達できません")
        path = reconstruct_path(prev, self.cell, target_cell)
        fuel_budget = self.fuel if self.kind == "patrol" else None
        part, steps, fuel_spent, reached = walk_prefix(
            path, self._key_of, self.remaining, fuel_budget, self._width
        )
        self._codes.extend(part)
        self._used += steps
        if self.kind == "patrol":
            self.fuel -= fuel_spent
        self.cell = reached
        return reached

    def wait(self, steps: int = 1) -> None:
        """steps ステップ待機する。"""
        if steps <= 0:
            raise ValueError("steps は1以上の整数")
        if steps > self.remaining:
            raise ValueError(f"残り{self.remaining}ステップを超える待機は指定できません")
        self._codes.append(-steps)
        self._used += steps

    def finish(self) -> list[int]:
        """残りステップを待機で埋めて確定し、このエージェントの行動計画を返す。"""
        if self._done:
            return list(self._codes)
        if self.remaining > 0:
            self.wait(self.remaining)
        self._done = True
        return list(self._codes)


class DayPlan:
    """試合状況（match + info + kinds）から、全エージェント分の
    AgentPlan をまとめて作る。1日分の行動計画をまとめて JSON 化する。
    """

    def __init__(self, match: dict, info: dict, kinds: list[int]):
        self.agents: list[AgentPlan] = [
            AgentPlan(
                match, info,
                cell=a["pos"], fuel=a["fuel"],
                kind="patrol" if k == 0 else "supply",
            )
            for a, k in zip(info["agents"], kinds)
        ]

    def agent(self, index: int) -> AgentPlan:
        return self.agents[index]

    def __len__(self) -> int:
        return len(self.agents)

    def __getitem__(self, index: int) -> AgentPlan:
        return self.agents[index]

    def to_json(self) -> list[list[int]]:
        """POST /api/actions にそのまま送れる行動計画の回答フォーマット。"""
        return [a.finish() for a in self.agents]


__all__ = ["AgentPlan", "DayPlan", "direction_code"]
