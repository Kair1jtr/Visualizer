"""ヘキサうどん のマップ（六角形グリッド）座標計算。

セル番号は行優先で 0 〜 (縦×横-1)。募集要項の図1の座標例と同じ並び。
六角形は pointy-top、偶数行が右に半セルずれる even-r オフセット配置
（公式Q&Aその1 Q1/A1で確定: 「偶数行が右にずれる形で固定されています」）。
"""

# axial 座標系での6方向
_DIRECTIONS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))

# 公式回答フォーマットの方向コード → axial 方向。
# 0:左上, 1:右上, 以降時計回り (2:右, 3:右下, 4:左下, 5:左)
DIRECTION_CODES = {
    0: (0, -1),   # 左上 (NW)
    1: (1, -1),   # 右上 (NE)
    2: (1, 0),    # 右   (E)
    3: (0, 1),    # 右下 (SE)
    4: (-1, 1),   # 左下 (SW)
    5: (-1, 0),   # 左   (W)
}


def _to_axial(cell: int, width: int) -> tuple[int, int]:
    row, col = divmod(cell, width)
    return col - (row + (row & 1)) // 2, row


def direction_code(src: int, dst: int, width: int) -> int | None:
    """隣接セル src→dst の移動を公式方向コード(0〜5)に変換する。非隣接は None。"""
    sq, sr = _to_axial(src, width)
    dq, dr = _to_axial(dst, width)
    delta = (dq - sq, dr - sr)
    for code, d in DIRECTION_CODES.items():
        if d == delta:
            return code
    return None


def apply_direction(cell: int, code: int, width: int, height: int) -> int | None:
    """セル cell から方向コード code へ1セル移動した先を返す。盤外は None。"""
    q, r = _to_axial(cell, width)
    dq, dr = DIRECTION_CODES[code]
    nq, nr = q + dq, r + dr
    if not 0 <= nr < height:
        return None
    nc = nq + (nr + (nr & 1)) // 2
    if not 0 <= nc < width:
        return None
    return nr * width + nc


def id_to_rc(cell: int, width: int) -> tuple[int, int]:
    return divmod(cell, width)


def rc_to_id(row: int, col: int, width: int) -> int:
    return row * width + col


def neighbors(cell: int, width: int, height: int) -> list[int]:
    """セル cell に隣接する（盤内の）セル番号を返す。"""
    row, col = divmod(cell, width)
    q = col - (row + (row & 1)) // 2
    result = []
    for dq, dr in _DIRECTIONS:
        nq, nr = q + dq, row + dr
        if not 0 <= nr < height:
            continue
        nc = nq + (nr + (nr & 1)) // 2
        if 0 <= nc < width:
            result.append(nr * width + nc)
    return result
