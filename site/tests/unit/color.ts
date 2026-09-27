// Color math for the token tests: WCAG 2.2 contrast, color-vision-deficiency simulation, and CIEDE2000.

export type Rgb = readonly [number, number, number];

export function parseHex(hex: string): Rgb {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) throw new Error(`not a #rrggbb color: ${hex}`);
  const n = parseInt(m[1]!, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

const toLinear = (c: number): number => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toGamma = (c: number): number =>
  c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
const clamp01 = (c: number): number => Math.min(1, Math.max(0, c));

export function linear(rgb: Rgb): Rgb {
  return [toLinear(rgb[0]), toLinear(rgb[1]), toLinear(rgb[2])];
}

/** WCAG 2.2 relative luminance. */
export function luminance(rgb: Rgb): number {
  const [r, g, b] = linear(rgb);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2.2 contrast ratio, 1 to 21. */
export function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

// Machado, Oliveira, and Fernandes (2009), severity 1.0, applied to linear sRGB.
const CVD = {
  protanopia: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deuteranopia: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
  tritanopia: [
    [1.255528, -0.076749, -0.178779],
    [-0.078411, 0.930809, 0.147602],
    [0.004733, 0.691367, 0.3039],
  ],
} as const;

export type Vision = "normal" | keyof typeof CVD;
export const VISIONS: readonly Vision[] = ["normal", "protanopia", "deuteranopia", "tritanopia"];

export function simulate(rgb: Rgb, vision: Vision): Rgb {
  if (vision === "normal") return rgb;
  const m = CVD[vision];
  const l = linear(rgb);
  const row = (i: 0 | 1 | 2): number => {
    const r = m[i];
    return toGamma(clamp01(r[0] * l[0] + r[1] * l[1] + r[2] * l[2]));
  };
  return [row(0), row(1), row(2)];
}

export type Lab = readonly [number, number, number];

export function lab(rgb: Rgb): Lab {
  const [r, g, b] = linear(rgb);
  // sRGB to XYZ (D65), normalized by the D65 white point.
  const x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047;
  const y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b;
  const z = (0.0193339 * r + 0.119192 * g + 0.9503041 * b) / 1.08883;
  const f = (t: number): number => (t > 216 / 24389 ? Math.cbrt(t) : ((24389 / 27) * t + 16) / 116);
  const [fx, fy, fz] = [f(x), f(y), f(z)];
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

/** CIEDE2000 color difference between two sRGB colors. */
export function deltaE2000(a: Rgb, b: Rgb): number {
  return deltaE2000Lab(lab(a), lab(b));
}

/** CIEDE2000 color difference in CIELAB (Sharma, Wu, and Dalal 2005). */
export function deltaE2000Lab([l1, a1, b1]: Lab, [l2, a2, b2]: Lab): number {
  const rad = Math.PI / 180;
  const c1 = Math.hypot(a1, b1);
  const c2 = Math.hypot(a2, b2);
  const cBar7 = ((c1 + c2) / 2) ** 7;
  const g = 0.5 * (1 - Math.sqrt(cBar7 / (cBar7 + 25 ** 7)));
  const ap1 = (1 + g) * a1;
  const ap2 = (1 + g) * a2;
  const cp1 = Math.hypot(ap1, b1);
  const cp2 = Math.hypot(ap2, b2);
  const hue = (bb: number, ap: number): number => {
    if (bb === 0 && ap === 0) return 0;
    const h = Math.atan2(bb, ap) / rad;
    return h < 0 ? h + 360 : h;
  };
  const hp1 = hue(b1, ap1);
  const hp2 = hue(b2, ap2);
  const dL = l2 - l1;
  const dC = cp2 - cp1;
  let dh = 0;
  if (cp1 * cp2 !== 0) {
    dh = hp2 - hp1;
    if (dh > 180) dh -= 360;
    else if (dh < -180) dh += 360;
  }
  const dH = 2 * Math.sqrt(cp1 * cp2) * Math.sin((dh / 2) * rad);
  const lBar = (l1 + l2) / 2;
  const cBar = (cp1 + cp2) / 2;
  let hBar = hp1 + hp2;
  if (cp1 * cp2 !== 0) {
    if (Math.abs(hp1 - hp2) <= 180) hBar = (hp1 + hp2) / 2;
    else hBar = hp1 + hp2 < 360 ? (hp1 + hp2 + 360) / 2 : (hp1 + hp2 - 360) / 2;
  }
  const t =
    1 -
    0.17 * Math.cos((hBar - 30) * rad) +
    0.24 * Math.cos(2 * hBar * rad) +
    0.32 * Math.cos((3 * hBar + 6) * rad) -
    0.2 * Math.cos((4 * hBar - 63) * rad);
  const dTheta = 30 * Math.exp(-(((hBar - 275) / 25) ** 2));
  const cBarP7 = cBar ** 7;
  const rc = 2 * Math.sqrt(cBarP7 / (cBarP7 + 25 ** 7));
  const sl = 1 + (0.015 * (lBar - 50) ** 2) / Math.sqrt(20 + (lBar - 50) ** 2);
  const sc = 1 + 0.045 * cBar;
  const sh = 1 + 0.015 * cBar * t;
  const rt = -Math.sin(2 * dTheta * rad) * rc;
  return Math.sqrt((dL / sl) ** 2 + (dC / sc) ** 2 + (dH / sh) ** 2 + rt * (dC / sc) * (dH / sh));
}

/** The smallest pairwise CIEDE2000 difference in a set of colors, as seen with the given vision. */
export function minPairwiseDelta(
  colors: Readonly<Record<string, string>>,
  vision: Vision,
): { delta: number; pair: [string, string] } {
  const entries = Object.entries(colors).map(
    ([k, v]) => [k, simulate(parseHex(v), vision)] as const,
  );
  let best = { delta: Infinity, pair: ["", ""] as [string, string] };
  for (let i = 0; i < entries.length; i++) {
    for (let j = i + 1; j < entries.length; j++) {
      const d = deltaE2000(entries[i]![1], entries[j]![1]);
      if (d < best.delta) best = { delta: d, pair: [entries[i]![0], entries[j]![0]] };
    }
  }
  return best;
}
