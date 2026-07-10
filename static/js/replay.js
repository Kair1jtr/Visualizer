// 公式フォーマットの試合データ一式（マップ構成 + 各日試合情報 + 行動計画）を
// クライアント側で再生し、1ステップ刻みのフレーム列とイベント列を作る。
//
// サーバー(Python)のシミュレーションと同じ順序・同じ規則で実行する:
//   各ステップ: 行動発行 → 移動進行/到着(うどん獲得) → 補給 → フレーム記録

import { applyDirection } from './hex.js';

export const TERRAIN_BY_CODE = ['plain', 'road', 'mountain', 'pond'];
export const ROAD_STATE_BY_CODE = ['smooth', 'congested', 'jammed'];

const STEP_COST = { plain: 2, mountain: 3, road_smooth: 1, road_congested: 2, road_jammed: 4 };
const FUEL_COST = { plain: 1, mountain: 2, road_smooth: 2, road_congested: 2, road_jammed: 2 };

function terrainKeyOf(terrain, roadStates, cell) {
  const t = terrain[cell];
  if (t === 'road') return `road_${roadStates.get(cell) ?? 'smooth'}`;
  return t;
}

// 行動計画を1コマンドずつ読み出すカーソル。-N の待機は N 回の wait に展開する。
class PlanCursor {
  constructor(plan) {
    this.plan = plan;
    this.index = 0;
    this.waitLeft = 0;
  }
  next() {
    if (this.waitLeft > 0) {
      this.waitLeft -= 1;
      return { type: 'wait' };
    }
    if (this.index >= this.plan.length) return null;
    const value = this.plan[this.index];
    this.index += 1;
    if (value <= -1) {
      this.waitLeft = -value - 1;
      return { type: 'wait' };
    }
    return { type: 'move', dir: value };
  }
}

export function buildReplay(bundle) {
  const match = bundle.match;
  const width = match.map.width;
  const height = match.map.height;
  const terrain = match.map.cells.flat().map((c) => TERRAIN_BY_CODE[c]);
  const spots = new Map(match.spots.map((s) => [s.pos, s]));
  const numTeams = bundle.kinds.length;
  const fuelLimits = match.fuelLimits;

  // チーム×エージェントの状態（試合を通して持ち越す）
  const teams = bundle.kinds.map((kinds) => ({
    agents: kinds.map((k, i) => ({
      kind: k === 0 ? 'patrol' : 'supply',
      cell: match.agents[i],
      fuel: k === 0 ? fuelLimits : null,
      moveRemaining: 0,
      moveTarget: null,
    })),
    seriesOverall: new Set(),
    dailySeriesCum: 0,
    total: 0,
  }));

  const snapshot = () =>
    teams.map((t) => t.agents.map((a) => ({ cell: a.cell, fuel: a.fuel })));

  const scoreSnapshot = () =>
    teams.map((t) => ({
      seriesCount: t.seriesOverall.size,
      dailySeriesCum: t.dailySeriesCum,
      total: t.total,
    }));

  const days = bundle.days.map((dayData, dayIndex) => {
    const steps = match.daySteps[dayIndex];
    const roadStates = new Map(
      dayData.info.traffics.map((t) => [t.pos, ROAD_STATE_BY_CODE[t.status]])
    );

    // 日開始時の突き合わせ（サーバー提供の試合情報と再生状態の相互検証）
    const infoTeams = [dayData.info.agents, ...dayData.info.others.map((o) => o.agents)];
    infoTeams.forEach((agents, ti) => {
      agents.forEach((a, ai) => {
        const mine = teams[ti].agents[ai];
        if (a.pos !== mine.cell || (mine.fuel !== null && a.fuel !== mine.fuel)) {
          console.warn(
            `再生結果と試合情報が不一致: day${dayIndex} team${ti} agent${ai}`,
            { info: a, replay: { pos: mine.cell, fuel: mine.fuel } }
          );
        }
      });
    });

    // 日初期化
    const stock = teams.map(() => {
      const m = new Map();
      for (const [pos, s] of spots) m.set(pos, s.stocks);
      return m;
    });
    const acquiredToday = teams.map((t) => t.agents.map(() => new Set()));
    const seriesToday = teams.map(() => new Set());
    const cursors = dayData.plans.map((teamPlans) =>
      teamPlans.map((plan) => new PlanCursor(plan))
    );

    const frames = [snapshot()];
    const scores = [scoreSnapshot()];
    const events = [];

    for (let k = 0; k < steps; k++) {
      const cellsAtStart = teams.map((t) => t.agents.map((a) => a.cell));

      // 行動発行
      teams.forEach((team, ti) => {
        team.agents.forEach((agent, ai) => {
          if (agent.moveRemaining > 0) return;
          const cmd = cursors[ti][ai].next();
          if (!cmd || cmd.type === 'wait') return;
          const key = terrainKeyOf(terrain, roadStates, agent.cell);
          const target = applyDirection(agent.cell, cmd.dir, width, height);
          if (target === null || terrain[target] === 'pond') {
            console.warn(`不正な移動を待機に読み替え: day${dayIndex} team${ti} agent${ai}`);
            return;
          }
          if (agent.kind === 'patrol') {
            if (agent.fuel < FUEL_COST[key]) {
              console.warn(`燃料不足の移動を待機に読み替え: day${dayIndex} team${ti} agent${ai}`);
              return;
            }
            agent.fuel -= FUEL_COST[key];
          }
          agent.moveRemaining = STEP_COST[key];
          agent.moveTarget = target;
        });
      });

      // 移動進行・到着処理（うどん獲得）
      teams.forEach((team, ti) => {
        team.agents.forEach((agent, ai) => {
          if (agent.moveRemaining <= 0) return;
          agent.moveRemaining -= 1;
          if (agent.moveRemaining > 0) return;
          agent.cell = agent.moveTarget;
          agent.moveTarget = null;
          if (agent.kind !== 'patrol') return;
          const spot = spots.get(agent.cell);
          if (
            spot &&
            stock[ti].get(agent.cell) > 0 &&
            !acquiredToday[ti][ai].has(agent.cell)
          ) {
            stock[ti].set(agent.cell, stock[ti].get(agent.cell) - 1);
            acquiredToday[ti][ai].add(agent.cell);
            team.total += 1;
            const isNewOverall = !team.seriesOverall.has(spot.brand);
            const isNewToday = !seriesToday[ti].has(spot.brand);
            team.seriesOverall.add(spot.brand);
            if (isNewToday) {
              seriesToday[ti].add(spot.brand);
              team.dailySeriesCum += 1;
            }
            events.push({
              frame: k + 1,
              type: 'acquire',
              team: ti,
              agent: ai,
              cell: agent.cell,
              brand: spot.brand,
              newOverall: isNewOverall,
            });
          }
        });
      });

      // 補給（1ステップの間 同セルに居続けた巡回車×補給車）
      teams.forEach((team, ti) => {
        const supplies = team.agents
          .map((a, i) => [i, a])
          .filter(([, a]) => a.kind === 'supply');
        team.agents.forEach((agent, ai) => {
          if (agent.kind !== 'patrol' || agent.fuel >= fuelLimits) return;
          for (const [si, supply] of supplies) {
            const stayedTogether =
              supply.cell === agent.cell &&
              cellsAtStart[ti][ai] === agent.cell &&
              cellsAtStart[ti][si] === supply.cell;
            if (stayedTogether) {
              agent.fuel = fuelLimits;
              events.push({ frame: k + 1, type: 'refuel', team: ti, agent: ai, cell: agent.cell });
              break;
            }
          }
        });
      });

      frames.push(snapshot());
      scores.push(scoreSnapshot());
    }

    return { steps, roadStates, frames, scores, events };
  });

  // 最終結果（勝敗判定の優先順位: 種類数 → 累積種類数 → 玉数）
  const finalScores = scoreSnapshot();
  const ranking = [...Array(numTeams).keys()].sort((a, b) => {
    const A = finalScores[a];
    const B = finalScores[b];
    return (
      B.seriesCount - A.seriesCount ||
      B.dailySeriesCum - A.dailySeriesCum ||
      B.total - A.total ||
      a - b
    );
  });

  // サーバー側シミュレーション結果との突き合わせ
  const expected = bundle.meta?.expected;
  if (expected) {
    expected.perTeam.forEach((e, ti) => {
      const got = finalScores[ti];
      if (
        e.seriesCount !== got.seriesCount ||
        e.dailySeriesCum !== got.dailySeriesCum ||
        e.totalUdon !== got.total
      ) {
        console.warn(`最終スコアがサーバー計算と不一致: team${ti}`, { expected: e, got });
      }
    });
  }

  return {
    width,
    height,
    terrain,
    spots,
    fuelLimits,
    numTeams,
    teamNames:
      bundle.meta?.teamNames ?? [...Array(numTeams).keys()].map((i) => `チーム${i}`),
    seriesNames: bundle.meta?.seriesNames ?? [],
    agentKinds: bundle.kinds.map((ks) => ks.map((k) => (k === 0 ? 'patrol' : 'supply'))),
    days,
    finalScores,
    ranking,
  };
}
