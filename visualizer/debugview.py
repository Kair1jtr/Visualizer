"""デバッグ GUI (/debug) 用のヘルパー。

- API リクエストログ（純 ASGI ミドルウェア）
- 試合データのサーバーサイド SVG 描画（Jinja2 テンプレートへ埋め込み）
- 行動計画のトレース（サーバー内部でのステップ検算）
"""

import html
import math
import time
from collections import deque

from .hexgrid import apply_direction

SQRT3 = math.sqrt(3)

TERRAIN_NAMES = {0: "平地", 1: "道路", 2: "山地", 3: "池"}
TERRAIN_FILL = {0: "#c9e4b8", 1: "#c9c7c0", 2: "#d9b98a", 3: "#a9d6e5"}
ROAD_STATE_NAMES = {0: "順調", 1: "混雑", 2: "渋滞"}
ROAD_STATE_COLOR = {1: "#eda100", 2: "#d03b3b"}
KIND_NAMES = {0: "巡回車", 1: "補給車"}

# static/js/palette.js のライト系カテゴリカル8色と同じ。
# チームは先頭から、うどん系列は末尾から取る。
_CATEGORICAL = [
    "#2a78d6", "#1baf7a", "#eda100", "#008300",
    "#4a3aa7", "#e34948", "#e87ba4", "#eb6834",
]


def team_color(i: int) -> str:
    return _CATEGORICAL[i % len(_CATEGORICAL)]


def series_color(i: int) -> str:
    return _CATEGORICAL[len(_CATEGORICAL) - 1 - i % len(_CATEGORICAL)]


# ---------------------------------------------------------------------------
# API リクエストログ
# ---------------------------------------------------------------------------

MAX_BODY = 600
REQUEST_LOG: deque = deque(maxlen=200)  # 新しい順


class ApiLogMiddleware:
    """/api/* の送受信を REQUEST_LOG に記録する純 ASGI ミドルウェア。

    BaseHTTPMiddleware と違い receive/send を直接ラップするため、
    リクエストボディを消費せずに写し取れる。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        entry = {
            "time": time.time(),
            "method": scope["method"],
            "path": scope["path"],
            "query": scope.get("query_string", b"").decode("utf-8", "replace"),
            "status": None,
            "duration_ms": None,
            "request_body": "",
            "response_body": "",
        }

        async def recv():
            message = await receive()
            if message["type"] == "http.request" and len(entry["request_body"]) < MAX_BODY:
                entry["request_body"] += message.get("body", b"").decode("utf-8", "replace")
            return message

        async def snd(message):
            if message["type"] == "http.response.start":
                entry["status"] = message["status"]
            elif message["type"] == "http.response.body" and len(entry["response_body"]) < MAX_BODY:
                entry["response_body"] += message.get("body", b"").decode("utf-8", "replace")
            await send(message)

        started = time.perf_counter()
        try:
            await self.app(scope, recv, snd)
        finally:
            entry["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
            for key in ("request_body", "response_body"):
                if len(entry[key]) > MAX_BODY:
                    entry[key] = entry[key][:MAX_BODY] + " …(省略)"
            REQUEST_LOG.appendleft(entry)


# ---------------------------------------------------------------------------
# 行動計画のトレース（検算）
# ---------------------------------------------------------------------------

_STEP_BY_TERRAIN = {0: 2, 2: 3}  # 平地2 / 山地3（道路は状態依存）
_ROAD_STEP = {0: 1, 1: 2, 2: 4}


def trace_plan(plan: list, start: int, match: dict, traffic_map: dict) -> dict:
    """行動計画を出発セルから実行し、ステップ合計と到達セルを返す。"""
    width = match["map"]["width"]
    height = match["map"]["height"]
    cells = match["map"]["cells"]
    cell = start
    total = 0
    for value in plan:
        if value <= -1:
            total += -value
            continue
        r, c = divmod(cell, width)
        code = cells[r][c]
        total += _ROAD_STEP[traffic_map.get(cell, 0)] if code == 1 else _STEP_BY_TERRAIN[code]
        nxt = apply_direction(cell, value, width, height)
        if nxt is None:
            return {"total": total, "cell": cell, "error": "マップ外への移動"}
        nr, nc = divmod(nxt, width)
        if cells[nr][nc] == 3:
            return {"total": total, "cell": cell, "error": "池への移動"}
        cell = nxt
    return {"total": total, "cell": cell, "error": None}


# ---------------------------------------------------------------------------
# サーバーサイド SVG 描画
# ---------------------------------------------------------------------------


def _hex_center(cell: int, width: int, size: float) -> tuple[float, float]:
    row, col = divmod(cell, width)
    x = size * SQRT3 * (col + 0.5 * (row & 1)) + size
    y = size * 1.5 * row + size
    return x, y


def _hex_points(cx: float, cy: float, size: float) -> str:
    pts = []
    for i in range(6):
        angle = math.radians(60 * i - 30)
        pts.append(f"{cx + size * math.cos(angle):.2f},{cy + size * math.sin(angle):.2f}")
    return " ".join(pts)


def render_map_svg(
    match: dict,
    *,
    series_names: list[str],
    team_names: list[str] | None = None,
    team_agents: list[list[dict]] | None = None,
    traffic_map: dict | None = None,
    size: float = 16,
) -> str:
    """マップ（地形・スポット・道路状態・エージェント位置）を SVG 文字列にする。"""
    width = match["map"]["width"]
    height = match["map"]["height"]
    cells = match["map"]["cells"]
    traffic_map = traffic_map or {}
    team_names = team_names or []

    w_px = int(SQRT3 * size * width + size) + 2
    h_px = int(size * 1.5 * (height - 1) + 2 * size) + 2
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w_px} {h_px}" class="map-svg">'
    ]

    # 地形
    for cell in range(width * height):
        r, c = divmod(cell, width)
        code = cells[r][c]
        cx, cy = _hex_center(cell, width, size)
        title = f"#{cell} ({r},{c}) {TERRAIN_NAMES[code]}"
        if code == 1:
            title += f" [{ROAD_STATE_NAMES[traffic_map.get(cell, 0)]}]"
        out.append(
            f'<polygon points="{_hex_points(cx, cy, size)}" fill="{TERRAIN_FILL[code]}"'
            f' stroke="#ffffff" stroke-width="1"><title>{html.escape(title)}</title></polygon>'
        )
        if code == 2:
            out.append(
                f'<text x="{cx:.1f}" y="{cy + size * 0.25:.1f}" text-anchor="middle"'
                f' font-size="{size * 0.6:.1f}" fill="rgba(0,0,0,0.3)" pointer-events="none">▲</text>'
            )
        elif code == 3:
            out.append(
                f'<text x="{cx:.1f}" y="{cy + size * 0.25:.1f}" text-anchor="middle"'
                f' font-size="{size * 0.6:.1f}" fill="rgba(0,0,0,0.3)" pointer-events="none">≈</text>'
            )

    # 道路状態（混雑・渋滞）
    for cell, status in traffic_map.items():
        if status not in ROAD_STATE_COLOR:
            continue
        cx, cy = _hex_center(cell, width, size)
        dash = ' stroke-dasharray="4 3"' if status == 1 else ""
        out.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{size * 0.72:.1f}" fill="none"'
            f' stroke="{ROAD_STATE_COLOR[status]}" stroke-width="2.2"{dash} pointer-events="none"/>'
        )

    # スポット
    for spot in match["spots"]:
        cx, cy = _hex_center(spot["pos"], width, size)
        brand = spot["brand"]
        name = series_names[brand] if brand < len(series_names) else f"系列{brand}"
        label = html.escape(name[:1])
        out.append(
            f'<g><circle cx="{cx:.1f}" cy="{cy:.1f}" r="{size * 0.42:.1f}" fill="#ffffff"'
            f' stroke="{series_color(brand)}" stroke-width="2.4"/>'
            f'<text x="{cx:.1f}" y="{cy + size * 0.22:.1f}" text-anchor="middle"'
            f' font-size="{size * 0.5:.1f}" font-weight="700" fill="#0b0b0b">{label}</text>'
            "<title>"
            + html.escape(f"スポット #{spot['pos']} {name} 在庫上限{spot['stocks']}")
            + "</title></g>"
        )

    # エージェント（チームごと・同一セルは中心の周りにずらす）
    if team_agents:
        occupancy: dict[int, list[tuple[int, int, dict]]] = {}
        for tid, agents in enumerate(team_agents):
            for ai, agent in enumerate(agents):
                occupancy.setdefault(agent["pos"], []).append((tid, ai, agent))
        for pos, group in occupancy.items():
            base_x, base_y = _hex_center(pos, width, size)
            for gi, (tid, ai, agent) in enumerate(group):
                if len(group) == 1:
                    cx, cy = base_x, base_y
                else:
                    angle = 2 * math.pi * gi / len(group) - math.pi / 2
                    cx = base_x + size * 0.45 * math.cos(angle)
                    cy = base_y + size * 0.45 * math.sin(angle)
                color = team_color(tid)
                tname = team_names[tid] if tid < len(team_names) else f"チーム{tid}"
                title = html.escape(
                    f"{tname} #{ai} {KIND_NAMES.get(agent['kind'], '?')}"
                    f" pos={agent['pos']} 燃料{agent['fuel']}"
                )
                r = size * 0.34
                if agent["kind"] == 0:  # 巡回車=円
                    shape = (
                        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{color}"'
                        f' stroke="#ffffff" stroke-width="1.4"/>'
                    )
                else:  # 補給車=ひし形
                    shape = (
                        f'<rect x="{cx - r:.1f}" y="{cy - r:.1f}" width="{r * 2:.1f}"'
                        f' height="{r * 2:.1f}" fill="{color}" stroke="#ffffff" stroke-width="1.4"'
                        f' transform="rotate(45 {cx:.1f} {cy:.1f})"/>'
                    )
                out.append(
                    f"<g>{shape}"
                    f'<text x="{cx:.1f}" y="{cy + size * 0.18:.1f}" text-anchor="middle"'
                    f' font-size="{size * 0.42:.1f}" font-weight="700" fill="#ffffff">{ai}</text>'
                    f"<title>{title}</title></g>"
                )

    out.append("</svg>")
    return "".join(out)
