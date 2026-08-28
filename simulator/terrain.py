"""地形・道路状態と移動コスト表（〔要項〕表1）。

移動に要するステップ数・消費燃料は、いずれも
**移動命令を受けた時点の現在地（移動元）の地形**で決まる。
〔要項〕〔Q10〕〔Q25〕【確定】
"""

from __future__ import annotations

from enum import IntEnum


class Terrain(IntEnum):
    """地形タイプ。値は公式JSONの `cells` の値そのもの。〔書式〕【確定】"""

    PLAIN = 0  # 平地
    ROAD = 1  # 道路
    MOUNTAIN = 2  # 山地
    POND = 3  # 池（進入不可）


class RoadStatus(IntEnum):
    """道路セルの状態。値は公式JSONの `traffics[].status` の値。〔書式〕【確定】"""

    SMOOTH = 0  # 順調
    CONGESTED = 1  # 混雑
    JAMMED = 2  # 渋滞


class AgentKind(IntEnum):
    """エージェント種別。値は公式JSONの `kind` の値。〔書式〕【確定】"""

    PATROL = 0  # 巡回車
    SUPPLY = 1  # 補給車


# 〔要項〕表1【確定】: (移動ステップ数, 消費燃料)
# 池は進入不可のため表に含めない。
_PLAIN = (2, 1)
_MOUNTAIN = (3, 2)
_ROAD_BY_STATUS = {
    RoadStatus.SMOOTH: (1, 2),
    RoadStatus.CONGESTED: (2, 2),
    RoadStatus.JAMMED: (4, 2),
}


class ImpassableTerrain(Exception):
    """池など進入不可の地形から／へ移動しようとした。"""


def move_cost(terrain: Terrain, road_status: RoadStatus | None) -> tuple[int, int]:
    """出発セルの地形から (必要ステップ数, 消費燃料) を返す。〔要項〕表1【確定】

    `road_status` は `terrain` が ROAD のときのみ参照する。
    池は移動の出発地になり得ないため例外とする。
    """
    if terrain == Terrain.PLAIN:
        return _PLAIN
    if terrain == Terrain.MOUNTAIN:
        return _MOUNTAIN
    if terrain == Terrain.ROAD:
        if road_status is None:
            raise ValueError("道路セルの移動コストには道路状態が必要です")
        return _ROAD_BY_STATUS[RoadStatus(road_status)]
    raise ImpassableTerrain(f"地形 {terrain!r} からは移動できません")


def is_enterable(terrain: Terrain) -> bool:
    """そのセルに進入できるか。池のみ不可。〔要項〕【確定】"""
    return terrain != Terrain.POND


TERRAIN_LABEL = {
    Terrain.PLAIN: "平地",
    Terrain.ROAD: "道路",
    Terrain.MOUNTAIN: "山地",
    Terrain.POND: "池",
}

ROAD_STATUS_LABEL = {
    RoadStatus.SMOOTH: "順調",
    RoadStatus.CONGESTED: "混雑",
    RoadStatus.JAMMED: "渋滞",
}

AGENT_KIND_LABEL = {
    AgentKind.PATROL: "巡回車",
    AgentKind.SUPPLY: "補給車",
}
