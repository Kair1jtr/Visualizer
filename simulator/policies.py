"""未確定仕様の切り替えポイント（Policy）。

`docs/状態設計書.md` 第19章B「未確認状態一覧」で【未確認】とされた事項のうち、
シミュレーションの結果を変えうるものを **この1ファイルに集約** する。
公式仕様が確定したら、ここの既定値を変えるだけで全体が追随する。

コード中の他の場所で同じ判断を再実装してはならない（実装指示書 第7章）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CellIndexing(Enum):
    """U-1: セル番号の割り当て規則。

    〔要項〕は「図1のように」と図に委ねており、本文に規則の記述がない。
    〔設定〕（試合設定JSON構造説明。簡易サーバー付属＝補助資料）のみが
    `pos = y * width + x` の行優先と明記している。
    公式競技資料では【未確認】のため、ここで切り替え可能にしておく。
    """

    ROW_MAJOR = "row_major"  # pos = y * width + x  〔設定〕【補助資料】（既定）
    COLUMN_MAJOR = "column_major"  # pos = x * height + y  （対抗仮説）


class RowOffset(Enum):
    """六角格子のオフセット方向。

    〔Q1〕「偶数行が右にずれる形で固定されています」により **確定**。
    U-1（セル番号の割り当て）とは別の軸なので独立させるが、
    既定値から変更する理由は現時点では無い。
    """

    EVEN_RIGHT = "even_right"  # 偶数行が右にずれる 〔Q1〕【確定】
    ODD_RIGHT = "odd_right"  # 奇数行が右にずれる （比較検証用）


class TrafficDivision(Enum):
    """U-3: 交通量の「チーム数で割った値」の扱い。

    〔要項〕は「合算し、チーム数で割った値」とのみ記述し、
    整数除算か実数かを明記していない。

    重要な観察:
        閾値は **正の整数** 〔Q30〕【確定】である。整数 t に対して
        `floor(x) >= t` と `x >= t` は同値なので、**EXACT と FLOOR は
        判定結果が完全に一致する**。したがって U-3 が実際に結果を変えるのは
        CEIL / ROUND_HALF_UP を採る場合に限られる。
    """

    EXACT = "exact"  # 実数のまま比較（既定。整数閾値では FLOOR と同値）
    FLOOR = "floor"  # 切り捨て
    CEIL = "ceil"  # 切り上げ
    ROUND_HALF_UP = "round_half_up"  # 四捨五入


class SecondDayDivisor(Enum):
    """U-4: 2日目の交通量における除数の扱い。

    〔要項〕「2日目は1日目の交通量のみで道路の状態が決まります」。
    前々日が存在しない分、除数を調整するのかどうかの記述がない。
    """

    TEAMS = "teams"  # 常にチーム数で割る（既定。〔要項〕の定義式をそのまま適用）
    TEAMS_TIMES_DAYS = "teams_times_days"  # チーム数 × 参照日数 で割る（対抗仮説）


class AgentOrder(Enum):
    """U-5: 反映フェーズでエージェントを処理する順序。

    〔Q22〕は「Bの移動の反映が先に行われる」と順序の存在に言及するが、
    順序を決める規則そのものは明記されていない。
    〔Q26〕はうどん獲得の競合について「リスト内の順番が若いエージェントが先」と
    定めている【確定】ので、既定はそれに合わせる。

    なお補給の成否は「全エージェントの移動を反映し終えた後」に判定するため
    （engine.py 参照）、この順序は補給の結果を変えない。
    """

    AGENT_ID = "agent_id"  # エージェントID昇順（既定。〔Q26〕に整合）
    REVERSED_ID = "reversed_id"  # 降順（影響の有無を確認するための対照）


class FuelTiming(Enum):
    """U-6: 複数ステップを要する移動での燃料消費タイミング。

    〔状態設計書〕では【未確認】としていたが、〔補足〕の状態遷移表から
    **ON_ARRIVAL であることが確定できる**（根拠は docs/実装ノート.md 参照）。
    実装指示書 第7章の指示に従い切り替え可能にしたうえで、
    既定を確定値に置く。
    """

    ON_ARRIVAL = "on_arrival"  # 移動が完了する反映フェーズで全額消費（既定・〔補足〕で確認）
    ON_FIRST_REFLECTION = "on_first_reflection"  # 予約直後の反映フェーズで全額消費
    ON_RESERVATION = "on_reservation"  # アクションフェーズで消費（〔Q6〕に反する。対照用）


@dataclass(frozen=True)
class Policies:
    """未確定仕様の選択をまとめた設定。GameState から参照される。"""

    cell_indexing: CellIndexing = CellIndexing.ROW_MAJOR
    row_offset: RowOffset = RowOffset.EVEN_RIGHT
    traffic_division: TrafficDivision = TrafficDivision.EXACT
    second_day_divisor: SecondDayDivisor = SecondDayDivisor.TEAMS
    agent_order: AgentOrder = AgentOrder.AGENT_ID
    fuel_timing: FuelTiming = FuelTiming.ON_ARRIVAL

    def describe(self) -> list[str]:
        """採用中の仮仕様を一覧化する（ログ・レポート用）。"""
        return [
            f"U-1 セル番号の割り当て      : {self.cell_indexing.value}",
            f"    六角オフセット（確定）  : {self.row_offset.value}",
            f"U-3 交通量の除算            : {self.traffic_division.value}",
            f"U-4 2日目の除数             : {self.second_day_divisor.value}",
            f"U-5 反映フェーズの処理順序  : {self.agent_order.value}",
            f"U-6 燃料消費タイミング      : {self.fuel_timing.value}",
        ]


DEFAULT_POLICIES = Policies()
