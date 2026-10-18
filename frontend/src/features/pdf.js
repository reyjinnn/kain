import { studio, actions } from '../core/state.js';
import { $, field, btn, dialog, clone, esc, cm, toast } from '../core/dom.js';
import { diagrams } from '../preview/diagrams.js';
import { fabricColors } from '../config/colors.js';
import { errors } from '../config/copy.en.js';
import { api } from '../core/api.js';
let previewController = null, previewUrl = null;
export function clearPdfPreview() {
    previewController?.abort();
    previewController = null;
    if(previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
}
export async function pdf(kind = "guide", download = false) {
    if (!studio.state.active_revision || studio.dirty)
        return;
    const version = studio.epoch, sig = studio.state.signature, revision = studio.state.active_revision.design_revision_id;
    await studio.mutation;
    studio.pdfController = new AbortController();
    actions.setBusy(true, "Preparing the guide and rechecking the pattern");
    const response = await fetch("/api/export-pdf?revision_sig=" +
        encodeURIComponent(sig) +
        "&kind=" +
        kind +
        "&mode=paper_test", { signal: studio.pdfController.signal, cache: "no-store" });
    if (!response.ok) {
        const value = await response.json();
        throw new Error(errors[value.detail?.code] ||
            "The guide could not be generated. Try again.");
    }
    if(response.headers.get('X-SISA-Revision') !== revision) {
        await actions.reportConflict();
        throw new Error(errors.STALE_REVISION);
    }
    const blob = await response.blob();
    if (version !== studio.epoch || studio.state.signature !== sig || studio.state.active_revision?.design_revision_id !== revision)
        return;
    const url = URL.createObjectURL(blob);
    if (kind === "guide") {
        if (studio.pdfUrl)
            URL.revokeObjectURL(studio.pdfUrl);
        studio.pdfUrl = url;
        studio.pdfSignature = sig;
        renderPdfPreview(sig, url, revision);
        await new Promise(requestAnimationFrame);
    }
    if (download) {
        if (version !== studio.epoch || studio.state.signature !== sig)
            return;
        const a = document.createElement("a");
        a.href = url;
        const header = response.headers.get("Content-Disposition") || "";
        a.download = /filename="([^"]+)"/.exec(header)?.[1] || "SISA-guide.pdf";
        a.click();
        actions.recordEvent('download');
        if (kind !== "guide")
            setTimeout(() => URL.revokeObjectURL(url), 30000);
    }
    studio.pdfController = null;
    actions.renderStepper();
}

export function renderPdfPreview(sig, url, revision = studio.state.active_revision?.design_revision_id) {
    let page = 1;
    $("pdfPreview").innerHTML =
        '<div class="pdf-sheet" tabindex="0" aria-label="Guide page preview"><img class="pdf-page-image" alt="First page of the guide"></div><div class="pdf-controls"><button id="previewPrev" aria-label="Previous page">Previous</button><span id="previewPage">Page 1</span><button id="previewNext" aria-label="Next page">Next</button><button id="previewZoom" aria-pressed="false">Zoom in</button></div><iframe hidden title="Complete PDF for printing" src="' +
            url +
            '"></iframe>';
    const image = /** @type {HTMLImageElement} */ ($("pdfPreview").querySelector("img"));
    const update = async () => {
        previewController?.abort();
        const controller = new AbortController();
        previewController = controller;
        const version = studio.epoch, selected = page;
        image.alt = "Page " + page + " of the pouch guide";
        $("previewPage").textContent = "Page " + page + " · first 3 pages";
        btn("previewPrev").disabled = page === 1;
        btn("previewNext").disabled = page === 3;
        try {
            const response = await fetch('/api/export-preview?revision_sig=' + encodeURIComponent(sig) + '&page=' + selected, {signal:controller.signal,cache:'no-store'});
            if(!response.ok) throw new Error('The guide page could not be opened. Try downloading the PDF.');
            if(response.headers.get('X-SISA-Revision') !== revision) {
                await actions.reportConflict();
                throw new Error(errors.STALE_REVISION);
            }
            const blob = await response.blob();
            if(version !== studio.epoch || studio.state.active_revision?.design_revision_id !== revision || selected !== page) return;
            if(previewUrl) URL.revokeObjectURL(previewUrl);
            previewUrl = URL.createObjectURL(blob);
            image.src = previewUrl;
        } catch(error) {
            if(error.name !== 'AbortError' && version === studio.epoch) {
                $('previewPage').textContent = error.message;
                toast(error.message, true);
            }
        }
    };
    btn("previewPrev").onclick = () => {
        page = Math.max(1, page - 1);
        update();
    };
    btn("previewNext").onclick = () => {
        page = Math.min(3, page + 1);
        update();
    };
    btn("previewZoom").onclick = () => {
        const zoom = btn("previewZoom").getAttribute("aria-pressed") !== "true";
        btn("previewZoom").setAttribute("aria-pressed", String(zoom));
        btn("previewZoom").textContent = zoom ? "Zoom out" : "Zoom in";
        $("pdfPreview").querySelector(".pdf-sheet").classList.toggle("zoom", zoom);
    };
    update();
}
