import { studio, actions } from './core/state.js';
import { $, field, btn, dialog, clone, esc, cm, toast } from './core/dom.js';
import { diagrams } from './preview/diagrams.js';
import { fabricColors } from './config/colors.js';
import { errors, explanationLabels } from './config/copy.en.js';
import { api } from './core/api.js';
import { say, showBrief, applyBrief, chat, sendStory, chatDesign } from './features/chat.js';
import { renderFields, renderFabrics, openFabric, wizardRing, drawWizard, saveFabric, drawSize } from './features/materials.js';
import { pdf, renderPdfPreview, clearPdfPreview } from './features/pdf.js';
import { savedStatus, rememberStep, restoreView, scheduleAutosave, announceChange, reportConflict, restoreConversation, recordEvent, setupProjects, beginDraft, clearDraft, restoreDraft } from './features/projects.js';
import { setupTour } from './features/onboarding.js';
import { setupOverlays } from './features/overlays.js';
const partNames = { FRONT: 'Front panel', FRONT_L: 'Left front', FRONT_R: 'Right front', BACK: 'Back panel' };
const checkLabels = {
    'VAL-01': 'Size and shape match',
    'VAL-02': 'All pieces fit within the fabric',
    'VAL-03': 'Pieces avoid stains and holes',
    'VAL-04': 'Pieces do not overlap',
    'VAL-05': 'Fabric direction and color combinations match your choices',
    'VAL-06': 'All parts and fabrics are available',
    'VAL-07': 'Seam and fold lines match',
    'VAL-08': 'Finished size and seam choices match',
    'VAL-09': 'Design data is up to date',
    'VAL-10': 'You have checked the fabric dimensions',
};
setupOverlays();
try {
    const stored = localStorage.getItem("sisa.ai");
    studio.consent = stored === null ? null : stored === "true";
}
catch { }
function setBusy(value, text = "") {
    studio.busy = value;
    $("progress").hidden = !value;
    $("progressText").textContent = text;
    for (const id of [
        "sendStory",
        "demoStart",
        "confirmFabric",
        "downloadGuide",
        "downloadTechnical",
        "toGuide",
    ])
        btn(id).disabled = value || !studio.state || studio.conflict;
    if (value)
        btn("cancelWork").disabled = !studio.state;
    renderStepper();
    savedStatus();
}
async function safe(fn, blocking = true) {
    if (blocking)
        setBusy(true, "Updating your design");
    try {
        await fn();
    }
    catch (e) {
        if (e.name !== "AbortError") {
            toast(e.message, true);
            recordEvent('error');
        }
    }
    finally {
        if (blocking)
            setBusy(false);
    }
}
function adopt(next, replace = true) {
    const before = studio.state;
    studio.state = next;
    if (replace)
        studio.data = clone(next.data);
    document.body.dataset.phase = next.phase;
    renderStepper();
    $("technicalDetails").textContent = JSON.stringify({
        phase: next.phase,
        revision: next.active_revision?.design_revision_id,
        signature: next.signature,
        trace: next.ai_trace,
        search: next.search,
    }, null, 2);
    announceChange(before, next);
}
function renderStepper() {
    if (!studio.state)
        return;
    document.querySelectorAll("[data-step]").forEach((node) => {
        const n = Number(node.getAttribute("data-step"));
        const allowed = !studio.conflict && (n === 1 ||
            (n === 2 && studio.briefReady) ||
            (n === 3 && Boolean(studio.state.candidate || studio.state.active_revision)) ||
            (n === 4 && Boolean(studio.state.active_revision && !studio.dirty)));
        /** @type {HTMLButtonElement} */ (node).disabled = !allowed;
        node.setAttribute("aria-label", node.textContent.trim() +
            (allowed ? "" : ". " + node.getAttribute("title")));
        if (n === studio.step)
            node.setAttribute("aria-current", "step");
        else
            node.removeAttribute("aria-current");
    });
    btn("downloadGuide").disabled = studio.busy || studio.conflict || !studio.state.active_revision || studio.dirty;
    btn("downloadTechnical").disabled = btn("downloadGuide").disabled;
    btn("printGuide").disabled = !studio.pdfUrl || studio.dirty || studio.busy;
    btn("toGuide").disabled = studio.busy || studio.conflict || !studio.state.active_revision || studio.dirty;
    btn("addFabric").disabled =
        studio.busy || studio.data.materials.length >= studio.config.max_materials;
    btn("regenerateImage").disabled =
        Boolean(studio.imageController) || studio.busy ||
            !studio.consent ||
            !studio.config.ai_image_available ||
            !studio.state.active_revision || studio.dirty;
}
function showStep(n, focus = true) {
    const node = document.querySelector('[data-step="' + n + '"]');
    if (node?.hasAttribute("disabled"))
        return;
    studio.step = n;
    rememberStep();
    for (let i = 1; i <= 4; i++)
        $("step" + i).hidden = i !== n;
    renderStepper();
    if (n === 2)
        actions.renderFabrics();
    if (n === 3) {
        renderDesign();
        studio.viewer?.setVisible?.(true);
    }
    else
        studio.viewer?.setVisible?.(false);
    if (focus) {
        const title = $("step" + n).querySelector("h1");
        title.setAttribute("tabindex", "-1");
        title.focus({ preventScroll: true });
        window.scrollTo({ top: 0, behavior: "auto" });
    }
}
function clearOutputs() {
    $('colorReplacementChoices')?.remove();
    clearImageModel();
    clearPdfPreview();
    if (studio.state?.active_revision)
        studio.previous = {
            data: clone(studio.state.data),
            revision: clone(studio.state.active_revision),
        };
    if (studio.state) {
        studio.state.active_revision = null;
        studio.state.candidate = null;
        studio.state.image = null;
    }
    if (studio.pdfUrl)
        URL.revokeObjectURL(studio.pdfUrl);
    studio.pdfUrl = null;
    studio.pdfSignature = null;
    studio.viewer?.dispose?.();
    studio.viewer = null;
    studio.viewerRevision = null;
    $("pouchScene").innerHTML = "";
    $("imagePreview").innerHTML =
        '<div class="empty"><p>Create a new design from your latest inputs.</p></div>';
    $("pdfPreview").innerHTML =
        "<p>Inputs changed. Create a new design first.</p>";
    studio.imageController?.abort();
    studio.pdfController?.abort();
    studio.imageController = null;
    studio.pdfController = null;
}
function invalidate() {
    beginDraft();
    studio.epoch++;
    studio.dirty = true;
    clearOutputs();
    $("brief").innerHTML = "";
    document.body.dataset.phase = "stale";
    renderStepper();
    studio.pendingMutations++;
    studio.mutation = studio.mutation.then(async () => {
        try {
            adopt(await api("/cancel", {
                expected_generation_token: studio.state.generation_token,
            }), false);
        }
        catch (error) {
            if(!studio.conflict) {
                const remote = await api("/state");
                if(remote.generation_token !== studio.state.generation_token) await reportConflict();
            }
        }
    }).finally(() => { studio.pendingMutations--; });
    scheduleAutosave();
}
async function save() {
    if(studio.conflict) throw new Error('This work changed in another tab. Choose which inputs to keep.');
    if(studio.savingDraft) {
        await studio.savingDraft;
        if(studio.dirty) return save();
        return;
    }
    studio.pendingMutations++;
    const operation = studio.mutation.then(async () => {
        if(studio.conflict) throw new Error('This work changed in another tab. Choose which inputs to keep.');
        if(!studio.dirty) return;
        const version = studio.epoch;
        const next = await api("/input", {
            data: clone(studio.data), expected_generation_token: studio.state.generation_token,
        });
        if(studio.conflict) return;
        adopt(next, version === studio.epoch);
        if(version === studio.epoch) { studio.dirty = false; clearDraft(); }
        savedStatus();
    }).finally(() => { studio.pendingMutations--; });
    // Input and cancellation share one queue. A second edit cannot send an
    // old generation guard while an earlier input response is in flight.
    studio.mutation = operation.catch(() => {});
    studio.savingDraft = operation;
    try {
        await operation;
    } finally { studio.savingDraft = null; }
    if(studio.dirty && !studio.conflict) return save();
}
async function permission() {
    if (studio.consent !== null)
        return studio.consent;
    dialog("consentDialog").showModal();
    return new Promise((resolve) => {
        studio.consentResolve = resolve;
    });
}
function chooseConsent(value) {
    studio.consent = value;
    field("aiEnabled").checked = value;
    try {
        localStorage.setItem("sisa.ai", String(value));
    }
    catch { }
    dialog("consentDialog").close();
    studio.consentResolve?.(value);
    studio.consentResolve = null;
    updateAiStatus();
}
function updateAiStatus() {
    $("aiStatus").textContent =
        studio.consent === false
            ? "Without AI"
            : studio.config?.ai_available
                ? "Design assistant"
                : "AI not available yet";
}
function startManual() {
    studio.briefReady = true;
    updateAiStatus();
    renderStepper();
    showStep(2);
}
async function makePlan(useAi = studio.consent && studio.config.ai_available) {
    await permission();
    await save();
    const version = studio.epoch;
    setBusy(true, "Matching your request to the fabric");
    const next = await api("/confirm", {
        expected_generation_token: studio.state.generation_token,
    });
    if (version !== studio.epoch)
        return;
    adopt(next);
    studio.activeController = new AbortController();
    setBusy(true, "Finding a layout and checking dimensions");
    const planned = await api("/plan", {
        use_ai: Boolean(useAi),
        ai_consent: Boolean(studio.consent),
        expected_generation_token: studio.state.generation_token,
    }, studio.activeController.signal);
    if (version !== studio.epoch)
        return;
    adopt(planned);
    studio.dirty = false;
    studio.briefReady = true;
    studio.activeController = null;
    renderDesign();
    showStep(3);
    actions.chatDesign();
    recordEvent('design_ready');
    if (studio.consent && studio.config.ai_image_available && studio.state.active_revision)
        imageAction().catch((e) => {
            if (e.name !== "AbortError")
                toast(e.message, true);
        });
}
async function decideSplit(accepted) {
    if (studio.splitPending || studio.conflict || !studio.state?.candidate)
        return;
    studio.splitPending = true;
    const version = studio.epoch;
    const signature = studio.state.signature;
    studio.pendingMutations++;
    const operation = studio.mutation.then(async () => {
        if (studio.conflict || version !== studio.epoch)
            return false;
        const next = await api("/accept-split", {
            accepted,
            additional_seams: 1,
            candidate_signature: signature,
            expected_generation_token: studio.state.generation_token,
        });
        if (studio.conflict)
            return false;
        if (version !== studio.epoch) {
            // Keep the queue's generation guard without restoring obsolete outputs.
            studio.state.generation_token = next.generation_token;
            return false;
        }
        adopt(next);
        return true;
    }).finally(() => { studio.pendingMutations--; });
    studio.mutation = operation.catch(() => {});
    try {
        if (!await operation || version !== studio.epoch || studio.conflict)
            return;
        if (accepted) {
            renderDesign();
            if (studio.consent && studio.config.ai_image_available)
                imageAction().catch((error) => toast(error.message, true));
        } else {
            clearOutputs();
            actions.renderFabrics();
            showStep(2);
        }
    } finally { studio.splitPending = false; }
}
function renderDesign() {
    const c = studio.state?.active_revision || studio.state?.candidate;
    if (!c) {
        $("metrics").innerHTML = "";
        return;
    }
    const used = new Set(c.placements.map((p) => p.material_id)).size, available = studio.data.materials.filter((m) => m.available).length;
    if(!$("visualCards").dataset.chosen) selectVisual(studio.state.image || (studio.consent && studio.config.ai_image_available) ? 'image' : 'model');
    $("metrics").innerHTML = [
        [
            "Finished size",
            cm(studio.data.request.W_mm) + " × " + cm(studio.data.request.H_mm) + " cm",
        ],
        ["Fabric used", used + " of " + available],
        ["Extra seams", String(c.extra_seams)],
    ]
        .map(([label, value]) => '<div class="metric"><span>' +
        label +
        "</span><b>" +
        value +
        "</b></div>")
        .join("");
    $("designSource").textContent = studio.state.ai_trace?.live_model_used
        ? "AI-designed · SISA-checked"
        : studio.state.ai_trace?.requested
            ? "AI unavailable · calculated automatically"
            : "Calculated automatically by SISA";
    $("colorNotice").hidden = !c.color_notice;
    $("colorNotice").textContent = c.color_notice || "";
    $("designDescription").textContent =
        c.variant === "whole"
            ? "Front and back each use one whole piece."
            : "Front uses two pieces; finished size stays the same.";
    $("splitDecision").hidden = Boolean(studio.state.active_revision);
    if (!studio.state.active_revision) {
        $("splitDecision").innerHTML =
            '<h2>Same size. Requires one extra seam.</h2><div class="split-visual"><span><i></i>Single front</span><span><i class="split"></i>Two front pieces</span></div><p>A single fabric piece does not fit the front. Available pieces can be joined in the middle; colors may differ.</p><div class="actions"><button id="acceptSplit" class="primary">Use one seam</button><button id="rejectSplit">Choose other fabric</button></div>';
        btn("acceptSplit").onclick = () => safe(() => decideSplit(true));
        btn("rejectSplit").onclick = () => safe(() => decideSplit(false));
    }
    btn("seamToggle").hidden = c.variant !== "front_split_center";
    $("layoutDiagram").innerHTML = diagrams.layout(studio.data, c, studio.config);
    $("guideSteps").innerHTML =
        "<summary>Guide steps</summary>" +
            (c.guide_steps || [])
                .map((s, i) => "<p><strong>" +
                (i + 1) +
                ". " +
                esc(s.title) +
                "</strong></p><p>" +
                esc(s.text) +
                "</p>")
                .join("");
    $("printTolerance").textContent =
        "Maximum difference of " +
            studio.config.tolerance.printed_square_mm +
            " mm in width and height.";
    if (studio.step === 3)
        mountViewer(c);
    else if (!studio.viewer)
        $("pouchScene").innerHTML = diagrams.nominal(studio.data, c, studio.config);
    $("pouchScene").setAttribute("aria-label", "Open-top pouch, size " +
        cm(studio.data.request.W_mm) +
        " × " +
        cm(studio.data.request.H_mm) +
        " cm. " + c.placements.map(p => (partNames[p.instance_id] || studio.config.part_names[p.instance_id]) + ": " + fabricColors.displayName(studio.data.materials.find(m => m.id === p.material_id).color)).join(", "));
    const names = Object.fromEntries(studio.data.materials.map((m) => [m.id, m]));
    $("swatches").innerHTML = c.placements
        .map((p) => '<span><i style="background:' +
        fabricColors.resolve(names[p.material_id].color).hex +
        '"></i>' +
        esc(partNames[p.instance_id] || studio.config.part_names[p.instance_id]) +
        ": " +
        esc(names[p.material_id].name) +
        "</span>")
        .join("");
    $("checkCount").textContent =
        c.validation_report.checks.filter((x) => x.status === "pass").length +
            " of 10 automated checks passed";
    $("checks").innerHTML = c.validation_report.checks
        .map((x, i) => "<li>" +
        esc(checkLabels[x.id] || studio.config.check_labels[i]) +
        ": " +
        (x.status === "pass"
            ? "passed"
            : x.status === "pending"
                ? "waiting for your choice"
                : "needs attention") +
        "</li>")
        .join("");
    $("fabricChoice").textContent =
        c.ai_package?.fabric_choice ||
            c.human_summary?.fabric_choice ||
            "This layout keeps your size, avoids stains, and uses available fabric. Single pieces are used when they fit.";
    const packageCopy = c.ai_package || c.human_summary || {};
    $("planExplanation").replaceChildren();
    const source = document.createElement('p');
    source.className = 'caption';
    source.textContent = c.ai_package?.source === 'live' && c.ai_package?.input_signature === studio.state.signature
        ? 'AI notes follow the design already checked by SISA.'
        : 'Automatic SISA notes follow the checked size and pieces.';
    $("planExplanation").append(source);
    Object.entries(explanationLabels).forEach(([key, label]) => {
        const text = packageCopy[key] || c.human_summary?.[key];
        if(!text) return;
        const title = document.createElement('h3'), paragraph = document.createElement('p');
        title.textContent = label; paragraph.textContent = text;
        $("planExplanation").append(title, paragraph);
    });
    $("designDiff").innerHTML = designDiff(c);
    $("scenarios").hidden = !new URLSearchParams(location.search).has("demo");
    $("imageStatus").textContent = !studio.consent
        ? "You chose to work without AI. You can still check the shape and pattern."
        : !studio.config.ai_image_available
            ? "Image generation is not available yet. You can still check the shape and pattern."
            : "The image follows this design's colors and layout.";
    if(studio.state.image?.input_signature === studio.state.signature) showImage(studio.state.image);
    renderStepper();
}
async function mountViewer(c) {
    if (studio.viewerRevision === c.design_revision_id && studio.viewer)
        return;
    studio.viewer?.dispose?.();
    studio.viewer = null;
    studio.viewerRevision = c.design_revision_id;
    const version = studio.epoch, revision = c.design_revision_id, fallback = diagrams.nominal(studio.data, c, studio.config), names = Object.fromEntries(studio.data.materials.map((m) => [m.id, m])), front = c.placements
        .filter((p) => p.instance_id.startsWith("FRONT"))
        .sort((a, b) => a.instance_id.localeCompare(b.instance_id))
        .map((p) => ({
        role: p.instance_id,
        hex: fabricColors.resolve(names[p.material_id].color).hex,
    })), backPlacement = c.placements.find((p) => p.instance_id === "BACK"), part = c.patterns[0], hem = part.hem_paths[0];
    $("pouchScene").innerHTML = fallback;
    try {
        // Non-literal specifier: the browser resolves it against /static/, tsc does not follow vendor JS.
        const specifier = "/static/src/preview/pouch3d.js";
        const module = await import(specifier);
        if (version !== studio.epoch || studio.viewerRevision !== revision)
            return;
        studio.viewer = module.createPouch($("pouchScene"), {
            W_mm: studio.data.request.W_mm,
            H_mm: studio.data.request.H_mm,
            variant: c.variant,
            front,
            back: {
                hex: fabricColors.resolve(names[backPlacement.material_id].color).hex,
            },
            hem_mm: studio.config.allowances.fold1_mm,
            hem_stitch_mm: part.height_mm -
                studio.config.allowances.fold1_mm -
                studio.config.allowances.fold2_mm -
                hem.points_mm[0][1],
        }, fallback);
        studio.viewer.setVisible(studio.step === 3);
        btn("dimensionsToggle").setAttribute("aria-pressed", "false");
        btn("rotationLock").setAttribute("aria-pressed", "false");
        if (studio.state.image) ensureImageModel(studio.state.image);
    }
    catch {
        $("pouchScene").innerHTML =
            "<p>3D preview is not available on this device.</p>" + fallback;
    }
}
async function imageAction(regenerate = false) {
    if (!studio.state.active_revision || studio.dirty || studio.imageController)
        return;
    const version = studio.epoch;
    clearImageModel();
    studio.state.image = null;
    studio.imageController = new AbortController();
    $("cancelImage").hidden = false;
    $("imagePreview").classList.add("skeleton");
    $("imagePreview").innerHTML =
        '<div class="empty"><p>Creating an illustration from your design…</p></div>';
    $("imageStatus").textContent =
        "Images usually take 1–3 minutes. You can still view the shape and pattern.";
    renderStepper();
    try {
        const image = await api("/ai/image", {
            regenerate,
            ai_consent: true,
            expected_generation_token: studio.state.generation_token,
        }, studio.imageController.signal);
        if (version !== studio.epoch)
            return;
        studio.state.image = image;
        showImage(image);
    }
    catch (e) {
        if (version === studio.epoch) {
            const cancelled = e.name === "AbortError";
            $("imageStatus").textContent = cancelled
                ? "Image generation was cancelled. The pattern remains available."
                : e.message || errors.AI_IMAGE_UNAVAILABLE;
            $("imagePreview").innerHTML =
                '<div class="empty"><p>' +
                    esc(cancelled
                        ? "Image cancelled. You can try again."
                        : "Could not create image yet. The shape and pattern remain available.") +
                    "</p></div>";
        }
        throw e;
    }
    finally {
        studio.imageController = null;
        $("cancelImage").hidden = true;
        $("imagePreview").classList.remove("skeleton");
        renderStepper();
    }
}
function showImage(image) {
    btn('regenerateImage').textContent = 'Regenerate image';
    const picture = document.createElement('img');
    picture.alt = 'AI illustration of your pouch design';
    picture.src = '/api/ai/image?revision_sig=' + encodeURIComponent(image.input_signature) + '&revision_id=' + encodeURIComponent(image.revision_id) + '&image_id=' + encodeURIComponent(image.image_id);
    picture.onerror = () => {
        if(studio.state?.image?.image_id !== image.image_id) return;
        $('imagePreview').innerHTML = '<div class="empty"><p>Could not open the saved image. You can generate it again.</p></div>';
        $('imageStatus').textContent = 'The design and pattern remain available.';
    };
    $('imagePreview').replaceChildren(picture);
    $('imageStatus').textContent = image.color_warning
        ? image.color_status === 'different' || /\b(berbeda|different|mismatch)\b/i.test(image.color_warning)
            ? 'The illustration colors differ from the fabric swatches. Use the pattern model and swatches as your reference.'
            : 'Illustration colors could not be fully verified; some panel colors may not be clearly visible. Use the fabric swatches in the design as your reference.'
        : 'AI illustration. Your actual fabric colors may look different.';
    ensureImageModel(image);
}
function clearImageModel() {
    studio.imageModelController?.abort();
    studio.imageModelController = null;
    studio.imageModelLoadingId = null;
    studio.imageModelImageId = null;
    studio.viewer?.restorePattern?.();
    $('modelTitle').textContent = 'SISA pattern model';
    $('modelStatus').textContent = 'Model from the pattern. Once the illustration is ready, SISA builds 3D from that image.';
    btn('dimensionsToggle').disabled = false;
    btn('dimensionsToggle').setAttribute('aria-pressed', 'false');
    btn('seamToggle').hidden = studio.state?.active_revision?.variant !== 'front_split_center';
}
async function ensureImageModel(image) {
    if (!studio.viewer || studio.dirty || image.input_signature !== studio.state.signature || image.image_id === studio.imageModelImageId || image.image_id === studio.imageModelLoadingId) return;
    clearImageModel();
    if (!studio.config.image_model_available) {
        $('modelStatus').textContent = '3D reconstruction from images is not available on this device. The pattern model is shown instead.';
        return;
    }
    const viewer = studio.viewer, version = studio.epoch, controller = new AbortController();
    const current = () => version === studio.epoch && viewer === studio.viewer && studio.state.image?.image_id === image.image_id && !controller.signal.aborted;
    studio.imageModelController = controller;
    studio.imageModelLoadingId = image.image_id;
    $('modelTitle').textContent = 'Creating 3D from illustration…';
    $('modelStatus').textContent = 'Reading illustration shape and texture on this device…';
    try {
        const model = await api('/ai/image-model', { image_id: image.image_id, expected_generation_token: studio.state.generation_token }, controller.signal);
        if (!current()) return;
        const query = new URLSearchParams({ image_id: image.image_id, model_id: model.model_id, revision_sig: model.input_signature, revision_id: model.revision_id });
        const response = await fetch('/api/ai/image-model?' + query, { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error('Could not load the 3D model. Try again.');
        const data = await response.arrayBuffer();
        if (!current()) return;
        await viewer.loadModel(data);
        if (!current()) return;
        studio.imageModelImageId = image.image_id;
        $('modelTitle').textContent = '3D model from illustration';
        $('pouchScene').setAttribute('aria-label', '3D reconstruction from AI illustration. Can be rotated. Depth and back are estimated.');
        $('modelStatus').textContent = 'Shape and texture from the illustration. Depth and back are estimated; sewing dimensions still follow the pattern.';
        btn('dimensionsToggle').disabled = true;
        btn('seamToggle').hidden = true;
    } catch (error) {
        if (current()) {
            viewer.restorePattern?.();
            $('modelTitle').textContent = 'SISA pattern model';
            $('modelStatus').textContent = 'Could not reconstruct 3D from illustration. Try regenerating the image; the pattern model is shown for now.';
        }
    } finally {
        if (studio.imageModelController === controller) {
            studio.imageModelController = null;
            studio.imageModelLoadingId = null;
        }
    }
}
function selectVisual(name, chosen = false) {
    if(chosen) $('visualCards').dataset.chosen = 'true';
    $('visualCards').dataset.visual = name;
    for(const [id, value] of [['visualImageTab','image'], ['visualModelTab','model']]) {
        btn(id).setAttribute('aria-selected', String(value === name));
        btn(id).tabIndex = value === name ? 0 : -1;
    }
}
async function reset() {
    const snapshot = clone(studio.data);
    const next = await api("/reset", {
        expected_generation_token: studio.state.generation_token,
    });
    studio.epoch++;
    clearOutputs();
    adopt(next);
    studio.dirty = false;
    clearDraft();
    studio.briefReady = false;
    $("brief").innerHTML = "";
    $("messages").innerHTML = "";
    actions.say("Starting again from the sample fabric. Tell me about the pouch you want.");
    dialog("resetDialog").close();
    showStep(1);
    const undoEpoch = studio.epoch;
    const undoToken = studio.state.generation_token;
    studio.undoReset = snapshot;
    toast("Sample fabric loaded.", false, {
        label: "Undo",
        run: async () => {
            if (studio.undoReset !== snapshot || studio.epoch !== undoEpoch || studio.state.generation_token !== undoToken || studio.conflict) {
                toast("Inputs already changed. This undo was not applied so your latest inputs are kept.", true);
                return;
            }
            studio.undoReset = null;
            setBusy(true, "Restoring previous inputs");
            try {
                invalidate();
                studio.data = clone(snapshot);
                studio.briefReady = true;
                await save();
                actions.renderFields();
                actions.renderFabrics();
                showStep(2);
            } finally { setBusy(false); }
        },
    });
}
async function init() {
    try {
        studio.config = await api("/config");
        fabricColors.setPalette(studio.config.color_palette);
        adopt(await api("/state"));
        await restoreDraft();
        studio.briefReady = Boolean(studio.state.active_revision || studio.state.candidate || studio.state.chat_history?.length);
        field("aiEnabled").checked = studio.consent === true;
        field('activityEnabled').checked = Boolean(studio.state.telemetry_enabled);
        $("scopeText").textContent =
            "Width " +
                cm(studio.config.w_range_mm[0]) +
                "–" +
                cm(studio.config.w_range_mm[1]) +
                " cm · height " +
                cm(studio.config.h_range_mm[0]) +
                "–" +
                cm(studio.config.h_range_mm[1]) +
                " cm · up to " +
                studio.config.max_materials +
                " non-stretch woven fabrics · no zippers, closures, or lining yet.";
        field("widthCm").min = String(studio.config.w_range_mm[0] / 10);
        field("widthCm").max = String(studio.config.w_range_mm[1] / 10);
        field("heightCm").min = String(studio.config.h_range_mm[0] / 10);
        field("heightCm").max = String(studio.config.h_range_mm[1] / 10);
        actions.renderFields();
        updateAiStatus();
        if (studio.state.chat_history?.length) {
            $("messages").innerHTML = "";
            studio.state.chat_history.forEach((m) => actions.say(m.text, m.role));
        }
        setBusy(false);
        if (studio.state.active_revision)
            renderDesign();
        restoreView();
        setupTour();
        recordEvent('start');
    }
    catch (e) {
        setBusy(false);
        toast(e.message, true, { label: "Try again", run: () => init() });
    }
}
Object.assign(actions, { setBusy, safe, adopt, renderStepper, showStep, clearOutputs, invalidate, save, permission, chooseConsent, updateAiStatus, startManual, makePlan, renderDesign, mountViewer, imageAction, reset, init, designDiff, say, showBrief, applyBrief, chat, sendStory, chatDesign, renderFields, renderFabrics, openFabric, wizardRing, drawWizard, saveFabric, drawSize, pdf, renderPdfPreview });
Object.assign(actions, { reportConflict, recordEvent, showImage, selectVisual });
setupProjects();
for(const [id, name] of [['visualImageTab','image'], ['visualModelTab','model']]) {
    btn(id).onclick = () => { $('visualCards').dataset.chosen = 'true'; selectVisual(name); };
    btn(id).onkeydown = event => {
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 'image' : event.key === 'End' ? 'model' : name === 'image' ? 'model' : 'image';
        $('visualCards').dataset.chosen = 'true'; selectVisual(next);
        btn(next === 'image' ? 'visualImageTab' : 'visualModelTab').focus();
    };
}
document
    .querySelectorAll("[data-step]")
    .forEach((n) => n.addEventListener("click", () => showStep(Number(n.getAttribute("data-step")))));
document
    .querySelectorAll("[data-close]")
    .forEach((n) => n.addEventListener("click", () => dialog(n.getAttribute("data-close")).close()));
document.querySelectorAll("[data-example]").forEach((n) => n.addEventListener("click", () => {
    /** @type {HTMLTextAreaElement} */ ($("storyInput")).value =
        n.getAttribute("data-example");
    $("storyInput").focus();
}));
document.querySelector(".brand").addEventListener("click", (e) => {
    e.preventDefault();
    showStep(1);
});
btn("startStory").onclick = () => {
    $("storyInput").focus();
    $("storyCard").scrollIntoView({ block: "center" });
};
btn("helpOpen").onclick = () => dialog("helpDialog").showModal();
btn("settingsOpen").onclick = () => dialog("settingsDialog").showModal();
btn("consentYes").onclick = () => chooseConsent(true);
btn("consentNo").onclick = () => chooseConsent(false);
dialog("consentDialog").addEventListener("cancel", (e) => {
    e.preventDefault();
    chooseConsent(false);
});
field("aiEnabled").onchange = () => {
    studio.consent = field("aiEnabled").checked;
    try {
        localStorage.setItem("sisa.ai", String(studio.consent));
    }
    catch { }
    updateAiStatus();
};
$("composer").onsubmit = (e) => {
    e.preventDefault();
    safe(actions.sendStory);
};
btn("manualStart").onclick = startManual;
btn("confirmFabric").onclick = () => safe(() => makePlan());
btn("demoStart").onclick = () => safe(async () => {
    await permission();
    studio.briefReady = true;
    await makePlan();
});
btn("cancelWork").onclick = () => {
    studio.activeController?.abort();
    studio.imageController?.abort();
    studio.pdfController?.abort();
    invalidate();
    setBusy(false);
    toast("Request cancelled. Your inputs are kept.");
};
btn("cancelImage").onclick = () => {
    clearImageModel();
    studio.imageController?.abort();
    api("/ai/image/cancel", {
        expected_generation_token: studio.state.generation_token,
    }).catch(() => { });
    $("imageStatus").textContent =
        "Image generation was cancelled. The pattern remains available.";
};
btn("toGuide").onclick = () => safe(async () => {
    showStep(4);
    await actions.pdf("guide", true);
});
btn("downloadGuide").onclick = () => safe(() => actions.pdf("guide", true));
btn("downloadTechnical").onclick = () => safe(() => actions.pdf("technical", true));
btn("printGuide").onclick = () => safe(async () => {
    const current = await api("/state");
    if (!studio.pdfUrl ||
        studio.pdfSignature !== current.signature || studio.dirty ||
        current.active_revision?.design_revision_id !==
            studio.state.active_revision?.design_revision_id) {
        clearOutputs();
        adopt(current);
        throw new Error(errors.STALE_REVISION);
    }
    const frame = /** @type {HTMLIFrameElement} */ ($("pdfPreview").querySelector("iframe"));
    frame?.contentWindow?.focus();
    frame?.contentWindow?.print();
});
btn("regenerateImage").onclick = () => safe(() => imageAction(true), false);
btn("reviseDesign").onclick = () => {
    showStep(1);
    $("storyInput").focus();
};
document.querySelectorAll("[data-revision]").forEach((n) => n.addEventListener("click", () => {
    showStep(1);
    /** @type {HTMLTextAreaElement} */ ($("storyInput")).value =
        n.getAttribute("data-revision");
    $("storyInput").focus();
}));
btn("layoutWhy").onclick = () => {
    $("fabricChoice").hidden = !$("fabricChoice").hidden;
};
btn("newStart").onclick = () => dialog("resetDialog").showModal();
btn("resetYes").onclick = () => safe(reset);
btn("addFabric").onclick = () => actions.openFabric(-1);
document.querySelectorAll("[data-shape]").forEach((n) => n.addEventListener("click", () => {
    studio.wizard.shape = n.getAttribute("data-shape");
    actions.drawWizard();
}));
btn("wizardNext").onclick = () => {
    studio.wizard.stage = Math.min(3, studio.wizard.stage + 1);
    actions.drawWizard();
};
btn("wizardBack").onclick = () => {
    studio.wizard.stage = Math.max(1, studio.wizard.stage - 1);
    actions.drawWizard();
};
btn("copyLastFabric").onclick = () => {
    const m = studio.data.materials.at(-1), b = diagrams.bounds(m.outer_mm);
    field("fabricWidth").value = String(b.w / 10);
    field("fabricHeight").value = String(b.h / 10);
    field("fabricColor").value = fabricColors.resolve(m.color).hex;
    field("fabricColorName").value = fabricColors.displayName(m.color);
    studio.wizard.shape = m.outer_mm.length === 6 ? "L" : "rectangle";
    studio.wizard.stage = 2;
    actions.drawWizard();
};
$("fabricForm").onsubmit = (e) => safe(() => actions.saveFabric(e));
[
    "fabricWidth",
    "fabricHeight",
    "notchWidth",
    "notchHeight",
    "fabricColor",
].forEach((id) => (field(id).oninput = actions.drawWizard));
btn("addDefect").onclick = () => {
    if (studio.wizard.defects.length >= studio.config.max_defects_per_material) {
        toast("Maximum two stain or hole marks per fabric.", true);
        return;
    }
    const b = diagrams.bounds(actions.wizardRing());
    studio.wizard.defects.push({ x: b.w / 4, y: b.h / 4, w: 10, h: 10 });
    actions.drawWizard();
};
for (const id of [
    "widthCm",
    "heightCm",
    "frontColor",
    "priority",
    "allowSplit",
    "allowMixed",
])
    $(id).addEventListener(id === "priority" || id.startsWith("allow") ? "change" : "input", () => {
        invalidate();
        studio.data.request.W_mm = Number(field("widthCm").value) * 10;
        studio.data.request.H_mm = Number(field("heightCm").value) * 10;
        studio.data.request.preferences.front_color =
            field("frontColor").value.trim() || null;
        studio.data.request.preferences.fewer_seams_first =
            /** @type {HTMLSelectElement} */ ($("priority")).value === "true";
        studio.data.request.max_front_splits = field("allowSplit").checked ? 1 : 0;
        studio.data.request.allow_mixed = field("allowMixed").checked;
        studio.data.request.confirmed = false;
        const r = studio.data.request;
        const ok = Number.isFinite(r.W_mm) &&
            Number.isFinite(r.H_mm) &&
            r.W_mm >= studio.config.w_range_mm[0] &&
            r.W_mm <= studio.config.w_range_mm[1] &&
            r.H_mm >= studio.config.h_range_mm[0] &&
            r.H_mm <= studio.config.h_range_mm[1];
        $("sizeError").textContent = ok
            ? ""
            : "Width must be " +
                cm(studio.config.w_range_mm[0]) +
                "–" +
                cm(studio.config.w_range_mm[1]) +
                " cm; height " +
                cm(studio.config.h_range_mm[0]) +
                "–" +
                cm(studio.config.h_range_mm[1]) +
                " cm.";
        btn("confirmFabric").disabled = !ok;
        actions.drawSize();
    });
for (const [id, index] of [
    ["removeExampleLarge", 0],
    ["removeExampleBeige", 3],
])
    btn(id).onclick = () => safe(async () => {
        invalidate();
        studio.data.materials[index].available = false;
        studio.data.materials[index].confirmed = false;
        await save();
        await makePlan(false);
    });
setBusy(true, "Preparing your design studio");
init();
document.querySelectorAll("[data-view]").forEach((n) => n.addEventListener("click", () => {
    studio.viewer?.preset(n.getAttribute("data-view"));
    document
        .querySelectorAll("[data-view]")
        .forEach((b) => b.setAttribute("aria-pressed", String(b === n)));
}));
btn("dimensionsToggle").onclick = () => {
    const value = btn("dimensionsToggle").getAttribute("aria-pressed") !== "true";
    btn("dimensionsToggle").setAttribute("aria-pressed", String(value));
    studio.viewer?.dimensions(value);
};
btn("rotationLock").onclick = () => {
    const value = btn("rotationLock").getAttribute("aria-pressed") !== "true";
    btn("rotationLock").setAttribute("aria-pressed", String(value));
    btn("rotationLock").textContent = value ? "Unlock rotation" : "Lock rotation";
    studio.viewer?.lock(value);
};
btn("refreshAiConfig").onclick = () => safe(async () => {
    studio.config = await api("/config");
    fabricColors.setPalette(studio.config.color_palette);
    updateAiStatus();
    renderStepper();
    if (studio.state.active_revision)
        renderDesign();
    toast(studio.config.ai_available
        ? "AI assistant is available."
        : "AI assistant is not available yet. You can still enter measurements manually.", !studio.config.ai_available);
});
btn("seamToggle").onclick = () => {
    const value = btn("seamToggle").getAttribute("aria-pressed") !== "true";
    btn("seamToggle").setAttribute("aria-pressed", String(value));
    btn("seamToggle").textContent = value
        ? "Hide seam labels"
        : "Show seam labels";
    studio.viewer?.seam(value);
};
document.querySelectorAll("[data-number]").forEach((n) => n.addEventListener("click", () => {
    const input = field(n.getAttribute("data-number")), value = Number(input.value) + Number(n.getAttribute("data-delta")) * 0.1;
    input.value = String(Math.min(Number(input.max), Math.max(Number(input.min), Number(value.toFixed(1)))));
    input.dispatchEvent(new Event("input", { bubbles: true }));
}));
function designDiff(c) {
    if (!studio.previous)
        return "<p>This is your first design for these inputs.</p>";
    const old = studio.previous.revision, oldData = studio.previous.data, names = Object.fromEntries(studio.data.materials.map((m) => [m.id, m])), oldNames = Object.fromEntries(oldData.materials.map((m) => [m.id, m])), used = new Set(c.placements.map((p) => p.material_id));
    const retired = [...new Set(old.placements.map((p) => p.material_id))].filter((id) => !used.has(id));
    const fronts = (rev, mats) => [
        ...new Set(rev.placements
            .filter((p) => p.instance_id.startsWith("FRONT"))
            .map((p) => fabricColors.displayName(mats[p.material_id].color))),
    ].join(" + ");
    return ((retired.length
        ? "<p>" +
            retired.map((id) => esc(oldNames[id].name)).join(" and ") +
            (retired.length > 1 ? " are no longer used.</p>" : " is no longer used.</p>")
        : "") +
        "<p>Size " +
        (oldData.request.W_mm === studio.data.request.W_mm &&
            oldData.request.H_mm === studio.data.request.H_mm
            ? "unchanged at "
            : cm(oldData.request.W_mm) + " × " + cm(oldData.request.H_mm) + " → ") +
        cm(studio.data.request.W_mm) +
        " × " +
        cm(studio.data.request.H_mm) +
        " cm.</p><p>Front " +
        (c.extra_seams
            ? "now uses two pieces with one seam."
            : "uses one whole piece.") +
        "</p><p>Front color: " +
        esc(fronts(old, oldNames)) +
        " → " +
        esc(fronts(c, names)) +
        ".</p>");
}

