import { studio, actions } from '../core/state.js';
import { $, field, btn, dialog, clone, esc, cm, toast } from '../core/dom.js';
import { diagrams } from '../preview/diagrams.js';
import { fabricColors } from '../config/colors.js';
import { errors } from '../config/copy.en.js';
import { api } from '../core/api.js';
export function renderFields() {
    const r = studio.data.request;
    field("widthCm").value = String(r.W_mm / 10);
    field("heightCm").value = String(r.H_mm / 10);
    field("frontColor").value = r.preferences.front_color || "";
    field("allowSplit").checked = Boolean(r.max_front_splits);
    field("allowMixed").checked = r.allow_mixed;
    /** @type {HTMLSelectElement} */ ($("priority")).value = String(r.preferences.fewer_seams_first);
    drawSize();
}

export function renderFabrics() {
    if (!studio.data)
        return;
    $("fabricCards").innerHTML = studio.data.materials
        .map((m, i) => {
        const b = diagrams.bounds(m.outer_mm);
        return ('<article class="card fabric-card"><div class="swatch" style="background-color:' +
            fabricColors.resolve(m.color).hex +
            '"><span class="fabric-status">' +
            (!m.available
                ? "Fabric unavailable"
                : m.measurement_source === "synthetic_fixture"
                    ? "Sample fabric"
                    : m.confirmed
                        ? "Measurements checked"
                        : "Needs your review") +
            "</span></div><h2>" +
            esc(m.name) +
            "</h2><p>" +
            cm(b.w) +
            " × " +
            cm(b.h) +
            " cm · " +
            (m.outer_mm.length === 6 ? "L-shaped" : "rectangle") +
            "</p><p>" +
            m.defects_mm.length +
            ' stain/hole markers</p><div class="actions"><button data-edit="' +
            i +
            '">Edit fabric</button><button data-available="' +
            i +
            '">' +
            (m.available ? "Mark unavailable" : "Use again") +
            '</button><button class="text-button" data-delete="' +
            i +
            '" aria-label="Delete ' +
            esc(m.name) +
            '">Delete</button></div></article>');
    })
        .join("");
    $("fabricCards")
        .querySelectorAll("[data-edit]")
        .forEach((n) => n.addEventListener("click", () => openFabric(Number(n.getAttribute("data-edit")))));
    $("fabricCards")
        .querySelectorAll("[data-available]")
        .forEach((n) => n.addEventListener("click", () => {
        const m = studio.data.materials[Number(n.getAttribute("data-available"))];
        actions.invalidate();
        m.available = !m.available;
        m.confirmed = false;
        renderFabrics();
    }));
    $("fabricCards")
        .querySelectorAll("[data-delete]")
        .forEach((n) => n.addEventListener("click", () => {
        if (studio.data.materials.length === 1) {
            toast("Keep at least one fabric, or add a replacement first.", true);
            return;
        }
        actions.invalidate();
        studio.data.materials.splice(Number(n.getAttribute("data-delete")), 1);
        renderFabrics();
    }));
    renderFields();
    actions.renderStepper();
}

export function openFabric(index, color = null) {
    studio.wizard = { stage: 1, shape: "rectangle", index, defects: [] };
    const m = index >= 0 ? studio.data.materials[index] : null, b = m ? diagrams.bounds(m.outer_mm) : { w: 190, h: 150 };
    studio.wizard.shape = m?.outer_mm.length === 6 ? "L" : "rectangle";
    studio.wizard.defects = m ? m.defects_mm.map((d) => diagrams.bounds(d)) : [];
    field("fabricWidth").value = String(b.w / 10);
    field("fabricHeight").value = String(b.h / 10);
    field("fabricName").value = m?.name || "";
    field("fabricColor").value = fabricColors.resolve(color || m?.color || "blue").hex;
    const selectedColor = color || m?.color;
    // Palette values keep the canonical name so families survive translation;
    // custom values keep the user's own spelling and case.
    studio.wizard.colorName = selectedColor
        ? fabricColors.resolve(selectedColor).family.startsWith('unknown:')
            ? fabricColors.displayName(selectedColor)
            : fabricColors.resolve(selectedColor).name
        : '';
    studio.wizard.colorLabel = selectedColor ? fabricColors.displayName(selectedColor) : '';
    field("fabricColorName").value = studio.wizard.colorLabel;
    field("fabricDirectional").checked = m
        ? m.allowed_rotations_deg.length === 1
        : true;
    field("notchWidth").value =
        m?.outer_mm.length === 6
            ? String((Math.max(...m.outer_mm.map((p) => p[0])) - m.outer_mm[3][0]) / 10)
            : "2";
    field("notchHeight").value =
        m?.outer_mm.length === 6 ? String((b.h - m.outer_mm[2][1]) / 10) : "5";
    $("fabricError").textContent = "";
    $("fabricDialogTitle").textContent = m
        ? "Check your fabric"
        : "Add your fabric";
    $("colorPicker").innerHTML = studio.config.color_palette
        .map((c) => '<button type="button" style="background:' +
        c.hex +
        '" aria-label="' +
        esc(fabricColors.displayName(c.name)) +
        '" title="' +
        esc(fabricColors.displayName(c.name)) +
        '" data-color="' +
        c.hex +
        '"></button>')
        .join("");
    $("colorPicker")
        .querySelectorAll("[data-color]")
        .forEach((n) => n.addEventListener("click", () => {
        field("fabricColor").value = n.getAttribute("data-color");
        studio.wizard.colorName = fabricColors.resolve(n.getAttribute("data-color")).name;
        studio.wizard.colorLabel = fabricColors.displayName(n.getAttribute("data-color"));
        field("fabricColorName").value = studio.wizard.colorLabel;
        drawWizard();
    }));
    drawWizard();
    dialog("fabricDialog").showModal();
}

export function wizardRing() {
    const w = Number(field("fabricWidth").value) * 10, h = Number(field("fabricHeight").value) * 10, nw = Number(field("notchWidth").value) * 10, nh = Number(field("notchHeight").value) * 10;
    return studio.wizard.shape === "L"
        ? [
            [0, 0],
            [w, 0],
            [w, h - nh],
            [w - nw, h - nh],
            [w - nw, h],
            [0, h],
        ]
        : [
            [0, 0],
            [w, 0],
            [w, h],
            [0, h],
        ];
}

export function drawWizard() {
    for (const [i, id] of ["wizardShape", "wizardSize", "wizardColor"].entries())
        $(id).hidden = studio.wizard.stage !== i + 1;
    $("wizardStage").textContent = [
        "",
        "1 · Choose a shape",
        "2 · Enter measurements",
        "3 · Choose a color and check for defects",
    ][studio.wizard.stage];
    btn("wizardBack").disabled = studio.wizard.stage === 1;
    btn("wizardNext").hidden = studio.wizard.stage === 3;
    btn("wizardSave").hidden = studio.wizard.stage !== 3;
    $("notchFields").hidden = studio.wizard.shape !== "L";
    document
        .querySelectorAll("[data-shape]")
        .forEach((n) => n.setAttribute("aria-pressed", String(n.getAttribute("data-shape") === studio.wizard.shape)));
    const ring = wizardRing(), b = diagrams.bounds(ring), scale = Math.min(240 / Math.max(b.w, 1), 150 / Math.max(b.h, 1)), points = (r) => r.map(([x, y]) => [30 + x * scale, 180 - y * scale].join(",")).join(" ");
    const base = '<polygon points="' +
        points(ring) +
        '" fill="' +
        field("fabricColor").value +
        '" fill-opacity=".3" stroke="#28418F"/><text x="30" y="202" font-size="14">' +
        cm(b.w) +
        " × " +
        cm(b.h) +
        " cm</text>";
    $("fabricSketch").innerHTML = base;
    $("defectSketch").innerHTML =
        base +
            studio.wizard.defects
                .map((d, i) => '<rect data-defect="' +
                i +
                '" x="' +
                (30 + d.x * scale) +
                '" y="' +
                (180 - (d.y + d.h) * scale) +
                '" width="' +
                d.w * scale +
                '" height="' +
                d.h * scale +
                '" fill="#B62F24" opacity=".7" tabindex="0" role="button" aria-label="Defect ' +
                (i + 1) +
                ', drag or use the arrow keys"/>')
                .join("");
    $("defectControls").innerHTML = studio.wizard.defects
        .map((d, i) => '<div class="fields"><label>Width of defect ' +
        (i + 1) +
        ' (cm)<input type="number" min="0.1" step="0.1" data-defect-width="' +
        i +
        '" value="' +
        d.w / 10 +
        '"></label><label>Height of defect ' +
        (i + 1) +
        ' (cm)<input type="number" min="0.1" step="0.1" data-defect-height="' +
        i +
        '" value="' +
        d.h / 10 +
        '"></label><button type="button" data-defect-delete="' +
        i +
        '">Delete defect marker</button></div>')
        .join("");
    $("defectSketch")
        .querySelectorAll("[data-defect]")
        .forEach((n) => {
        let start = null;
        const d = studio.wizard.defects[Number(n.getAttribute("data-defect"))];
        n.addEventListener("pointerdown", (e) => {
            if (!(e instanceof PointerEvent))
                return;
            start = { x: e.clientX, y: e.clientY, dx: d.x, dy: d.y };
            n.setPointerCapture(e.pointerId);
        });
        n.addEventListener("pointermove", (e) => {
            if (!(e instanceof PointerEvent))
                return;
            if (!start)
                return;
            const ratio = $("defectSketch").getBoundingClientRect().width / 300;
            d.x = Math.max(2, Math.min(b.w - d.w - 2, start.dx + (e.clientX - start.x) / ratio / scale));
            d.y = Math.max(2, Math.min(b.h - d.h - 2, start.dy - (e.clientY - start.y) / ratio / scale));
            n.setAttribute("x", String(30 + d.x * scale));
            n.setAttribute("y", String(180 - (d.y + d.h) * scale));
        });
        n.addEventListener("pointerup", () => {
            start = null;
        });
        n.addEventListener("keydown", (e) => {
            if (!(e instanceof KeyboardEvent))
                return;
            if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key))
                return;
            e.preventDefault();
            d.x = Math.max(2, Math.min(b.w - d.w - 2, d.x + (e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0)));
            d.y = Math.max(2, Math.min(b.h - d.h - 2, d.y + (e.key === "ArrowUp" ? 1 : e.key === "ArrowDown" ? -1 : 0)));
            drawWizard();
        });
    });
    $("defectControls")
        .querySelectorAll("input")
        .forEach((n) => n.addEventListener("change", () => {
        const i = Number(n.getAttribute("data-defect-width") ??
            n.getAttribute("data-defect-height"));
        studio.wizard.defects[i][n.hasAttribute("data-defect-width") ? "w" : "h"] =
            Number(/** @type {HTMLInputElement} */ (n).value) * 10;
        drawWizard();
    }));
    $("defectControls")
        .querySelectorAll("[data-defect-delete]")
        .forEach((n) => n.addEventListener("click", () => {
        studio.wizard.defects.splice(Number(n.getAttribute("data-defect-delete")), 1);
        drawWizard();
    }));
}

export async function saveFabric(e) {
    e.preventDefault();
    const ring = wizardRing(), b = diagrams.bounds(ring);
    if (!Number.isFinite(b.w) ||
        !Number.isFinite(b.h) ||
        b.w <= 0 ||
        b.h <= 0 ||
        (studio.wizard.shape === "L" &&
            (Number(field("notchWidth").value) * 10 >= b.w ||
                Number(field("notchHeight").value) * 10 >= b.h))) {
        $("fabricError").textContent =
            "The cutout must be smaller than the fabric. Enter your measured dimensions.";
        return;
    }
    const old = studio.wizard.index >= 0 ? studio.data.materials[studio.wizard.index] : null, ids = new Set(studio.data.materials.map((m) => m.id));
    const id = old?.id || ["A", "B", "C", "D"].find((i) => !ids.has(i));
    // ponytail: colorLabel tracks the English display name for wizard editing; canonical colorName is kept for payload preservation until the backend palette is translated to English.
    const typedName = field("fabricColorName").value.trim();
    const colorName = typedName && typedName === studio.wizard.colorLabel ? studio.wizard.colorName : typedName;
    const m = {
        id,
        name: field("fabricName").value.trim() ||
            (fabricColors.displayName(colorName || field("fabricColor").value) + " fabric").slice(0, 40),
        outer_mm: ring,
        defects_mm: studio.wizard.defects.map((d) => [
            [d.x, d.y],
            [d.x + d.w, d.y],
            [d.x + d.w, d.y + d.h],
            [d.x, d.y + d.h],
        ]),
        material_type: "woven_non_stretch",
        compatible_group: old?.compatible_group || "woven",
        color: fabricColors.customValue(colorName, field("fabricColor").value),
        allowed_rotations_deg: field("fabricDirectional").checked ? [0] : [0, 90],
        face: "right_side_up",
        confirmed: false,
        available: old?.available ?? true,
        measurement_source: "manual_mm",
    };
    actions.invalidate();
    if (old)
        studio.data.materials[studio.wizard.index] = m;
    else
        studio.data.materials.push(m);
    try {
        await actions.save();
        dialog("fabricDialog").close();
        renderFabrics();
    }
    catch {
        $("fabricError").textContent =
            "Check the measurements and defect positions: defects must stay inside the fabric and must not overlap.";
    }
}

export function drawSize() {
    const w = studio.data.request.W_mm, h = studio.data.request.H_mm;
    if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) {
        $("sizeSketch").innerHTML = "";
        return;
    }
    const scale = Math.min(180 / w, 110 / h), width = w * scale, height = h * scale;
    $("sizeSketch").innerHTML =
        '<rect x="' +
            (260 - width) / 2 +
            '" y="15" width="' +
            width +
            '" height="' +
            height +
            '" fill="#E6EBF8" stroke="#28418F"/><text x="130" y="' +
            (height + 40) +
            '" text-anchor="middle" font-size="14">' +
            cm(w) +
            " × " +
            cm(h) +
            " cm</text>";
}
