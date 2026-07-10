"""ヘキサうどん Visualizer — FastAPI サーバー。

公式資料「競技部門『ヘキサうどん』のフォーマットについて」に準拠した
モック競技サーバー + ビジュアライザ配信を行う。

- GET  /api/match        試合開始前のマップ構成フォーマット
- GET  /api/match/{day}  各日開始時の試合情報フォーマット（day は 0 始まり）
- POST /api/agents       エージェント種別の回答（例: [0, 1, 0, 1]）を検証
- POST /api/actions      行動計画の回答を検証（?day=N、省略時 0）
- GET  /api/replay       ビジュアライザ用: 全日の情報+全チームの行動計画一式
- POST /api/new          新しいサンプル試合を生成（seed 等のパラメータ付き）
- GET  /debug            サーバー内部を確認するデバッグ GUI（Jinja2）

本番サーバー同様 HTTP/1.1 で応答する（uvicorn の h11 実装）。

起動:  python app.py   または   uvicorn app:app
"""

import time
from pathlib import Path
from threading import Lock

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from visualizer import debugview
from visualizer.hexgrid import apply_direction
from visualizer.livematch import LiveError, LiveMatch
from visualizer.simulator import generate_sample_match

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="ヘキサうどん モックサーバー & Visualizer",
    description="第37回全国高専プロコン 競技部門「ヘキサうどん」公式フォーマット準拠",
)
app.add_middleware(debugview.ApiLogMiddleware)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_lock = Lock()
_current: dict | None = None  # サンプルモードの試合データ（bundle）
_live: LiveMatch | None = None  # ライブ対戦モードの試合（非 None ならライブモード）


def _get_current() -> dict:
    global _current
    with _lock:
        if _live is not None:
            return _live.bundle()
        if _current is None:
            _current = generate_sample_match()
        return _current


@app.post("/api/new")
def new_match(
    seed: int | None = Query(None, description="乱数シード（省略時はランダム）"),
    teams: int = Query(3, ge=2, le=6, description="チーム数"),
    days: int = Query(5, ge=4, le=10, description="日数（ルール上 4〜10）"),
    agents: int = Query(4, ge=3, le=8, description="1チームのエージェント数（ルール上 3〜8）"),
    width: int = Query(12, ge=8, le=32, description="マップ横セル数（ルール上 8〜32）"),
    height: int = Query(10, ge=8, le=32, description="マップ縦セル数（ルール上 8〜32）"),
):
    """新しいサンプル試合を生成して現在の試合として保持する。

    ライブ対戦中に呼ぶとライブ試合は破棄され、サンプルモードに戻る。
    """
    global _current, _live
    try:
        bundle = generate_sample_match(
            seed=seed,
            num_teams=teams,
            num_days=days,
            num_agents=agents,
            width=width,
            height=height,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"試合生成に失敗しました: {exc}")
    with _lock:
        _current = bundle
        _live = None
    return {"ok": True, "seed": bundle["meta"]["seed"]}


# ----- ライブ対戦モード -----


@app.post("/api/live/new")
def live_new(
    seed: int | None = Query(None, description="乱数シード（省略時はランダム）"),
    teams: int = Query(3, ge=2, le=6, description="チーム数（チーム0=プレイヤー、他はAI）"),
    days: int = Query(5, ge=4, le=10, description="日数（ルール上 4〜10）"),
    agents: int = Query(4, ge=3, le=8, description="1チームのエージェント数（ルール上 3〜8）"),
    width: int = Query(12, ge=8, le=32, description="マップ横セル数（ルール上 8〜32）"),
    height: int = Query(10, ge=8, le=32, description="マップ縦セル数（ルール上 8〜32）"),
):
    """ライブ対戦を開始する。

    チーム0があなた（クライアント）、他チームは内蔵AI。以降の流れ:
    1. GET  /api/match       でマップ構成を取得
    2. POST /api/agents      でエージェント種別を提出（試合開始）
    3. GET  /api/match/{day} で当日の試合情報を取得
    4. POST /api/actions?day={day} で行動計画を提出 → その日が実行され翌日へ
    5. 3〜4 を最終日まで繰り返し。経過は GET /api/live・GET /api/replay で確認
    """
    global _live
    try:
        match = LiveMatch(
            seed=seed,
            num_teams=teams,
            num_days=days,
            num_agents=agents,
            width=width,
            height=height,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"試合生成に失敗しました: {exc}")
    with _lock:
        _live = match
    return {"ok": True, "seed": match.sim.seed, "status": match.status,
            "message": "POST /api/agents でエージェント種別を提出すると試合が始まります"}


@app.get("/api/live")
def live_status():
    """ライブ対戦の進行状況（ライブモードでなければ live: false）。"""
    with _lock:
        if _live is None:
            return {"live": False, "status": "sample"}
        return _live.summary()


@app.get("/api/match")
def match_config():
    """試合開始前のマップ構成フォーマット（公式）を返す。"""
    return _get_current()["match"]


@app.get("/api/match/{day}")
def match_day_info(day: int):
    """各日開始時の試合情報フォーマット（公式）を返す。day は 0 始まり。

    ライブ対戦中は「過去の日〜現在受付中の日」のみ取得できる。
    """
    with _lock:
        if _live is not None:
            try:
                return _live.day_info(day)
            except LiveError as exc:
                raise HTTPException(status_code=409, detail=exc.detail)
    bundle = _get_current()
    if not 0 <= day < len(bundle["days"]):
        raise HTTPException(status_code=404, detail=f"day は 0〜{len(bundle['days']) - 1}")
    return bundle["days"][day]["info"]


@app.get("/api/replay")
def replay_bundle():
    """ビジュアライザ用に試合データ一式（公式フォーマットの集合）を返す。"""
    return _get_current()


# ----- 回答の受付・検証（公式回答フォーマット） -----


def _validate_kinds(kinds: list, num_agents: int):
    if not isinstance(kinds, list) or len(kinds) != num_agents:
        raise HTTPException(
            status_code=422,
            detail=f"エージェント数と一致しません（{num_agents} 要素が必要）",
        )
    if not all(isinstance(k, int) and k in (0, 1) for k in kinds):
        raise HTTPException(status_code=422, detail="種別は 0（巡回車）か 1（補給車）のみ")


def _validate_plans(plans: list, match: dict, info: dict, day_steps: int):
    """行動計画の回答を公式仕様に沿って検証する（不正なら HTTPException 422）。

    - 全エージェント分の行動列があること
    - 値は -1 以下（待機）または 0〜5（方向）であること
    - 各エージェントの行動計画のステップ合計が当日のステップ数と一致すること
    - 池・マップ外への移動が含まれないこと
    ※ 燃料・補給のシミュレーションまでは行わない（フォーマット検証のみ）
    """
    num_agents = len(match["agents"])
    if not isinstance(plans, list) or len(plans) != num_agents:
        raise HTTPException(
            status_code=422,
            detail=f"エージェント数と一致しません（{num_agents} 本の行動列が必要）",
        )

    width = match["map"]["width"]
    height = match["map"]["height"]
    cells = match["map"]["cells"]
    # 状態別の道路ステップ数はその日の交通量に依存するため、
    # 当日の試合情報の traffics を参照する
    traffics = {t["pos"]: t["status"] for t in info["traffics"]}
    step_cost = {0: 2, 1: None, 2: 3, 3: None}  # 地形コード → ステップ（道路は状態依存）
    road_step = {0: 1, 1: 2, 2: 4}
    positions = [a["pos"] for a in info["agents"]]

    for ai, plan in enumerate(plans):
        if not isinstance(plan, list) or not plan:
            raise HTTPException(status_code=422, detail=f"エージェント{ai}: 行動列が空です")
        cell = positions[ai]
        total = 0
        for value in plan:
            if not isinstance(value, int):
                raise HTTPException(status_code=422, detail=f"エージェント{ai}: 整数以外の値")
            if value <= -1:
                total += -value
                continue
            if value > 5:
                raise HTTPException(
                    status_code=422, detail=f"エージェント{ai}: 方向は 0〜5（{value} は不正）"
                )
            r, c = divmod(cell, width)
            code = cells[r][c]
            total += road_step[traffics.get(cell, 0)] if code == 1 else step_cost[code]
            nxt = apply_direction(cell, value, width, height)
            if nxt is None:
                raise HTTPException(status_code=422, detail=f"エージェント{ai}: マップ外への移動")
            nr, nc = divmod(nxt, width)
            if cells[nr][nc] == 3:
                raise HTTPException(status_code=422, detail=f"エージェント{ai}: 池への移動")
            cell = nxt
        if total != day_steps:
            raise HTTPException(
                status_code=422,
                detail=f"エージェント{ai}: ステップ合計 {total} が当日のステップ数 {day_steps} と不一致",
            )


@app.post("/api/agents")
def submit_agent_kinds(kinds: list = Body(...)):
    """エージェント種別の回答。例: [0, 1, 0, 1]

    サンプルモードでは検証のみ。ライブ対戦中はプレイヤー（チーム0）の
    種別として確定し、試合が始まる。
    """
    with _lock:
        if _live is not None:
            _validate_kinds(kinds, len(_live.sim.map["starts"]))
            try:
                _live.submit_kinds(kinds)
            except LiveError as exc:
                raise HTTPException(status_code=409, detail=exc.detail)
            return {"accepted": True, "status": _live.status,
                    "message": "試合開始。GET /api/match/0 で初日の情報を取得してください"}
    bundle = _get_current()
    _validate_kinds(kinds, len(bundle["match"]["agents"]))
    return {"accepted": True}


@app.post("/api/actions")
def submit_actions(
    plans: list = Body(...),
    day: int = Query(0, ge=0, description="対象の日（0 始まり）"),
):
    """行動計画の回答。

    サンプルモードでは検証のみ。ライブ対戦中はプレイヤー（チーム0）の
    当日の行動として実行され、AIチームと共にその日が進行して翌日へ移る。
    """
    with _lock:
        if _live is not None:
            if _live.status == "waiting_agents":
                raise HTTPException(
                    status_code=409,
                    detail="先に POST /api/agents でエージェント種別を提出してください",
                )
            if _live.status == "finished":
                raise HTTPException(
                    status_code=409,
                    detail="試合は終了しています（POST /api/live/new で新しい試合を開始）",
                )
            if day != _live.current_day:
                raise HTTPException(
                    status_code=409, detail=f"現在回答を受付中の日は {_live.current_day} です"
                )
            match = _live.bundle()["match"]
            _validate_plans(plans, match, _live.pending_info, match["daySteps"][day])
            try:
                _live.submit_actions(day, plans)
            except LiveError as exc:
                raise HTTPException(status_code=409, detail=exc.detail)
            return {
                "accepted": True,
                "day": day,
                "finished": _live.finished,
                "standings": _live.standings(),
            }

    bundle = _get_current()
    match = bundle["match"]
    if not 0 <= day < len(match["daySteps"]):
        raise HTTPException(status_code=404, detail=f"day は 0〜{len(match['daySteps']) - 1}")
    _validate_plans(plans, match, bundle["days"][day]["info"], match["daySteps"][day])
    return {"accepted": True}


# ----- デバッグ GUI（Jinja2） -----


def _fmt_epoch(sec: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sec))


def _team_agents_for_day(bundle: dict, day: int) -> list[list[dict]]:
    """各日開始時の試合情報から全チームのエージェント一覧を組み立てる。"""
    info = bundle["days"][day]["info"]
    by_id = {o["id"]: o["agents"] for o in info["others"]}
    return [info["agents"]] + [
        by_id.get(tid, []) for tid in range(1, bundle["match"]["players"])
    ]


@app.get("/debug", response_class=HTMLResponse, include_in_schema=False)
def debug_index(request: Request):
    """試合サマリ・マップ・スポット・期待スコアの一覧。"""
    with _lock:
        live = _live.summary() if _live is not None else None
    bundle = _get_current()
    match = bundle["match"]
    meta = bundle["meta"]
    series_names = meta["seriesNames"]

    spot_rows = []
    for brand, name in enumerate(series_names):
        positions = [s["pos"] for s in match["spots"] if s["brand"] == brand]
        spot_rows.append(
            {
                "brand": brand,
                "name": name,
                "color": debugview.series_color(brand),
                "count": len(positions),
                "positions": positions,
            }
        )

    per_team = meta["expected"]["perTeam"]
    ranking_rows = [
        {**per_team[tid], "color": debugview.team_color(tid)}
        for tid in meta["expected"]["ranking"]
    ]

    svg = debugview.render_map_svg(
        match,
        series_names=series_names,
        team_names=meta["teamNames"],
        # ライブ試合でまだ1日も完了していない場合は初期配置のみ表示
        team_agents=_team_agents_for_day(bundle, 0) if bundle["days"] else None,
    )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "nav": "index",
            "num_days": len(bundle["days"]),
            "match": match,
            "meta": meta,
            "starts_at_str": _fmt_epoch(match["startsAt"]),
            "spot_rows": spot_rows,
            "ranking_rows": ranking_rows,
            "svg": svg,
            "live": live,
        },
    )


@app.get("/debug/day/{day}", response_class=HTMLResponse, include_in_schema=False)
def debug_day(request: Request, day: int):
    """指定日の試合情報・道路状態・各チームの行動計画とそのトレース。"""
    bundle = _get_current()
    if not 0 <= day < len(bundle["days"]):
        raise HTTPException(status_code=404, detail=f"day は 0〜{len(bundle['days']) - 1}")
    match = bundle["match"]
    meta = bundle["meta"]
    info = bundle["days"][day]["info"]
    plans = bundle["days"][day]["plans"]
    width = match["map"]["width"]
    traffic_map = {t["pos"]: t["status"] for t in info["traffics"]}
    team_agents = _team_agents_for_day(bundle, day)

    teams = []
    for tid, agents in enumerate(team_agents):
        rows = []
        for ai, agent in enumerate(agents):
            plan = plans[tid][ai]
            row, col = divmod(agent["pos"], width)
            rows.append(
                {
                    **agent,
                    "row": row,
                    "col": col,
                    "kind_name": debugview.KIND_NAMES.get(agent["kind"], "?"),
                    "plan_text": " ".join(str(v) for v in plan),
                    "trace": debugview.trace_plan(plan, agent["pos"], match, traffic_map),
                }
            )
        teams.append(
            {
                "id": tid,
                "name": meta["teamNames"][tid],
                "color": debugview.team_color(tid),
                "agents": rows,
            }
        )

    svg = debugview.render_map_svg(
        match,
        series_names=meta["seriesNames"],
        team_names=meta["teamNames"],
        team_agents=team_agents,
        traffic_map=traffic_map,
    )
    return templates.TemplateResponse(
        request=request,
        name="day.html",
        context={
            "nav": "day",
            "nav_day": day,
            "num_days": len(bundle["days"]),
            "day": day,
            "info": info,
            "ends_at_str": _fmt_epoch(info["endsAt"]),
            "day_steps": match["daySteps"][day],
            "day_seconds": match["daySeconds"][day],
            "fuel_limit": match["fuelLimits"],
            "road_total": len(info["traffics"]),
            "busy_cells": [p for p, s in traffic_map.items() if s == 1],
            "jammed_cells": [p for p, s in traffic_map.items() if s == 2],
            "teams": teams,
            "svg": svg,
        },
    )


@app.get("/debug/requests", response_class=HTMLResponse, include_in_schema=False)
def debug_requests(request: Request):
    """直近の /api/* リクエスト・レスポンスの記録。"""
    bundle = _get_current()
    entries = [
        {**e, "time_str": _fmt_epoch(int(e["time"])) + f".{int(e['time'] * 10) % 10}"}
        for e in debugview.REQUEST_LOG
    ]
    return templates.TemplateResponse(
        request=request,
        name="requests.html",
        context={
            "nav": "requests",
            "num_days": len(bundle["days"]),
            "entries": entries,
        },
    )


# フロントエンド（素の HTML/JS/CSS）。API ルートより後に mount する。
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    # 本番競技サーバーは HTTP/1.1 のみ対応 → h11 を明示
    uvicorn.run("app:app", host="127.0.0.1", port=8000, http="h11", reload=True)
