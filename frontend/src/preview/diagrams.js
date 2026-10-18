import { fabricColors } from '../config/colors.js';
"use strict";
export const diagrams = (() => {
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const bounds = (ring) => {
    const xs = ring.map((p) => p[0]),
      ys = ring.map((p) => p[1]);
    return {
      x: Math.min(...xs),
      y: Math.min(...ys),
      w: Math.max(...xs) - Math.min(...xs),
      h: Math.max(...ys) - Math.min(...ys),
    };
  };
  const transform = (ring, p) => {
    const a = (p.rotation_deg * Math.PI) / 180;
    return ring.map(([x, y]) => [
      x * Math.cos(a) - y * Math.sin(a) + p.tx_mm,
      x * Math.sin(a) + y * Math.cos(a) + p.ty_mm,
    ]);
  };
  const cm = (n) =>
    (n / 10).toLocaleString("en", { maximumFractionDigits: 2 });
  const partNames = { FRONT: "Front", FRONT_L: "Left front", FRONT_R: "Right front", BACK: "Back" };
  function layout(data, candidate, config) {
    const mats = data.materials,
      parts = Object.fromEntries(
        (candidate?.patterns || []).map((p) => [p.id, p]),
      );
    const maxW = Math.max(...mats.map((m) => bounds(m.outer_mm).w)),
      maxH = Math.max(...mats.map((m) => bounds(m.outer_mm).h));
    const scale = Math.min(248 / maxW, 155 / maxH);
    return (
      '<div class="layout-grid">' +
      mats
        .map((m, index) => {
          const box = bounds(m.outer_mm),
            used = (candidate?.placements || []).filter(
              (p) => p.material_id === m.id,
            ),
            project = (ring) =>
              ring
                .map(([x, y]) =>
                  [(x - box.x) * scale + 28, 182 - (y - box.y) * scale].join(
                    ",",
                  ),
                )
                .join(" ");
          const color = fabricColors.resolve(m.color).hex,
            name = m.name || fabricColors.displayName(m.color) + " fabric";
          let svg = `<svg viewBox="0 0 304 232" role="img" aria-label="${esc(name)}, ${cm(box.w)} × ${cm(box.h)} cm"><defs><pattern id="defect-${index}" width="6" height="6" patternUnits="userSpaceOnUse"><path d="m0 6 6-6" stroke="#9D231B" stroke-width="2"/></pattern></defs><polygon points="${project(m.outer_mm)}" fill="${color}" fill-opacity=".14" stroke="#394150" stroke-width="1.5" ${!used.length ? 'stroke-dasharray="5 4"' : ""}/>`;
          for (const d of m.defects_mm)
            svg += `<polygon points="${project(d)}" fill="url(#defect-${index})" stroke="#9D231B"/><text x="${28 + (bounds(d).x - box.x) * scale}" y="${178 - (bounds(d).y - box.y) * scale}" font-size="12" fill="#8A2018">avoid</text>`;
          for (const p of used) {
            const part = parts[p.instance_id],
              cut = transform(part.cut_mm, p),
              b = bounds(cut),
              s = config.allowances?.seam_mm || 10,
              hem =
                (config.allowances?.fold1_mm || 5) +
                (config.allowances?.fold2_mm || 5);
            const x0 = part.role === "front_right" ? s : s,
              x1 = part.width_mm - s,
              y0 = s,
              y1 = part.height_mm - hem;
            const finished = transform(
              [
                [x0, y0],
                [x1, y0],
                [x1, y1],
                [x0, y1],
              ],
              p,
            );
            svg += `<polygon points="${project(cut)}" fill="white" fill-opacity=".72" stroke="#28418F" stroke-width="2"/><polygon points="${project(finished)}" fill="none" stroke="#28418F" stroke-dasharray="4 4"/><text x="${28 + (b.x - box.x + b.w / 2) * scale}" y="${182 - (b.y - box.y + b.h / 2) * scale}" text-anchor="middle" font-size="12" fill="#1E2430">${esc(partNames[p.instance_id] || config.part_names?.[p.instance_id] || "Piece")}</text>`;
          }
          if (!m.available)
            svg += `<path d="M28 27 276 182M276 27 28 182" stroke="#394150" stroke-width="2"/>`;
          svg += `<path d="M28 198h${box.w * scale}" stroke="#394150"/><text x="${28 + (box.w * scale) / 2}" y="219" text-anchor="middle" font-size="12" fill="#394150">${cm(box.w)} cm</text><text x="22" y="24" font-size="12" fill="#394150">height ${cm(box.h)} cm</text></svg>`;
          return `<div class="layout-item" data-scale="${scale}">${svg}<p><strong>${esc(name)}</strong> · ${!m.available ? "Fabric turned off" : used.length ? "Used" : "Not used"}</p></div>`;
        })
        .join("") +
      "</div>"
    );
  }
  function nominal(data, candidate, config) {
    if (!candidate)
      return '<div class="empty"><p>The pouch shape will appear after the design is checked.</p></div>';
    const materials = Object.fromEntries(data.materials.map((m) => [m.id, m])),
      faces = Object.fromEntries(
        candidate.placements.map((p) => [
          p.instance_id,
          materials[p.material_id],
        ]),
      ),
      w = data.request.W_mm,
      h = data.request.H_mm,
      scale = Math.min(135 / w, 170 / h),
      width = w * scale,
      height = h * scale;
    const front = faces.FRONT ? [faces.FRONT] : [faces.FRONT_L, faces.FRONT_R];
    let svg =
      '<svg viewBox="0 0 340 235" width="100%" height="100%" role="img" aria-label="Pouch front and back"><title>Illustration of the front and back</title>';
    for (const [i, panels] of [front, [faces.BACK]].entries()) {
      const x = i * 170 + (170 - width) / 2,
        y = 18;
      panels.forEach((m, j) => {
        svg += `<rect x="${x + (j * width) / panels.length}" y="${y}" width="${width / panels.length}" height="${height}" rx="2" fill="${fabricColors.resolve(m.color).hex}"/>`;
      });
      svg += `<path d="M${x + 5} ${y + 12}h${width - 10}M${x + 6} ${y + 18}v${height - 24}h${width - 12}v${-height + 24}" stroke="#F3EFE6" stroke-dasharray="3 4" fill="none"/><text x="${x + width / 2}" y="${y + height + 24}" text-anchor="middle" font-size="14" fill="#394150">${i ? "Back" : "Front"}</text>`;
    }
    return svg + "</svg>";
  }
  return { bounds, layout, nominal, esc, cm };
})();
