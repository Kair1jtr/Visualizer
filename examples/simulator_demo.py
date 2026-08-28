#!/usr/bin/env python3
"""シミュレーターの動作デモ。

  1. 〔補足〕Q&A補足資料の状態遷移例を再生し、追跡ログを表示する
  2. 「渋滞する道路を使う戦略」と「道路を避ける戦略」を数日間比較する

実行:
    python examples/simulator_demo.py            # 両方
    python examples/simulator_demo.py official   # 1 のみ
    python examples/simulator_demo.py traffic    # 2 のみ
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import compare, engine, scenarios, tracing  # noqa: E402
from simulator.state import SpotDef  # noqa: E402
from simulator.terrain import Terrain  # noqa: E402

PLAIN, ROAD = int(Terrain.PLAIN), int(Terrain.ROAD)


# ---------------------------------------------------------------------------
# 1. 公式例の再生
# ---------------------------------------------------------------------------


def demo_official() -> None:
    print("=" * 78)
    print("1. 〔補足〕Q&A補足資料（行動詳細）の状態遷移例を再生する")
    print("=" * 78)

    state, plans = scenarios.official_supplement_scenario()
    tracer = tracing.Tracer()

    engine.begin_day(state, tracer)
    scenarios.apply_supplement_road_status(state)
    engine.set_plans(state, {0: plans})
    engine.simulate_day_steps(state, tracer, strict=True)

    print("\n--- 追跡ログ（うどん獲得・燃料補給のみ抜粋） ---")
    for e in tracer.events:
        if e.phase.startswith(("reflection.3", "reflection.4")):
            label = scenarios.SUPPLEMENT_LABELS[e.agent_id]
            print(f"  step{e.step}  {label:<8} {e.message}")

    print("\n--- 巡回車A の全イベント（燃料消費タイミングの確認） ---")
    print(tracer.for_agent(0, 0))

    print("\n--- 滞在数の検証 ---")
    actual = dict(sorted(state.traffic.stay_today.items()))
    expected = scenarios.SUPPLEMENT_EXPECTED_STAY
    print(f"  実測: {actual}")
    print(f"  期待: {expected}   （〔補足〕の滞在数行）")
    print(f"  一致: {actual == expected}")

    print("\n--- 最終状態 ---")
    print(tracing.snapshot(state))


# ---------------------------------------------------------------------------
# 2. 交通量を踏まえた戦略比較
# ---------------------------------------------------------------------------

# 1行3列: セル0=平地(出発), セル1=道路, セル2=平地(スポット)
#
#   [0]平地(出発) --- [1]道路 --- [2]平地◎スポット
#
# 「どこに居座るか」で道路の交通量が変わり、翌日以降の道路状態が変わる。
# またスポット上に留まると、翌日の1ステップ目に再度うどんを獲得できる〔Q7〕。
DEMO_CELLS = [[PLAIN, ROAD, PLAIN]]
DEMO_SPOTS = [SpotDef(pos=2, brand=0, stocks=99)]
DEMO_DAY_STEPS = (8, 8, 8, 8)
DEMO_BUSY, DEMO_JAMMED = 3, 5


def make_demo_state():
    return scenarios.minimal_scenario(
        cells=DEMO_CELLS,
        spots=DEMO_SPOTS,
        starts=[0],
        kinds_by_team=[[0]],
        day_steps=DEMO_DAY_STEPS,
        fuel_limits=999,  # 燃料を論点から外す
        busy_threshold=DEMO_BUSY,
        jammed_threshold=DEMO_JAMMED,
    )


def park_at(goal: int) -> compare.Strategy:
    """指定セルへ向かい、着いたらその日はそこで待機し続ける戦略。"""

    def strategy(state, team_id):
        team = next(t for t in state.teams if t.team_id == team_id)
        agent = team.agents[0]
        directions = compare.straight_line_directions(state, agent.pos, goal)
        return [compare.build_plan(state, agent, prefix=directions)]

    return strategy


def demo_traffic() -> None:
    print("\n" + "=" * 78)
    print("2. 「道路に居座る」vs「道路を通り抜けてスポットに居座る」を4日間比較する")
    print("=" * 78)
    print(
        "\nマップ: [0]平地(出発) --- [1]道路 --- [2]平地◎スポット\n"
        f"1日{DEMO_DAY_STEPS[0]}ステップ × {len(DEMO_DAY_STEPS)}日 / "
        f"混雑基準{DEMO_BUSY}・渋滞基準{DEMO_JAMMED}・1チーム\n"
        "\n見どころ:\n"
        "  ・道路(セル1)に居座ると滞在数が積み上がり、翌日以降その道路が渋滞する\n"
        "  ・通り抜けるだけなら滞在数はほとんど増えず、道路は順調のまま\n"
        "  ・スポット上に留まると翌日以降も1ステップ目に獲得できる〔Q7〕\n"
    )

    results = compare.compare(
        make_demo_state,
        {
            "道路(セル1)に居座る": {0: park_at(1)},
            "スポット(セル2)に居座る": {0: park_at(2)},
        },
    )
    print(compare.comparison_report(results))


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "official"):
        demo_official()
    if which in ("all", "traffic"):
        demo_traffic()


if __name__ == "__main__":
    main()
