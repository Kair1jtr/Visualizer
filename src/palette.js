// Single source of truth for all colors used by the visualizer.
// Categorical hues come from the validated 8-slot palette (see dataviz skill,
// references/palette.md) — light-mode worst adjacent CVD ΔE 24.2, dark 10.3.
// Teams draw from the front of the fixed order, series from the back, so the
// two roles don't share a hue at the small counts this app targets
// (<=8 teams / <=8 series; collision only possible if both maxed out at once).

const CATEGORICAL = [
  { light: '#2a78d6', dark: '#3987e5' }, // 1 blue
  { light: '#1baf7a', dark: '#199e70' }, // 2 aqua
  { light: '#eda100', dark: '#c98500' }, // 3 yellow
  { light: '#008300', dark: '#008300' }, // 4 green
  { light: '#4a3aa7', dark: '#9085e9' }, // 5 violet
  { light: '#e34948', dark: '#e66767' }, // 6 red
  { light: '#e87ba4', dark: '#d55181' }, // 7 magenta
  { light: '#eb6834', dark: '#d95926' }, // 8 orange
];

export function teamColor(index) {
  return CATEGORICAL[index % CATEGORICAL.length];
}

export function seriesColor(index) {
  const reversed = [...CATEGORICAL].reverse();
  return reversed[index % reversed.length];
}

// Status palette (fixed, never themed) — used for road congestion badges,
// always paired with an icon/label, never color alone.
export const STATUS = {
  good: { light: '#0ca30c', dark: '#0ca30c' },
  warning: { light: '#fab219', dark: '#fab219' },
  serious: { light: '#ec835a', dark: '#ec835a' },
  critical: { light: '#d03b3b', dark: '#d03b3b' },
};

export const ROAD_STATE_STATUS = {
  smooth: null, // no badge — default state
  congested: 'warning',
  jammed: 'critical',
};

// Terrain fills are a separate, board-like palette (not the categorical set)
// so they never compete visually with team/series hues. Each terrain also
// gets a distinct SVG pattern (see render.js) as a non-color channel.
export const TERRAIN_COLOR = {
  plain: { light: '#c9e4b8', dark: '#33472e' },
  mountain: { light: '#d9b98a', dark: '#5a4630' },
  pond: { light: '#a9d6e5', dark: '#1f3d4d' },
  road: { light: '#c9c7c0', dark: '#3a3a37' },
};

export const CHROME = {
  surfaceLight: '#fcfcfb',
  surfaceDark: '#1a1a19',
  pageLight: '#f9f9f7',
  pageDark: '#0d0d0d',
  textPrimaryLight: '#0b0b0b',
  textPrimaryDark: '#ffffff',
  textSecondaryLight: '#52514e',
  textSecondaryDark: '#c3c2b7',
  mutedLight: '#898781',
  mutedDark: '#898781',
  gridLight: '#e1e0d9',
  gridDark: '#2c2c2a',
  baselineLight: '#c3c2b7',
  baselineDark: '#383835',
  borderLight: 'rgba(11,11,11,0.10)',
  borderDark: 'rgba(255,255,255,0.10)',
};

// Injects every palette value as CSS custom properties on :root, once, so
// style.css and render.js (which needs raw hex for SVG attributes) both read
// from this single definition.
export function installPaletteCssVars() {
  const root = document.documentElement;
  const set = (name, value) => root.style.setProperty(name, value);

  CATEGORICAL.forEach((c, i) => {
    set(`--team-${i + 1}-light`, c.light);
    set(`--team-${i + 1}-dark`, c.dark);
  });
  [...CATEGORICAL].reverse().forEach((c, i) => {
    set(`--series-${i + 1}-light`, c.light);
    set(`--series-${i + 1}-dark`, c.dark);
  });
  Object.entries(STATUS).forEach(([k, c]) => {
    set(`--status-${k}-light`, c.light);
    set(`--status-${k}-dark`, c.dark);
  });
  Object.entries(TERRAIN_COLOR).forEach(([k, c]) => {
    set(`--terrain-${k}-light`, c.light);
    set(`--terrain-${k}-dark`, c.dark);
  });
  Object.entries(CHROME).forEach(([k, v]) => {
    set(`--chrome-${k}`, v);
  });
}
