"""公式資料に掲載されている試合設定を、そのままシミュレーターの入力にしたもの。

状態設計書 第17章「テスト用の初期状態」に対応する。
**公式資料に記載のある値のみ**を使い、記載のない値は補わない
（補えない場合はその旨をコメントに明記する）。
"""

from __future__ import annotations

from .engine import create_game
from .grid import build_grid
from .policies import DEFAULT_POLICIES, Policies
from .state import HexaUdon, MatchConfig, SpotDef
from .terrain import RoadStatus, Terrain

# ---------------------------------------------------------------------------
# テストケース A: 〔補足〕Q&A補足資料（行動詳細）の状態遷移例
# ---------------------------------------------------------------------------

# 〔補足〕の記述:
#   「セル０・１は道路で、セル２・３は平地（ステップ数：２、燃料：１）です。
#    道路の状態は、セル０は順調（ステップ数：１、燃料：２）、
#    セル１は混雑（ステップ数：２、燃料：２）です。
#    セル２には在庫が４のスポットがあります。
#    セル０に巡回車A〜Dの４台、セル２に巡回車Eと補給車Aが１台ずつ、
#    セル３に巡回車F・Gの２台が配置されています。
#    巡回車の最大燃料積載量は３です。
#    １日のステップ数が６ステップ（０〜６ステップ）」

SUPPLEMENT_DAY_STEPS = 6
SUPPLEMENT_FUEL_LIMIT = 3
SUPPLEMENT_SPOT_STOCKS = 4

# 行動計画（〔補足〕の表そのまま）。2=右移動, 5=左移動, 負数=待機。
SUPPLEMENT_PLANS: list[list[int]] = [
    [2, 2, 2, -1],  # 巡回車A  初期セル0
    [-1, 2, 2, 2],  # 巡回車B  初期セル0
    [-2, 2, 2, -1],  # 巡回車C  初期セル0
    [-3, 2, -2],  # 巡回車D  初期セル0
    [5, 5, -2],  # 巡回車E  初期セル2
    [5, 5, -2],  # 巡回車F  初期セル3
    [-2, 5, 5],  # 巡回車G  初期セル3
    [5, 5, -2],  # 補給車A  初期セル2
]

SUPPLEMENT_STARTS = [0, 0, 0, 0, 2, 3, 3, 2]
SUPPLEMENT_KINDS = [0, 0, 0, 0, 0, 0, 0, 1]  # 末尾のみ補給車
SUPPLEMENT_LABELS = [
    "巡回車A", "巡回車B", "巡回車C", "巡回車D",
    "巡回車E", "巡回車F", "巡回車G", "補給車A",
]

# 〔補足〕の「滞在数」行の最終累積値
SUPPLEMENT_EXPECTED_STAY = {0: 12, 1: 17, 2: 12, 3: 7}


def official_supplement_scenario(
    policies: Policies = DEFAULT_POLICIES,
) -> tuple[HexaUdon, list[list[int]]]:
    """〔補足〕の状態遷移例を再現するための初期状態と行動計画を返す。

    注意事項（いずれも〔補足〕がそう書いているため、そのまま採用する）:
      - 図はセル0〜3が**一直線に隣接**した形で示されている。ここでは 1行×4列の
        マップとして表現する。公式のマップサイズ下限（8）を下回るが、これは
        状態遷移の検証専用であり本番設定ではない。
      - 複数のエージェントが同じセルから開始している。〔Q37〕の
        「初期位置は全て異なるセル」には反するが、〔補足〕の例をそのまま使う。
      - 道路状態（セル0=順調、セル1=混雑）は〔補足〕が前提として与えている値であり、
        交通量から導いたものではない。呼び出し側で `begin_day` の後に上書きする。
    """
    grid = build_grid(
        height=1,
        width=4,
        cells=[[Terrain.ROAD, Terrain.ROAD, Terrain.PLAIN, Terrain.PLAIN]],
        policies=policies,
    )
    config = MatchConfig(
        daySteps=(SUPPLEMENT_DAY_STEPS,),
        daySeconds=(60,),  # 〔補足〕に記載なし。状態遷移には影響しない
        fuelLimits=SUPPLEMENT_FUEL_LIMIT,
        # 閾値は〔補足〕に記載がない。この例では道路状態を直接与えるため使われない。
        busyThreshold=1,
        jammedThreshold=2,
        players=1,
        policies=policies,
    )
    spots = [SpotDef(pos=2, brand=0, stocks=SUPPLEMENT_SPOT_STOCKS)]
    state = create_game(grid, config, spots, SUPPLEMENT_STARTS, [SUPPLEMENT_KINDS])
    return state, [list(p) for p in SUPPLEMENT_PLANS]


def apply_supplement_road_status(state: HexaUdon) -> None:
    """〔補足〕が前提として与えている道路状態を設定する。

    セル0 = 順調、セル1 = 混雑。`begin_day` の直後に呼ぶ。
    """
    state.traffic.traffics = {
        0: RoadStatus.SMOOTH,
        1: RoadStatus.CONGESTED,
    }


# ---------------------------------------------------------------------------
# テストケース B: 〔書式〕フォーマット資料のマップ構成例
# ---------------------------------------------------------------------------

# 〔書式〕の例（8×8）。全行が同じ地形の並び。
FORMAT_EXAMPLE_CELLS = [[3, 0, 1, 2, 0, 1, 2, 0] for _ in range(8)]
FORMAT_EXAMPLE_SPOTS = [
    SpotDef(pos=1, brand=0, stocks=4),
    SpotDef(pos=9, brand=1, stocks=1),
    SpotDef(pos=17, brand=0, stocks=1),
    SpotDef(pos=25, brand=1, stocks=3),
]
FORMAT_EXAMPLE_STARTS = [4, 12, 20, 28]


def format_example_scenario(
    num_teams: int = 2,
    kinds_by_team: list[list[int]] | None = None,
    policies: Policies = DEFAULT_POLICIES,
) -> HexaUdon:
    """〔書式〕のマップ構成例をそのまま入力にした状態を返す。

    注意: 〔書式〕の `fuelLimits: 20` は `daySteps: [50,100,150,200]` に対して
    **サンプル上の誤り**であることが〔Q62〕で確定している
    （daySteps が50なら fuelLimits は50〜150）。そのためこの関数では
    燃料に関する検証に使わないこと。値は資料のまま 20 を用いる。
    """
    grid = build_grid(8, 8, FORMAT_EXAMPLE_CELLS, policies)
    config = MatchConfig(
        daySteps=(50, 100, 150, 200),
        daySeconds=(5, 5, 5, 10),
        fuelLimits=20,  # 〔Q62〕によりサンプル上の誤り
        busyThreshold=2,
        jammedThreshold=4,
        players=num_teams,
        policies=policies,
    )
    if kinds_by_team is None:
        kinds_by_team = [[0, 0, 0, 1] for _ in range(num_teams)]
    return create_game(
        grid, config, FORMAT_EXAMPLE_SPOTS, FORMAT_EXAMPLE_STARTS, kinds_by_team
    )


# ---------------------------------------------------------------------------
# テストケース C: 単体検証用の最小構成（公式資料に存在しない自作ケース）
# ---------------------------------------------------------------------------


def minimal_scenario(
    cells: list[list[int]],
    spots: list[SpotDef],
    starts: list[int],
    kinds_by_team: list[list[int]],
    day_steps: tuple[int, ...],
    *,
    fuel_limits: int = 10,
    busy_threshold: int = 1,
    jammed_threshold: int = 2,
    policies: Policies = DEFAULT_POLICIES,
) -> HexaUdon:
    """ルール単体を切り出して確認するための小さな盤面を作る。

    **公式資料に存在しない構成**であり、マップサイズ・エージェント数・日数が
    公式の範囲（状態設計書 第2章）を下回ることがある。本番設定ではない。
    """
    height = len(cells)
    width = len(cells[0]) if cells else 0
    grid = build_grid(height, width, cells, policies)
    config = MatchConfig(
        daySteps=day_steps,
        daySeconds=tuple(60 for _ in day_steps),
        fuelLimits=fuel_limits,
        busyThreshold=busy_threshold,
        jammedThreshold=jammed_threshold,
        players=len(kinds_by_team),
        policies=policies,
    )
    return create_game(grid, config, spots, starts, kinds_by_team)
