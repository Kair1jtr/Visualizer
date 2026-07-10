// カラー定義（JS 側で SVG 属性やレジェンドに使う分）。
// CSS 変数側 (style.css) と同じ値。カテゴリカル8色は CVD 検証済みの
// 固定順パレット。チームは先頭から、うどん系列は末尾から取ることで
// 少数同士なら色が衝突しない。

const CATEGORICAL_LIGHT = ['#2a78d6', '#1baf7a', '#eda100', '#008300', '#4a3aa7', '#e34948', '#e87ba4', '#eb6834'];
const CATEGORICAL_DARK = ['#3987e5', '#199e70', '#c98500', '#008300', '#9085e9', '#e66767', '#d55181', '#d95926'];

function isDark() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export function teamColor(i) {
  const p = isDark() ? CATEGORICAL_DARK : CATEGORICAL_LIGHT;
  return p[i % p.length];
}

export function seriesColor(i) {
  const p = isDark() ? CATEGORICAL_DARK : CATEGORICAL_LIGHT;
  return p[(p.length - 1 - i % p.length)];
}

export const ROAD_STATE_COLOR = {
  congested: '#fab219', // status warning
  jammed: '#d03b3b', // status critical
};

export const ROAD_STATE_LABEL = {
  smooth: '順調',
  congested: '混雑',
  jammed: '渋滞',
};

export const TERRAIN_LABEL = {
  plain: '平地',
  mountain: '山地',
  pond: '池',
  road: '道路',
};

export const AGENT_TYPE_LABEL = {
  patrol: '巡回車',
  supply: '補給車',
};
