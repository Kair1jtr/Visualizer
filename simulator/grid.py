"""六角格子（マップ）— セル番号・座標・隣接関係。

**U-1 / U-2 の隔離場所。** セル番号と (行, 列) の相互変換、および方向コードから
隣接セルを求める処理は、この1ファイルにのみ実装する。
公式でセル番号の割り当て規則が確定したら、ここを直せば全体が追随する。
（実装指示書 第7章）

確定していること:
    - 各セルに 0 〜 (縦×横 − 1) の整数値が割り当てられる       〔要項〕【確定】
    - 各セルは六方向で別セルと隣接する                         〔要項〕【確定】
    - 六角格子は **偶数行が右にずれる** 形で固定               〔Q1〕【確定】
    - 方向コード 0:左上 1:右上 2:右 3:右下 4:左下 5:左         〔書式〕〔Q47〕【確定】
    - マップはトーラスではない（ラップアラウンドなし）         〔Q2〕【確定】

未確認:
    - **セル番号の割り当て規則そのもの**。〔要項〕は図1に委ねており本文に
      記述がない。行優先（pos = y*width + x）は〔設定〕にのみ記載があり、
      これは簡易サーバー付属の補助資料である。             → U-1【未確認】
"""

from __future__ import annotations

from dataclasses import dataclass

from .policies import CellIndexing, Policies, RowOffset
from .terrain import Terrain

# 方向コード → axial 座標系での差分。0:左上 から時計回り。〔書式〕〔Q47〕【確定】
_AXIAL_DELTA: tuple[tuple[int, int], ...] = (
    (0, -1),  # 0 左上
    (1, -1),  # 1 右上
    (1, 0),  # 2 右
    (0, 1),  # 3 右下
    (-1, 1),  # 4 左下
    (-1, 0),  # 5 左
)

DIRECTION_LABEL = ("左上", "右上", "右", "右下", "左下", "左")
NUM_DIRECTIONS = 6


@dataclass(frozen=True)
class HexGrid:
    """マップの形状。試合中は不変。〔要項〕【確定】"""

    height: int
    width: int
    cells: tuple[tuple[Terrain, ...], ...]  # cells[row][col]
    policies: Policies

    # ----- U-1: セル番号 ↔ (行, 列) -----

    def to_rc(self, cell: int) -> tuple[int, int]:
        """セル番号 → (行, 列)。U-1 に依存する。"""
        if self.policies.cell_indexing is CellIndexing.ROW_MAJOR:
            return divmod(cell, self.width)
        # COLUMN_MAJOR: pos = x * height + y
        col, row = divmod(cell, self.height)
        return row, col

    def to_cell(self, row: int, col: int) -> int:
        """(行, 列) → セル番号。U-1 に依存する。"""
        if self.policies.cell_indexing is CellIndexing.ROW_MAJOR:
            return row * self.width + col
        return col * self.height + row

    # ----- U-2: 隣接関係 -----

    def _shifted_right(self, row: int) -> bool:
        """その行が右に半セルずれているか。〔Q1〕【確定】"""
        if self.policies.row_offset is RowOffset.EVEN_RIGHT:
            return row % 2 == 0
        return row % 2 == 1

    def _row_offset(self, row: int) -> int:
        """行 `row` の列番号を axial の q に直すための補正量。

        偶数行が右にずれる (even-r) 場合: q = col - (row + row%2) // 2
        奇数行が右にずれる (odd-r)  場合: q = col - (row - row%2) // 2
        """
        if self.policies.row_offset is RowOffset.EVEN_RIGHT:
            return (row + (row % 2)) // 2
        return (row - (row % 2)) // 2

    def _to_axial(self, row: int, col: int) -> tuple[int, int]:
        return col - self._row_offset(row), row

    def _from_axial(self, q: int, r: int) -> tuple[int, int]:
        return r, q + self._row_offset(r)

    def neighbor(self, cell: int, direction: int) -> int | None:
        """セル `cell` から方向コード `direction` へ1セル移動した先。

        盤外なら None を返す（マップはトーラスではない。〔Q2〕【確定】）。
        """
        if not 0 <= direction < NUM_DIRECTIONS:
            raise ValueError(f"方向コードは 0〜5 です: {direction}")
        row, col = self.to_rc(cell)
        q, r = self._to_axial(row, col)
        dq, dr = _AXIAL_DELTA[direction]
        nrow, ncol = self._from_axial(q + dq, r + dr)
        if not (0 <= nrow < self.height and 0 <= ncol < self.width):
            return None
        return self.to_cell(nrow, ncol)

    def neighbors(self, cell: int) -> list[int]:
        """盤内の隣接セル一覧。"""
        out = []
        for d in range(NUM_DIRECTIONS):
            nb = self.neighbor(cell, d)
            if nb is not None:
                out.append(nb)
        return out

    # ----- 地形 -----

    def terrain_at(self, cell: int) -> Terrain:
        row, col = self.to_rc(cell)
        return self.cells[row][col]

    @property
    def num_cells(self) -> int:
        return self.height * self.width

    def all_cells(self) -> range:
        return range(self.num_cells)

    def road_cells(self) -> list[int]:
        """道路セルの一覧（交通量の対象。〔要項〕【確定】）。

        `cells` から毎回導出できる値であり状態ではない（キャッシュ用途）。
        """
        return [c for c in self.all_cells() if self.terrain_at(c) == Terrain.ROAD]

    def in_bounds(self, cell: int) -> bool:
        return 0 <= cell < self.num_cells


def build_grid(
    height: int,
    width: int,
    cells: list[list[int]],
    policies: Policies,
) -> HexGrid:
    """公式フォーマットの `map` から HexGrid を作る。〔書式〕"""
    if len(cells) != height:
        raise ValueError(f"cells の行数が height と一致しません: {len(cells)} != {height}")
    for r, row in enumerate(cells):
        if len(row) != width:
            raise ValueError(f"cells[{r}] の要素数が width と一致しません: {len(row)} != {width}")
    return HexGrid(
        height=height,
        width=width,
        cells=tuple(tuple(Terrain(v) for v in row) for row in cells),
        policies=policies,
    )
