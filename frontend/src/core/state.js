// One state object belongs to this browser tab. Server mutations use generation guards.
/** @type {any} */
export const studio = {
state: null,
data: null,
config: null,
step: 1,
epoch: 0,
dirty: false,
briefReady: false,
busy: false,
consent: null,
consentResolve: null,
activeController: null,
imageController: null,
pdfController: null,
pdfUrl: null,
pdfSignature: null,
previous: null,
undoReset: null,
mutation: Promise.resolve(),
viewer: null,
viewerRevision: null,
wizard: { stage: 1, shape: "rectangle", index: -1, defects: [] },
conflict: false,
savingDraft: null,
pendingMutations: 0
};
/** @type {Record<string, (...args: any[]) => any>} */
export const actions = {};
