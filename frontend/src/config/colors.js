"use strict";

// The server sends the palette from colors.json. No second palette to drift.
export const fabricColors = (() => {
  let palette = [];
  const normalize = (value) =>
    String(value || "")
      .trim()
      .toLowerCase()
      .replaceAll("_", " ")
      .replace(/\s+/g, " ");
  const lab = (hex) => {
    const [r, g, b] = [1, 3, 5]
      .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    const [x, y, z] = [
      (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047,
      r * 0.2126729 + g * 0.7151522 + b * 0.072175,
      (r * 0.0193339 + g * 0.119192 + b * 0.9503041) / 1.08883,
    ].map((v) =>
      v > (6 / 29) ** 3 ? Math.cbrt(v) : v / (3 * (6 / 29) ** 2) + 4 / 29,
    );
    return [116 * y - 16, 500 * (x - y), 200 * (y - z)];
  };
  const distance = (a, b) => Math.hypot(...lab(a).map((v, i) => v - lab(b)[i]));
  const resolve = (value) => {
    const name = normalize(value);
    const custom = /^(.{1,40})\s+\(\s*(#[0-9a-f]{6})\s*\)$/.exec(name);
    if (custom) {
      const [, label, hex] = custom;
      const entry = palette.find(p => p.aliases.includes(label) || p.key === label || p.en === label);
      return {
        family: entry ? entry.key : `unknown:${label}`,
        name: label,
        label: entry?.en || String(value).slice(0, String(value).lastIndexOf('(')).trim(),
        hex: hex.toUpperCase(),
      };
    }
    const isHex = /^#[0-9a-f]{6}$/.test(name);
    let entry = palette.find((p) => p.aliases.includes(name) || p.key === name || p.en === name);
    if (isHex && palette.length)
      entry = palette.reduce((a, b) =>
        distance(name, a.hex) <= distance(name, b.hex) ? a : b,
      );
    return entry
      ? {
          family: entry.key,
          name: entry.name,
          label: entry.en || entry.name,
          hex: isHex ? name.toUpperCase() : entry.hex,
        }
      : {
          family: `unknown:${name}`,
          name: name || "tanpa preferensi",
          label: String(value ?? '').trim() || "no preference",
          hex: isHex ? name.toUpperCase() : "#788a96",
        };
  };
  return {
    setPalette: (value) => {
      palette = Array.isArray(value) ? value : [];
    },
    resolve,
    displayName: (value) => resolve(value).label,
    customValue: (name, hex) => name.trim() && resolve(name).hex !== hex.toUpperCase() ? `${name.trim()} (${hex.toUpperCase()})` : hex.toUpperCase(),
    sameFamily: (a, b) =>
      a != null && b != null && resolve(a).family === resolve(b).family,
  };
})();
