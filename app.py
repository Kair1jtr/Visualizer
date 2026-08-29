"""ヘキサうどん Visualizer — FastAPI サーバー。

観戦ビューを2系統ぶん配信する。どちらも同じ形の JSON を返すので、
ブラウザ側は同じ描画コード（static/js/matchview.js）で表示できる。

- POST /api/real/start   公式配布の簡易サーバー(procon-server)を起動して観戦開始
- POST /api/real/stop    procon-server を停止
- GET  /api/real/status  観戦データ（設定・日ごとのスナップショット・推定軌跡）
- GET  /api/sim/strategies  選べる戦略（パラメータのスキーマ込み）とプレイヤー一覧
- POST /api/sim/start    公式ルール忠実シミュレーター(simulator/)で1試合を実行
- POST /api/sim/stop     シミュレーション結果を破棄
- GET  /api/sim/status   観戦データ（/api/real/status と同じ形。軌跡は実測）

本番サーバー同様 HTTP/1.1 で応答する（uvicorn の h11 実装）。

起動:  python app.py   または   uvicorn app:app
"""

import json
from pathlib import Path
from threading import Lock

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from visualizer.procon_process import DEFAULT_CONFIG, ProconProcess, ProconProcessError
from visualizer.sim_spectator import DEFAULT_CONFIG as SIM_DEFAULT_CONFIG
from visualizer.sim_spectator import (
    SimSpectator,
    SimSpectatorError,
    available_strategies,
    load_match_config,
    run_simulation,
)
from visualizer.spectator import MatchSpectator

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="ヘキサうどん 観戦ビュー",
    description="第37回全国高専プロコン 競技部門「ヘキサうどん」公式フォーマット準拠",
)

_lock = Lock()

# ----- 本番用: 公式配布の簡易サーバー(procon-server)の観戦 -----
_real_process = ProconProcess()
_real_spectator: MatchSpectator | None = None

# ----- 公式ルール忠実シミュレーター(simulator/)の観戦 -----
_sim_spectator: SimSpectator | None = None
_sim_runs = 0  # 実行ごとに増える。盤面を作り直すべきかの判定に使う


# ---------------------------------------------------------------------------
# 本番用: 公式配布の簡易サーバー(procon-server)の起動・観戦
# ---------------------------------------------------------------------------


def _load_team_names(config_path: Path) -> list[str]:
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        return [t.get("name") or "" for t in data.get("teams", [])]
    except Exception:
        return []


@app.post("/api/real/start")
def real_start(
    config: str = Query(
        default=str(DEFAULT_CONFIG),
        description="procon-server に渡す試合設定JSONのパス（省略時は配布サンプル）",
    ),
):
    """公式配布の簡易サーバー(procon-server)を起動し、観戦を開始する。

    試合の開始・終了はこのプロセスの起動・停止に対応する。procon-server が
    内部で締切・試合開始・各日の進行をすべて管理するため、このAPIは
    プロセスを立ち上げるだけでよい。
    """
    global _real_spectator
    with _lock:
        try:
            config_path = Path(config)
            base_url = _real_process.start(config_path=config_path)
        except ProconProcessError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        # 観戦には1チーム分のトークンで足りる（GET / の others[] に全チームが載るため）
        try:
            teams = json.loads(config_path.read_text(encoding="utf-8"))["teams"]
            token = teams[0]["token"]
        except Exception as exc:
            _real_process.stop()
            raise HTTPException(
                status_code=422, detail=f"設定JSONからトークンを読み取れません: {exc}"
            )
        _real_spectator = MatchSpectator(
            base_url, token, team_names=_load_team_names(config_path)
        )
    return {"ok": True, "baseUrl": base_url}


@app.post("/api/real/stop")
def real_stop():
    """procon-server を停止する。"""
    with _lock:
        _real_process.stop()
    return {"ok": True}


@app.get("/api/real/status")
def real_status():
    """観戦データ（設定・日ごとのスナップショット・推定軌跡）を返す。

    procon-server が起動していない場合は running: false のみを返す。
    呼び出しごとに procon-server から最新状態を1回取得する（ポーリング用）。
    """
    with _lock:
        if _real_spectator is None:
            return {"running": False, "started": False}
        _real_spectator.poll()
        return {
            "started": True,
            "processAlive": _real_process.running,
            **_real_spectator.summary(),
        }


# ---------------------------------------------------------------------------
# 公式ルール忠実シミュレーター(simulator/)の観戦
#
# 実時間の締切が無いため試合は一瞬で終わる。/api/sim/start で全日程を走らせ、
# /api/sim/status がその結果を返す（形は /api/real/status と同じ）。
# ---------------------------------------------------------------------------


@app.get("/api/sim/strategies")
def sim_strategies(
    config: str = Query(
        default=str(SIM_DEFAULT_CONFIG),
        description="プレイヤー一覧を読む試合設定JSONのパス",
    ),
):
    """選べる戦略（パラメータのスキーマ込み）と、設定JSON上のプレイヤー一覧を返す。

    UI はこれを使って「プレイヤーごとの戦略設定ダイアログ」を組み立てる。
    戦略を増やすときは `simulator/strategy.py` でクラスを書いて `@register` を
    付けるだけでよく、ここも UI も触らなくてよい。
    """
    strategies = available_strategies()
    try:
        state, names = load_match_config(Path(config))
    except SimSpectatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "strategies": strategies,
        "default": strategies[0]["name"],
        "players": [
            {"id": team.team_id, "name": names[i] if i < len(names) else f"Player {i}"}
            for i, team in enumerate(state.teams)
        ],
    }


@app.post("/api/sim/start")
def sim_start(
    config: str = Query(
        default=str(SIM_DEFAULT_CONFIG),
        description="試合設定JSONのパス（procon-server に渡すのと同じ形式）",
    ),
    strategy: str | None = Query(
        default=None,
        description=(
            "戦略の簡易指定。プレイヤーごとに変える場合はカンマ区切りで並べる"
            "（例: greedy,stay）か、番号付きで指定する（例: greedy,2:stay）。"
            "パラメータまで指定したい場合はボディの players を使う"
        ),
    ),
    body: dict | None = Body(
        default=None,
        description=(
            "プレイヤーごとの詳細設定。"
            '{"players": [{"strategy": "greedy", "params": {"repeat_value": 0}}, ...]}'
        ),
    ),
):
    """シミュレーターで1試合を最後まで実行し、観戦データを作る。

    公式簡易サーバーと同じ設定JSONを読むので、同じ試合を
    「実サーバーで動かした結果」と「シミュレーターで動かした結果」で見比べられる。

    戦略はプレイヤーごとに割り当てられる。パラメータまで指定する場合は
    ボディに `players` を渡す（`GET /api/sim/strategies` のスキーマに従う）。
    """
    global _sim_spectator, _sim_runs
    players = None
    if isinstance(body, dict):
        if body.get("config"):
            config = str(body["config"])
        if body.get("strategy"):
            strategy = str(body["strategy"])
        raw = body.get("players")
        if raw is not None:
            if not isinstance(raw, list):
                raise HTTPException(
                    status_code=422, detail="players は配列である必要があります"
                )
            players = raw
    with _lock:
        _sim_runs += 1
        try:
            _sim_spectator = run_simulation(
                Path(config),
                strategy=strategy,
                players=players,
                run_key=_sim_runs,
            )
        except SimSpectatorError as exc:
            _sim_spectator = None
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "ok": True,
            "days": len(_sim_spectator.days),
            "assignments": [s.settings() for s in _sim_spectator.setups],
        }


@app.post("/api/sim/stop")
def sim_stop():
    """シミュレーション結果を破棄する。"""
    global _sim_spectator
    with _lock:
        _sim_spectator = None
    return {"ok": True}


@app.get("/api/sim/status")
def sim_status():
    """観戦データを返す。`/api/real/status` と同じ形（軌跡は推定ではなく実測）。"""
    with _lock:
        if _sim_spectator is None:
            return {"running": False, "started": False}
        return _sim_spectator.summary()


# フロントエンド（素の HTML/JS/CSS）。API ルートより後に mount する。
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    # 本番競技サーバーは HTTP/1.1 のみ対応 → h11 を明示
    uvicorn.run("app:app", host="127.0.0.1", port=8000, http="h11", reload=True)
