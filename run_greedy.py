#!/usr/bin/env python3
"""簡易サーバーに接続して、貪欲法で全日自動対戦。"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from algorithm.template import build_plans


def api(server: str, method: str, path: str, body=None):
    req = urllib.request.Request(
        server + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as exc:
        print(f"❌ {method} {path} -> HTTP {exc.code}", file=sys.stderr)
        print(exc.read().decode(), file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.URLError as exc:
        print(f"❌ サーバーに接続できません ({server}): {exc.reason}", file=sys.stderr)
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description="簡易サーバーの試合に貪欲法で自動対戦")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--day", type=int, help="特定の日だけ実行（省略時は全日）")
    args = ap.parse_args()

    print(f"🔗 接続: {args.server}")

    # マップ取得
    match = api(args.server, "GET", "/api/match")
    num_agents = len(match["agents"])
    num_days = len(match["daySteps"])
    print(f"📋 マップ: {match['map']['width']}×{match['map']['height']}, エージェント数: {num_agents}, 日数: {num_days}")

    # 種別: 最後の1台を補給車、残りを巡回車に
    kinds = [0] * (num_agents - 1) + [1]
    try:
        api(args.server, "POST", "/api/agents", kinds)
        print(f"✅ 種別提出: {kinds} (0=巡回車 1=補給車)")
    except SystemExit:
        print("⚠️  種別提出失敗 (既に提出済み?)")

    # 対戦実行
    days_to_run = [args.day] if args.day is not None else range(num_days)
    for day in days_to_run:
        if day >= num_days:
            print(f"❌ day={day} は範囲外 (0-{num_days-1})")
            raise SystemExit(1)

        info = api(args.server, "GET", f"/api/match/{day}")
        plans = build_plans(match, info, kinds)
        print(f"📍 Day {day}: 行動計画を生成・送信...")

        res = api(args.server, "POST", f"/api/actions?day={day}", plans)

        if "standings" in res:
            standings = " | ".join(
                f"{s['rank']}位 {s['name']} (種類数: {s['seriesCount']}, 玉数: {s['totalUdon']})"
                for s in res["standings"]
            )
            print(f"   成績: {standings}")

        if res.get("finished"):
            print("🎉 試合終了！")
            break

    print("✅ 完了")


if __name__ == "__main__":
    main()
