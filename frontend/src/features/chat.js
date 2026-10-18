import { studio, actions } from '../core/state.js';
import { $, field, btn, dialog, clone, esc, cm, toast } from '../core/dom.js';
import { diagrams } from '../preview/diagrams.js';
import { fabricColors } from '../config/colors.js';
import { errors } from '../config/copy.en.js';
import { api } from '../core/api.js';
// Backend step events and deterministic warning labels are sent in Indonesian;
// these known values are mapped for display. Unknown text and arbitrary prose
// fall through unchanged.
const progressLabels = {
    "Menyiapkan ilustrasi sesuai rancanganmu": "Preparing an illustration for your design",
    "Membaca ceritamu": "Reading your story",
    "Mencocokkan dengan kainmu": "Matching against your fabric",
    "Mencari susunan potongan": "Looking for a piece layout",
    "Mengecek 10 hal": "Checking 10 points",
};
const questionLabels = {
    "Kamu ingin pouch ukuran berapa?": "What size pouch would you like?",
};
const warningText = (w, understood) => {
    if (w.code === "OUT_OF_RANGE" && ["W_mm", "H_mm"].includes(w.field)) {
        const value = understood?.find((u) => u.field === w.field)?.value;
        const range = studio.config[w.field === "W_mm" ? "w_range_mm" : "h_range_mm"];
        const label = w.field === "W_mm" ? "width" : "height";
        return (Number.isFinite(value) ? "The " + label + " you asked for is " + cm(value) + " cm. " : "The requested " + label + " is outside the supported range. ") +
            (range ? "Supported range: " + cm(range[0]) + "–" + cm(range[1]) + " cm. " : "") + "Your details have not been changed.";
    }
    if (w.code === "COLOR_NOT_IN_STOCK") {
        const desired = (w.actions || []).find((a) => a.id === "add");
        return (desired ? "The requested color, " + fabricColors.displayName(desired.value) + ", is not in stock. " : "The requested color is not in stock. ") +
            "Pick an available fabric, or add your own fabric in that color. Your choice will not be replaced automatically.";
    }
    return w.text;
};
const warningLabel = (w, a) => {
    if (w.code === "COLOR_NOT_IN_STOCK") {
        switch (a.id) {
            case "nearest": return "Use nearest color (" + fabricColors.displayName(a.value) + ")";
            case "choose": return "Use " + fabricColors.displayName(a.value) + " fabric";
            case "add": return "Add " + fabricColors.displayName(a.value) + " fabric";
            case "ignore": return "Ignore the color preference";
        }
    }
    if (w.code === "OUT_OF_RANGE" && ["W_mm", "H_mm"].includes(w.field) && a.id === "range")
        return "Use " + (w.field === "W_mm" ? "width" : "height") + " " + cm(a.value) + " cm";
    return a.label;
};
export function say(text, role = "assistant") {
    const el = document.createElement("div");
    el.className = "bubble " + role;
    el.textContent = text;
    $("messages").append(el);
    while ($("messages").children.length > 20)
        $("messages").firstElementChild.remove();
    $("messages").scrollTop = $("messages").scrollHeight;
}

export function showBrief(result) {
    $('colorReplacementChoices')?.remove();
    if (result.status === "unavailable" || result.trace?.mode === "fallback") {
        $("brief").innerHTML =
            '<div class="callout warning"><p><strong>AI is currently unreachable, so no new proposal was made.</strong></p><p>Your inputs have not changed.</p><div class="actions"><button id="retryStory">Try again</button><button id="manualFallback">Enter manually</button></div></div>';
        btn("retryStory").onclick = () => actions.safe(sendStory);
        btn("manualFallback").onclick = actions.startManual;
        return;
    }
    say(result.reply || "Check your requirements below.");
    if (result.status === "rejected")
        return;
    const labels = {
        W_mm: "Width",
        H_mm: "Height",
        front_color: "Front color",
        max_front_splits: "Front seams",
        allow_mixed: "Color mix",
        fewer_seams_first: "Priority",
    };
    const display = (f, v) => ["W_mm", "H_mm"].includes(f)
        ? cm(v) + " cm"
        : f === "max_front_splits"
            ? v
                ? "allow one"
                : "no seams"
            : f === "allow_mixed"
                ? v
                    ? "allow"
                    : "single color"
                : f === "fewer_seams_first"
                    ? v
                        ? "neater"
                        : "save fabric"
                    : f === 'front_color'
                        ? (v ? fabricColors.displayName(v) : 'any')
                        : String(v);
    const items = (result.understood || [])
        .map((u) => '<li><button class="text-button" data-brief-field="' +
        esc(u.field) +
        '">' +
        esc(labels[u.field] || "Requirement") +
        ": " +
        esc(display(u.field, u.value)) +
        '</button><span class="evidence">From your story: “' +
        esc(u.from) +
        "”</span></li>")
        .join("");
    const request = result.proposal || studio.data.request;
    $("brief").innerHTML =
        '<div class="brief"><h3>What AI understood</h3>' +
            (items ? "<ul>" + items + "</ul>" : "<p>No changes to your inputs yet.</p>") +
            "<p>Input size: " +
            cm(request.W_mm) +
            " × " +
            cm(request.H_mm) +
            " cm · front: " +
            esc(request.preferences.front_color ? fabricColors.displayName(request.preferences.front_color) : "any") +
            "</p>" +
            (result.assumptions || [])
                .map((a) => "<p>Please confirm: " + esc(a.note) + "</p>")
                .join("") +
            "</div>" +
            (result.warnings || [])
                .map((w, i) => '<div class="callout warning"><p>' +
                esc(warningText(w, result.understood)) +
                '</p><div class="actions">' +
                (w.actions || [])
                    .map((a, j) => '<button data-warning="' +
                    i +
                    '" data-action="' +
                    j +
                    '">' +
                    esc(warningLabel(w, a)) +
                    "</button>")
                    .join("") +
                "</div></div>")
                .join("") +
            (result.questions || [])
                .map((q) => '<div class="callout"><p>' +
                esc(questionLabels[q.text] || q.text) +
                '</p><div class="chips">' +
                q.options
                    .map((o) => '<button data-answer="' + esc(o) + '">' + esc(o) + "</button>")
                    .join("") +
                "</div></div>")
                .join("") +
            (result.proposal && !result.warnings?.length
                ? '<div class="actions"><button id="useBrief" class="primary">Use this proposal</button><button id="checkMyFabric" class="secondary">Check fabric myself</button></div>'
                : "");
    $("brief")
        .querySelectorAll("[data-brief-field]")
        .forEach((n) => n.addEventListener("click", () => {
        studio.briefReady = true;
        actions.showStep(2);
        document.querySelector(".manual-fields").setAttribute("open", "");
        const id = {
            W_mm: "widthCm",
            H_mm: "heightCm",
            front_color: "frontColor",
            max_front_splits: "allowSplit",
            allow_mixed: "allowMixed",
            fewer_seams_first: "priority",
        }[n.getAttribute("data-brief-field")];
        $(id)?.focus();
    }));
    $("brief")
        .querySelectorAll("[data-answer]")
        .forEach((n) => n.addEventListener("click", () => {
        /** @type {HTMLTextAreaElement} */ ($("storyInput")).value +=
            "\n" + n.getAttribute("data-answer");
        $("storyInput").focus();
    }));
    $("brief")
        .querySelectorAll("[data-warning]")
        .forEach((n) => n.addEventListener("click", () => actions.safe(async () => {
        const warning = result.warnings[Number(n.getAttribute("data-warning"))], action = warning.actions[Number(n.getAttribute("data-action"))];
        if (action.id === "add") {
            studio.briefReady = true;
            actions.showStep(2);
            if (studio.data.materials.length >= studio.config.max_materials) {
                toast("You already have four fabrics. Replace or remove one first.", true);
                const choice = document.createElement('div');
                $('colorReplacementChoices')?.remove();
                choice.id = 'colorReplacementChoices';
                choice.className = 'callout';
                choice.setAttribute('role', 'group');
                choice.setAttribute('aria-label', 'Choose the fabric to replace');
                const explanation = document.createElement('p');
                explanation.textContent = `All four fabric slots are full. Choose which fabric to replace with ${fabricColors.displayName(action.value)}. The old fabric is replaced only after you save.`;
                choice.append(explanation);
                studio.data.materials.forEach((material, index) => {
                    const button = document.createElement('button');
                    button.textContent = `Replace ${material.name || fabricColors.displayName(material.color)}`;
                    button.onclick = () => actions.safe(async () => {
                        await applyBrief(result);
                        actions.openFabric(index, action.value);
                        choice.remove();
                    });
                    choice.append(button);
                });
                $('fabricCards').before(choice);
                choice.scrollIntoView({ block: 'center' });
            } else {
                await applyBrief(result);
                actions.openFabric(-1, action.value);
            }
            return;
        }
        const next = clone(result);
        if (!next.proposal)
            next.proposal = clone(studio.data.request);
        if (warning.field === "front_color")
            next.proposal.preferences.front_color = action.value;
        else
            next.proposal[warning.field] = action.value;
        next.warnings.splice(Number(n.getAttribute("data-warning")), 1);
        next.understood = next.understood.filter((u) => u.field !== warning.field);
        await applyBrief(next);
        showBrief(next);
    })));
    if ($("useBrief"))
        btn("useBrief").onclick = () => actions.safe(async () => {
            if (studio.data.materials.some((m) => m.available &&
                !m.confirmed &&
                m.measurement_source !== "synthetic_fixture")) {
                actions.showStep(2);
                return;
            }
            await actions.makePlan();
        });
    if ($("checkMyFabric"))
        btn("checkMyFabric").onclick = () => actions.showStep(2);
}

export async function applyBrief(result) {
    if (!result.proposal)
        return;
    const before = clone(studio.data.request);
    actions.invalidate();
    studio.data.request = clone(result.proposal);
    studio.briefReady = true;
    await actions.save();
    actions.renderFields();
    toast("Your requirements have been added to the form. You can undo this.", false, {
        label: "Undo",
        run: () => actions.safe(async () => {
            actions.invalidate();
            studio.data.request = before;
            await actions.save();
            actions.renderFields();
        }),
    });
}

export async function chat(text, replan, signal) {
    const response = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            text,
            replan,
            ai_consent: true,
            expected_generation_token: studio.state.generation_token,
        }),
        signal,
    });
    if (!response.ok) {
        const value = await response.json();
        throw new Error(errors[value.detail?.code] || "The chat could not be completed. Try again.");
    }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = "", result = null, planned = null, image = null;
    const messages = [];
    while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        let pos;
        while ((pos = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, pos);
            buffer = buffer.slice(pos + 2);
            const name = /event: ([^\n]+)/.exec(block)?.[1], raw = /data: (.*)/.exec(block)?.[1];
            if (!raw)
                continue;
            const event = JSON.parse(raw);
            if (name === "step")
                $("progressText").textContent = progressLabels[event.text] || event.text;
            if (name === "brief")
                result = event;
            if(name === 'message') messages.push(event);
            if(name === 'image') image = event;
            if (name === "design_ready")
                planned = event;
            if (name === "error")
                throw new Error(errors[event.code] ||
                    "The design could not be updated. Check your details.");
        }
        if (done)
            break;
    }
    return { result, planned, messages, image };
}

export async function sendStory() {
    await actions.permission();
    if (!studio.consent) {
        actions.startManual();
        return;
    }
    await actions.save();
    const version = studio.epoch, text = /** @type {HTMLTextAreaElement} */ ($("storyInput")).value.trim(), replan = Boolean(studio.state.active_revision);
    say(text || "Help me choose a size.", "user");
    actions.recordEvent('story_sent');
    studio.activeController = new AbortController();
    actions.setBusy(true, "Reading your story");
    let response;
    try {
        response = await chat(text, replan, studio.activeController.signal);
    }
    catch (e) {
        if (e.name === "AbortError" ||
            e.message === errors.CHAT_LIMIT ||
            e.message === errors.STALE_REVISION)
            throw e;
        response = {
            result: {
                status: "unavailable",
                proposal: null,
                trace: { mode: "fallback" },
            },
        };
    }
    if (version !== studio.epoch)
        return;
    const { result, planned, messages = [], image } = response;
    if (planned) {
        actions.clearOutputs();
        actions.adopt(planned);
        studio.dirty = false;
        studio.briefReady = true;
        say("Your design has been updated. Check what changed.");
        actions.showStep(3);
        chatDesign();
        actions.recordEvent('design_ready');
        if (studio.consent && studio.config.ai_image_available && studio.state.active_revision)
            actions.imageAction().catch((e) => toast(e.message, true));
    }
    else if(result) {
        await applyBrief(result);
        showBrief(result);
    }
    else {
        messages.forEach(message => say(message.text || 'The design and pattern are unchanged.'));
        if(image && image.input_signature === studio.state.signature && image.revision_id === studio.state.active_revision?.design_revision_id) {
            studio.state.image = image;
            actions.showImage(image);
            actions.showStep(3);
            actions.selectVisual('image', true);
        }
    }
    studio.activeController = null;
}

export function chatDesign() {
    const c = studio.state.active_revision || studio.state.candidate;
    if (!c)
        return;
    const bubble = document.createElement("div");
    bubble.className = "bubble assistant design-result";
    bubble.innerHTML =
        "<p><strong>Your design</strong></p>" +
            diagrams.nominal(studio.data, c, studio.config) +
            "<p>" +
            esc(c.human_summary?.summary ||
                "See this design's measurements, colors, and pieces.") +
            "</p><button>View design</button>";
    bubble.querySelector("button").onclick = () => actions.showStep(3);
    $("messages").append(bubble);
    while ($("messages").children.length > 20)
        $("messages").firstElementChild.remove();
    $("messages").scrollTop = $("messages").scrollHeight;
}
