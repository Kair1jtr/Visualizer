"""状態モデル。`docs/状態設計書.md` の定義をそのままコードに対応させる。

確定度の表記は状態設計書に準拠する:
    【確定】公式競技資料から直接確認できる
    【推定】実装上必要だが公式資料に明記がない（＝公式仕様として扱わない）
    【未確認】資料だけでは決定できない
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .grid import HexGrid
from .policies import Policies
from .terrain import AgentKind, RoadStatus

# ---------------------------------------------------------------------------
# 試合設定（試合中不変）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchConfig:
    """試合開始時に確定し、以後変化しない設定。状態設計書 第2章。"""

    day_steps: tuple[int, ...]  # 各日のステップ数。要素数＝日数 〔書式〕【確定】
    day_seconds: tuple[int, ...]  # 各日の回答時間（秒） 〔書式〕【確定】
    fuel_limits: int  # 燃料積載量上限 〔書式〕【確定】
    busy_threshold: int  # 混雑基準値（1〜5の正整数） 〔Q13〕〔Q30〕【確定】
    jammed_threshold: int  # 渋滞基準値（2〜10の正整数） 〔Q13〕〔Q30〕【確定】
    num_teams: int  # 参加チーム数 〔書式〕【確定】
    policies: Policies  # 未確定仕様の選択

    @property
    def num_days(self) -> int:
        """日数。`daySteps` の配列要素数が日数を表す。〔書式〕【確定】"""
        return len(self.day_steps)


@dataclass(frozen=True)
class SpotDef:
    """スポットの静的定義。試合中不変。〔要項〕〔書式〕【確定】

    1セルに配置されるスポットは1つまで〔Q18〕〔Q34〕【確定】なので、
    セル番号でスポットを一意に引ける（スポットIDは別途持たない）。
    """

    pos: int  # 設置セル番号（平地）
    brand: int  # 系列（整数）
    stocks: int  # 最大在庫数


# ---------------------------------------------------------------------------
# 行動の予約（アクションフェーズの結果）
# ---------------------------------------------------------------------------


@dataclass
class ReservedAction:
    """アクションフェーズで予約された行動。

    公式は「次のアクション（移動 or 待機）の**反映の予約**を行う」と定めている
    〔Q6〕〔補足〕【確定】が、`remaining_steps` などの内部表現は公式仕様ではない
    【推定】（状態設計書 P-1〜P-3）。
    """

    is_move: bool  # True:移動 / False:待機
    remaining_steps: int  # 残り所要ステップ数 【推定】
    target: int | None = None  # 移動先セル（移動時のみ） 【推定】
    fuel_cost: int = 0  # 消費燃料（移動命令を受けた時点の出発セル地形で決定）〔要項〕【確定】
    direction: int | None = None  # 方向コード（ログ用）
    fuel_consumed: bool = False  # 既に燃料を消費済みか（U-6 の判定に使う） 【推定】


# ---------------------------------------------------------------------------
# エージェント
# ---------------------------------------------------------------------------


@dataclass
class AgentState:
    """エージェント1体の状態。状態設計書 第7章。"""

    agent_id: int  # `agents` リスト内の順序 〔書式〕【確定】
    kind: AgentKind  # 種別。試合中不変 〔要項〕【確定】
    pos: int  # 現在位置 〔書式〕【確定】
    fuel: int  # 燃料。巡回車のみ意味を持つ 〔要項〕【確定】

    # --- 以下は内部表現【推定】 ---
    reserved: ReservedAction | None = None  # P-1/P-2/P-3
    plan: tuple[int, ...] = ()  # その日の行動計画 〔書式〕【確定】
    plan_cursor: int = 0  # 行動計画の消化位置 P-4
    acquired_spots_today: set[int] = field(default_factory=set)  # P-5

    @property
    def is_patrol(self) -> bool:
        return self.kind == AgentKind.PATROL

    @property
    def is_moving(self) -> bool:
        return self.reserved is not None and self.reserved.is_move


# ---------------------------------------------------------------------------
# チーム
# ---------------------------------------------------------------------------


@dataclass
class TeamState:
    """チームの状態。状態設計書 第6章。"""

    team_id: int  # 【推定】P-10
    agents: list[AgentState]

    # スポット在庫はチームごとに独立 〔要項〕【確定】
    spot_stocks: dict[int, int] = field(default_factory=dict)

    total_udon: int = 0  # 玉数の累計（勝敗③） 〔要項〕【確定】
    brands_all: set[int] = field(default_factory=set)  # 獲得系列（勝敗①） 〔要項〕【確定】
    brands_today: set[int] = field(default_factory=set)  # 当日獲得系列 【推定】P-6
    daily_brand_counts: list[int] = field(default_factory=list)  # 日ごと種類数 【推定】P-7

    # 勝敗④。計測基準が【未確認】U-7 のため、外部から与えられる値として扱う
    response_time_total: float = 0.0

    def patrols(self) -> list[AgentState]:
        return [a for a in self.agents if a.is_patrol]

    def supplies(self) -> list[AgentState]:
        return [a for a in self.agents if not a.is_patrol]

    # ----- 勝敗判定に使う導出値（保存しない。状態設計書 第15.2節） -----

    @property
    def brand_count(self) -> int:
        """勝敗① 1試合で獲得したうどんの種類数。"""
        return len(self.brands_all)

    @property
    def daily_brand_cumulative(self) -> int:
        """勝敗② 各日の獲得種類数の累積。"""
        return sum(self.daily_brand_counts)

    def score_key(self) -> tuple:
        """勝敗判定の比較キー。①②③は多い順、④は少ない順。〔要項〕【確定】"""
        return (
            -self.brand_count,
            -self.daily_brand_cumulative,
            -self.total_udon,
            self.response_time_total,
        )


# ---------------------------------------------------------------------------
# 交通量
# ---------------------------------------------------------------------------


@dataclass
class TrafficState:
    """交通量と道路状態。状態設計書 第9章。

    滞在ステップ数は **全チーム分を合算した値** を保持する 〔要項〕【確定】。
    交通量そのものは (前日 + 前々日) ÷ チーム数 で一意に計算できるため保持しない
    （状態設計書 第9.3節）。
    """

    road_status: dict[int, RoadStatus] = field(default_factory=dict)
    stay_today: dict[int, int] = field(default_factory=dict)  # 【推定】P-8
    stay_prev1: dict[int, int] = field(default_factory=dict)  # 【推定】P-8
    stay_prev2: dict[int, int] = field(default_factory=dict)  # 【推定】P-8

    def status_of(self, cell: int) -> RoadStatus | None:
        return self.road_status.get(cell)

    def shift_days(self) -> None:
        """日終了時に 当日 → 前日 → 前々日 へ繰り越す。【推定】"""
        self.stay_prev2 = self.stay_prev1
        self.stay_prev1 = self.stay_today
        self.stay_today = {}


# ---------------------------------------------------------------------------
# ゲーム全体
# ---------------------------------------------------------------------------


@dataclass
class GameState:
    """試合全体の状態。状態設計書 第3章。"""

    config: MatchConfig
    grid: HexGrid
    spots: tuple[SpotDef, ...]
    teams: list[TeamState]
    traffic: TrafficState = field(default_factory=TrafficState)
    day: int = 0  # 現在の日（0始まり） 〔書式〕【確定】
    step: int = 0  # 現在のステップ 【推定】P-9
    finished: bool = False

    # セル番号 → スポット定義（1セル1スポット〔Q18〕なので一意）。キャッシュ。
    _spot_by_cell: dict[int, SpotDef] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self._spot_by_cell:
            # 1セルに配置されるスポットは1つまで 〔Q18〕〔Q34〕【確定】。
            # 重複を黙って握りつぶすと、片方のスポットが存在しないことになり
            # 結果が静かに狂うため、入力段階で弾く。
            seen: dict[int, SpotDef] = {}
            for spot in self.spots:
                if spot.pos in seen:
                    raise ValueError(
                        f"1セルに複数のスポットが指定されています（セル {spot.pos}: "
                        f"系列 {seen[spot.pos].brand} と {spot.brand}）。"
                        f"1セルのスポットは1つまでです〔Q18〕〔Q34〕"
                    )
                seen[spot.pos] = spot
            self._spot_by_cell = seen

    def spot_at(self, cell: int) -> SpotDef | None:
        return self._spot_by_cell.get(cell)

    @property
    def steps_today(self) -> int:
        """その日のステップ数 N。〔書式〕【確定】"""
        return self.config.day_steps[self.day]

    def all_agents(self):
        """(チーム, エージェント) を順に返す。"""
        for team in self.teams:
            for agent in team.agents:
                yield team, agent

    def clone(self) -> "GameState":
        """検証用の使い捨てコピー（dry-run 用）。"""
        return copy.deepcopy(self)

    def ranking(self) -> list[TeamState]:
        """勝敗判定順に並べたチーム一覧。〔要項〕【確定】

        ⑤（サイコロ等または引き分け）はシミュレーター対象外のため、
        ①〜④が完全に同値の場合は team_id 昇順で安定させる。
        """
        return sorted(self.teams, key=lambda t: (t.score_key(), t.team_id))
