"""ライブ対戦モードの試合進行管理。

チーム0 = プレイヤー（外部クライアント）、他チーム = 内蔵の貪欲AI。
公式フォーマットの回答（エージェント種別・行動計画）を受け取るたびに
シミュレーションを1日ずつ進める。

進行状態:
  waiting_agents   種別の回答待ち（試合開始前）
  waiting_actions  当日の行動計画の回答待ち
  finished         全日程終了
"""

import random

from .simulator import MatchSimulator

PLAYER_TEAM = 0
PLAYER_NAME = "プレイヤー"


class LiveError(Exception):
    """クライアントの手順誤り（HTTP 409 相当）。"""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class LiveMatch:
    def __init__(
        self,
        seed: int | None = None,
        num_teams: int = 3,
        num_days: int = 5,
        num_agents: int = 4,
        width: int = 12,
        height: int = 10,
    ):
        if seed is None:
            seed = random.randrange(1_000_000_000)
        self.sim = MatchSimulator(seed, num_teams, num_days, num_agents, width, height)
        self.sim.teams[PLAYER_TEAM].name = PLAYER_NAME
        self.kinds: list[int] | None = None
        self.current_day = 0

    # ----- 状態 -----

    @property
    def num_days(self) -> int:
        return len(self.sim.day_steps)

    @property
    def finished(self) -> bool:
        return self.current_day >= self.num_days

    @property
    def status(self) -> str:
        if self.kinds is None:
            return "waiting_agents"
        if self.finished:
            return "finished"
        return "waiting_actions"

    @property
    def pending_info(self) -> dict:
        """受付中の日の試合情報（begin_day 済み）。"""
        return self.sim._pending[2]

    # ----- クライアントからの回答 -----

    def submit_kinds(self, kinds: list[int]):
        if self.kinds is not None:
            raise LiveError("エージェント種別は提出済みです（試合は開始しています）")
        self.sim.set_team_kinds(PLAYER_TEAM, kinds)
        self.kinds = kinds
        self.sim.begin_day(0)

    def submit_actions(self, day: int, plans: list[list[int]]):
        if self.kinds is None:
            raise LiveError("先に POST /api/agents でエージェント種別を提出してください")
        if self.finished:
            raise LiveError("試合は終了しています（POST /api/live/new で新しい試合を開始）")
        if day != self.current_day:
            raise LiveError(f"現在回答を受付中の日は {self.current_day} です")
        self.sim.execute_day({PLAYER_TEAM: plans})
        self.current_day += 1
        if not self.finished:
            self.sim.begin_day(self.current_day)

    # ----- 参照 -----

    def day_info(self, day: int) -> dict:
        if self.kinds is None:
            raise LiveError("先に POST /api/agents でエージェント種別を提出してください")
        if day < self.current_day:
            return self.sim.days[day]["info"]
        if day == self.current_day and not self.finished:
            return self.pending_info
        raise LiveError(f"day {day} はまだ開始していません（現在 day {self.current_day}）")

    def bundle(self) -> dict:
        bundle = self.sim.bundle()
        bundle["meta"]["title"] = f"ライブ試合 (seed={self.sim.seed})"
        bundle["meta"]["generator"] = "live-match"
        return bundle

    def standings(self) -> list[dict]:
        """現時点の順位表（順位順）。"""
        expected = self.sim.bundle()["meta"]["expected"]
        return [
            {"rank": rank + 1, "team": ti, **expected["perTeam"][ti]}
            for rank, ti in enumerate(expected["ranking"])
        ]

    def summary(self) -> dict:
        """GET /api/live 用の進行状況。"""
        out = {
            "live": True,
            "status": self.status,
            "seed": self.sim.seed,
            "day": min(self.current_day, self.num_days - 1),
            "numDays": self.num_days,
            "playerTeam": PLAYER_TEAM,
            "standings": self.standings(),
        }
        if self.status == "waiting_agents":
            out["message"] = "POST /api/agents で種別を提出すると試合が始まります"
        elif self.status == "waiting_actions":
            out["message"] = f"POST /api/actions?day={self.current_day} で行動計画を提出してください"
        else:
            out["message"] = "試合終了。GET /api/replay で全記録を取得できます"
        return out
