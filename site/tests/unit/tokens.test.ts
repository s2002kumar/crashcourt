import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

import { ACTORS, verdictLegend } from "../../lib/tokens.ts";
import {
  VISIONS,
  contrast,
  deltaE2000Lab,
  minPairwiseDelta,
  parseHex,
  type Rgb,
  type Vision,
} from "./color.ts";

type Theme = "light" | "dark";
const THEMES: readonly Theme[] = ["light", "dark"];

/** Reads every color token in styles/tokens.css as {name: {light, dark}}. */
function readTokens(): Map<string, Record<Theme, string>> {
  const css = readFileSync(new URL("../../styles/tokens.css", import.meta.url), "utf8");
  const tokens = new Map<string, Record<Theme, string>>();
  for (const m of css.matchAll(/--([\w-]+):\s*light-dark\((#[0-9a-f]{6}),\s*(#[0-9a-f]{6})\);/gi)) {
    tokens.set(m[1]!, { light: m[2]!, dark: m[3]! });
  }
  for (const m of css.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6});/gi)) {
    tokens.set(m[1]!, { light: m[2]!, dark: m[2]! });
  }
  return tokens;
}

const TOKENS = readTokens();
const TINT = (() => {
  const css = readFileSync(new URL("../../styles/tokens.css", import.meta.url), "utf8");
  const m = /--verdict-tint:\s*(\d+(?:\.\d+)?)%;/.exec(css);
  assert.ok(m, "styles/tokens.css defines --verdict-tint");
  return Number(m[1]) / 100;
})();

/** A color at `alpha` over an opaque surface, as the browser composites it (in sRGB). */
function over(fg: Rgb, bg: Rgb, alpha: number): Rgb {
  return [0, 1, 2].map((i) => fg[i]! * alpha + bg[i]! * (1 - alpha)) as unknown as Rgb;
}

function color(name: string, theme: Theme): string {
  const t = TOKENS.get(name);
  assert.ok(t, `styles/tokens.css defines --${name}`);
  return t[theme];
}

const SURFACES = ["bg", "panel", "raised"];

function minContrast(name: string, theme: Theme): number {
  const fg = parseHex(color(name, theme));
  return Math.min(...SURFACES.map((s) => contrast(fg, parseHex(color(s, theme)))));
}

// Thresholds. Text is WCAG 2.2 AA (1.4.3); graphics and focus indicators are 3:1 (1.4.11).
// Separation is the smallest CIEDE2000 difference between any two colors in a set, as seen with
// normal vision and with each simulated dichromacy.
const TEXT = 4.5;
const GRAPHIC = 3;
const SEPARATION: Record<"verdict" | "actor", Record<Vision, number>> = {
  verdict: { normal: 20, protanopia: 10, deuteranopia: 10, tritanopia: 10 },
  actor: { normal: 20, protanopia: 9, deuteranopia: 9, tritanopia: 9 },
};

const verdictVars = verdictLegend().map(({ verdict }) => `verdict-${verdict.toLowerCase()}`);
const actorVars = ACTORS.map((a) => `actor-${a}`);

describe("tokens.css", () => {
  test("defines a token for every verdict and actor in lib/tokens.ts", () => {
    for (const name of [...verdictVars, ...actorVars]) assert.ok(TOKENS.has(name), `--${name}`);
  });

  for (const theme of THEMES) {
    describe(`${theme} theme`, () => {
      test("text colors meet 4.5:1 on every surface", () => {
        for (const name of ["ink", "ink-muted", "ink-faint", "accent-text", ...verdictVars]) {
          const c = minContrast(name, theme);
          assert.ok(c >= TEXT, `--${name} is ${c.toFixed(2)}:1`);
        }
      });

      test("actor colors, the focus ring, and the accent edge meet 3:1 on every surface", () => {
        for (const name of [...actorVars, "focus", "accent-edge"]) {
          const c = minContrast(name, theme);
          assert.ok(c >= GRAPHIC, `--${name} is ${c.toFixed(2)}:1`);
        }
      });

      test("a verdict badge keeps its ink label at 4.5:1 and its glyph at 3:1 on its own tint", () => {
        for (const name of verdictVars) {
          const verdict = parseHex(color(name, theme));
          for (const surface of SURFACES) {
            const badge = over(verdict, parseHex(color(surface, theme)), TINT);
            const label = contrast(parseHex(color("ink", theme)), badge);
            const glyph = contrast(verdict, badge);
            assert.ok(
              label >= TEXT,
              `ink on --${name} badge over --${surface}: ${label.toFixed(2)}:1`,
            );
            assert.ok(glyph >= GRAPHIC, `--${name} glyph over --${surface}: ${glyph.toFixed(2)}:1`);
          }
        }
      });

      test("text on the accent meets 4.5:1", () => {
        const c = contrast(parseHex(color("accent-ink", theme)), parseHex(color("accent", theme)));
        assert.ok(c >= TEXT, `${c.toFixed(2)}:1`);
      });

      for (const [set, names] of [
        ["verdict", verdictVars],
        ["actor", actorVars],
      ] as const) {
        test(`${set} colors stay apart for normal vision and each dichromacy, with the accent`, () => {
          const colors = Object.fromEntries([
            ...names.map((n) => [n, color(n, theme)]),
            ["accent", color("accent", theme)],
          ]);
          for (const vision of VISIONS) {
            const { delta, pair } = minPairwiseDelta(colors, vision);
            const floor = SEPARATION[set][vision];
            assert.ok(
              delta >= floor,
              `${vision}: ${pair.join(" vs ")} differ by ${delta.toFixed(1)} < ${floor}`,
            );
          }
        });
      }
    });
  }
});

describe("color math", () => {
  test("CIEDE2000 matches Sharma, Wu, and Dalal's published test pairs", () => {
    const pairs: [[number, number, number], [number, number, number], number][] = [
      [[50, 2.6772, -79.7751], [50, 0, -82.7485], 2.0425],
      [[50, 3.1571, -77.2803], [50, 0, -82.7485], 2.8615],
      [[50, -1.3802, -84.2814], [50, 0, -82.7485], 1.0],
      [[50, 2.5, 0], [73, 25, -18], 27.1492],
      [[2.0776, 0.0795, -1.135], [0.9033, -0.0636, -0.5514], 0.9082],
      [[60.2574, -34.0099, 36.2677], [60.4626, -34.1751, 39.4387], 1.2644],
    ];
    for (const [a, b, want] of pairs) assert.equal(deltaE2000Lab(a, b).toFixed(4), want.toFixed(4));
  });

  test("WCAG contrast of black on white is 21:1", () => {
    assert.equal(contrast(parseHex("#000000"), parseHex("#ffffff")).toFixed(2), "21.00");
  });
});
