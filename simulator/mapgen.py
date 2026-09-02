"""マップ生成。`compare.py`／`experiment.py`と同じく、ここもルールや実行を
持たない薄い層。`tests/test_configuration_matrix.py`にあった決定的な正方形
マップ生成を切り出し、`experiment.py`のマップサイズ×構成の実験（指示書#3・#6）
がテストコードに依存せず使えるようにする。

    from simulator import mapgen, experiment

    factory = mapgen.square_scenario_factory(16, num_patrol=3, num_supply=1)
    condition = experiment.ExperimentCondition(
        label="16x16", state_factory=factory, strategies={0: "greedy"},
    )
"""

from __future__ import annotations

import random
from collections.abc import Callable

from . import scenarios
from .state import HexaUdon, SpotDef
from .terrain import Terrain

PLAIN, ROAD, MOUNTAIN = int(Terrain.PLAIN), int(Terrain.ROAD), int(Terrain.MOUNTAIN)


def square_map(size: int) -> list[list[int]]:
    """`size`×`size`の決定的なマップ。中央に縦1本の道路を通し、山地を規則的に散らす。

    池は置かないので、道路が無くても必ず全セルが連結する。
    """
    if size < 1:
        raise ValueError(f"size は1以上にしてください: {size}")
    cells = [[PLAIN] * size for _ in range(size)]
    mid = size // 2
    for r in range(size):
        cells[r][mid] = ROAD
    for r in range(0, size, 3):
        for c in range(0, size, 5):
            if c != mid:
                cells[r][c] = MOUNTAIN
    return cells


def plain_cells(cells: list[list[int]]) -> list[int]:
    """平地セルの番号一覧（行優先）。スポット・初期位置の置き場所探しに使う。"""
    width = len(cells[0]) if cells else 0
    return [r * width + c for r, row in enumerate(cells) for c, v in enumerate(row) if v == PLAIN]


def square_map_random(size: int, seed: int, *, mountain_ratio: float = 0.12) -> list[list[int]]:
    """`square_map()`のランダム版。中央の道路は同じだが、山地の位置を`seed`で散らす。

    山地は進入不可ではない〔要項〕ため、比率をいくら上げても道路の縦一直線が
    全行を貫通する限り盤面全体の連結性は崩れない。
    """
    if size < 1:
        raise ValueError(f"size は1以上にしてください: {size}")
    rng = random.Random(seed)
    cells = [[PLAIN] * size for _ in range(size)]
    mid = size // 2
    for r in range(size):
        cells[r][mid] = ROAD
    for r in range(size):
        for c in range(size):
            if c != mid and rng.random() < mountain_ratio:
                cells[r][c] = MOUNTAIN
    return cells


def _build_scenario(
    cells: list[list[int]],
    spot_cells: list[int],
    start_cells: list[int],
    num_patrol: int,
    num_supply: int,
    num_teams: int,
    num_days: int,
    size: int,
) -> HexaUdon:
    num_agents = num_patrol + num_supply
    if len(start_cells) < num_agents:
        raise ValueError(
            f"{size}×{size} では平地が足りません（必要{num_agents}、残り{len(start_cells)}）"
        )
    spots = [SpotDef(pos=c, brand=i % 4, stocks=2) for i, c in enumerate(spot_cells)]
    kinds = [0] * num_patrol + [1] * num_supply
    day_steps = tuple([size * 2] * num_days)
    return scenarios.minimal_scenario(
        cells=cells,
        spots=spots,
        starts=start_cells,
        kinds_by_team=[list(kinds) for _ in range(num_teams)],
        day_steps=day_steps,
        fuel_limits=size * 2 * 2,
        busy_threshold=3,
        jammed_threshold=6,
    )


def square_scenario_factory(
    size: int,
    num_patrol: int,
    num_supply: int,
    *,
    num_teams: int = 1,
    num_days: int = 4,
    num_spots: int | None = None,
) -> Callable[[], HexaUdon]:
    """`experiment.ExperimentCondition.state_factory`にそのまま渡せる関数を返す。

    - スポットは平地に置く〔要項〕。数はエージェント数以上にする〔Q15〕
    - エージェント初期位置はスポットの無い平地〔要項〕、かつ全て異なるセル〔Q37〕
    - `daySteps`は公式範囲`W+H 〜 (W+H)×4`の下限に合わせる〔Q20〕
    - 燃料上限は1日目ステップ数の2倍（公式は1〜3倍の範囲内）〔Q60〕

    毎回同じ配置を返す（乱数を使わない）。呼ぶたびに新しい`HexaUdon`を作るので、
    `experiment.run_matrix()`のように同じ条件を複数回実行しても状態は共有されない。
    """
    num_agents = num_patrol + num_supply
    spots_needed = num_spots if num_spots is not None else max(num_agents, 8)

    def factory() -> HexaUdon:
        cells = square_map(size)
        plains = plain_cells(cells)
        spot_cells = plains[:spots_needed]
        start_cells = plains[spots_needed : spots_needed + num_agents]
        return _build_scenario(
            cells, spot_cells, start_cells, num_patrol, num_supply, num_teams, num_days, size
        )

    return factory


def square_scenario_factory_random(
    size: int,
    num_patrol: int,
    num_supply: int,
    seed: int,
    *,
    num_teams: int = 1,
    num_days: int = 4,
    num_spots: int | None = None,
    mountain_ratio: float = 0.12,
) -> Callable[[], HexaUdon]:
    """`square_scenario_factory()`のランダム版。山地の配置とスポット・初期位置の
    割り当てを`seed`でランダム化する。

    `experiment.run_repeated()`と組み合わせ、「同じ戦略・同じ構成を多数の
    異なるマップで走らせて結果のばらつきを見る」という統計的評価（指示書#8）
    に使う。`seed`が同じなら毎回同じマップ・配置を返す（再現可能）。
    """
    num_agents = num_patrol + num_supply
    spots_needed = num_spots if num_spots is not None else max(num_agents, 8)

    def factory() -> HexaUdon:
        cells = square_map_random(size, seed, mountain_ratio=mountain_ratio)
        plains = plain_cells(cells)
        random.Random(seed).shuffle(plains)
        spot_cells = plains[:spots_needed]
        start_cells = plains[spots_needed : spots_needed + num_agents]
        return _build_scenario(
            cells, spot_cells, start_cells, num_patrol, num_supply, num_teams, num_days, size
        )

    return factory
