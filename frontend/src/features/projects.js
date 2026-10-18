import { studio, actions } from '../core/state.js';
import { api } from '../core/api.js';
import { $, btn, field, dialog, toast } from '../core/dom.js';

let autosaveTimer = null;
let channel = null;
let restoring = false;
let deleting = null;
let draftIdentity = null;
const tabId = crypto.randomUUID();
const setPagesInert = value => { for(let i=1;i<=4;i++) $('step'+i).inert = value; };

export function savedStatus(next = studio.state) {
  const time = next?.persistence?.autosaved_at;
  $('saveStatus').textContent = studio.conflict
    ? 'Changes in another tab'
    : studio.dirty
      ? 'Saving your details…'
      : time
        ? 'Autosaved · ' + new Date(time * 1000).toLocaleTimeString('en', {hour:'2-digit',minute:'2-digit'})
        : 'Your details are saved automatically';
  btn('projectSave').disabled = !studio.state || studio.busy || studio.conflict;
  btn('projectOpen').disabled = !studio.state || studio.busy;
}

export function rememberStep() {
  try {
    sessionStorage.setItem('sisa.view', JSON.stringify({step:studio.step, brief:studio.briefReady}));
  } catch {}
}

export function restoreView() {
  try {
    const view = JSON.parse(sessionStorage.getItem('sisa.view') || '{}');
    studio.briefReady ||= Boolean(view.brief || studio.state.chat_history?.length || studio.state.data.request.confirmed);
    actions.renderStepper();
    const target = studio.state.active_revision ? (view.step === 4 ? 4 : 3) : view.step === 2 && studio.briefReady ? 2 : 1;
    actions.showStep(target, false);
    if(target === 4) actions.safe(() => actions.pdf(), false);
  } catch {}
}

export function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  queueMicrotask(rememberDraft);
  savedStatus();
  autosaveTimer = setTimeout(async () => {
    if(studio.conflict || !studio.dirty) return;
    try { await actions.save(); savedStatus(); }
    catch(error) {
      if(error.name !== 'AbortError' && !studio.conflict)
        $('saveStatus').textContent = 'Not saved · check your details';
    }
  }, 650);
}

const identity = next => next ? JSON.stringify([
  next.session_id, next.generation_token, next.job_token, next.signature,
  next.active_revision?.design_revision_id, next.candidate?.design_revision_id,
]) : null;

export function beginDraft() {
  if(!studio.dirty) draftIdentity = identity(studio.state);
}

export function rememberDraft() {
  if(!studio.state || !studio.dirty) return;
  try {
    sessionStorage.setItem('sisa.draft', JSON.stringify({
      savedAt:Date.now(), session:studio.state.session_id,
      base:draftIdentity || identity(studio.state), data:studio.data,
    }));
  } catch {}
}

export function clearDraft() {
  draftIdentity = null;
  try { sessionStorage.removeItem('sisa.draft'); } catch {}
}

export async function restoreDraft() {
  let draft;
  try { draft = JSON.parse(sessionStorage.getItem('sisa.draft') || 'null'); } catch { clearDraft(); return; }
  if(!draft?.data || !draft?.base || Date.now() - draft.savedAt > 30 * 86400000) { clearDraft(); return; }
  if(JSON.stringify(draft.data) === JSON.stringify(studio.state.data)) { clearDraft(); return; }
  const current = identity(studio.state);
  studio.data = draft.data;
  studio.briefReady = true;
  if(current === draft.base) {
    actions.invalidate();
    await actions.save();
    toast('Your latest details have been restored.');
    return;
  }
  draftIdentity = draft.base;
  studio.dirty = true;
  studio.conflict = true;
  actions.clearOutputs();
  $('conflictTitle').textContent = 'You have unsaved details.';
  $('conflictMessage').textContent = 'Restore the details from this tab, or load your latest saved work.';
  $('conflictHint').textContent = 'Using the details from this tab replaces the saved details. The old design will need another review.';
  $('sessionConflict').hidden = false;
  setPagesInert(true);
  savedStatus();
}

export function announceChange(before, next) {
  savedStatus(next);
  if(studio.dirty && !studio.conflict) { draftIdentity = identity(next); rememberDraft(); }
  if(!restoring && before && identity(before) !== identity(next))
    channel?.postMessage({tab:tabId, identity:identity(next)});
}

export async function reportConflict() {
  const remote = await api('/state');
  if(!studio.state || identity(remote) === identity(studio.state)) return;
  studio.conflict = true;
  clearTimeout(autosaveTimer);
  studio.epoch++;
  studio.activeController?.abort();
  studio.imageController?.abort();
  studio.pdfController?.abort();
  actions.clearOutputs();
  $('sessionConflict').hidden = false;
  $('conflictTitle').textContent = 'This work changed in another tab.';
  $('conflictMessage').textContent = 'Choose which details to use before continuing.';
  $('conflictHint').textContent = 'The second choice replaces the details in the other tab. The design will need to be created again.';
  setPagesInert(true);
  actions.renderStepper();
  savedStatus();
}

async function loadLatest(keepMine) {
  const mine = structuredClone(studio.data);
  const wasReady = studio.briefReady, priorSession = studio.state.session_id;
  await studio.mutation;
  const remote = await api('/state');
  studio.epoch++;
  actions.clearOutputs();
  studio.conflict = false;
  restoring = true;
  actions.adopt(remote);
  restoring = false;
  $('sessionConflict').hidden = true;
  setPagesInert(false);
  if(keepMine) {
    studio.data = mine;
    actions.invalidate();
    await actions.save();
    toast('Using the details from this tab. The design needs to be created again.');
  } else {
    studio.dirty = false;
    clearDraft();
    studio.briefReady = Boolean(remote.active_revision || remote.candidate || remote.chat_history?.length || (wasReady && remote.session_id === priorSession));
    restoreConversation();
    toast('The latest changes have been loaded.');
  }
  actions.renderFields();
  actions.renderFabrics();
  actions.renderStepper();
  actions.showStep(studio.state.active_revision ? 3 : studio.briefReady ? 2 : 1);
  savedStatus();
}

export function restoreConversation() {
  if(!studio.state.chat_history?.length) return;
  $('messages').replaceChildren();
  studio.state.chat_history.forEach(message => actions.say(message.text, message.role));
}

async function showProjects() {
  if(!dialog('openProjectDialog').open) dialog('openProjectDialog').showModal();
  const list = $('projectList');
  list.replaceChildren();
  const loading = document.createElement('p');
  loading.textContent = 'Looking for your saved work…';
  list.append(loading);
  try {
    const value = await api('/projects');
    list.replaceChildren();
    if(!value.projects.length) {
      const empty = document.createElement('p');
      empty.textContent = 'No saved projects yet. Choose Save to save one.';
      list.append(empty);
    }
    value.projects.forEach(project => {
      const item = document.createElement('div');
      item.className = 'project-item';
      const button = document.createElement('button');
      button.textContent = 'Open ' + project.name;
      const date = document.createElement('small');
      date.textContent = 'Saved ' + new Date(project.updated_at * 1000).toLocaleString('en', {dateStyle:'medium',timeStyle:'short'});
      button.onclick = () => actions.safe(async () => {
        await studio.mutation;
        if(studio.dirty && !studio.conflict) await actions.save();
        const currentToken = studio.conflict ? (await api('/state')).generation_token : studio.state.generation_token;
        const next = await api('/projects/open', {project_id:project.id, expected_generation_token:currentToken});
        studio.epoch++;
        studio.activeController?.abort();
        actions.clearOutputs();
        studio.conflict = false;
        $('sessionConflict').hidden = true;
        setPagesInert(false);
        actions.adopt(next);
        studio.dirty = false;
        clearDraft();
        studio.undoReset = null;
        studio.briefReady = Boolean(next.data.materials.length);
        restoreConversation();
        dialog('openProjectDialog').close();
        actions.renderFields();
        actions.renderFabrics();
        actions.showStep(next.active_revision || next.candidate ? 3 : studio.briefReady ? 2 : 1);
        toast('Project “' + project.name + '” opened.');
      });
      item.append(button, date);
      const remove = document.createElement('button');
      remove.className = 'text-button';
      remove.textContent = 'Delete saved project';
      remove.setAttribute('aria-label', 'Delete saved project ' + project.name);
      remove.onclick = () => {
        deleting = project;
        $('deleteProjectName').textContent = project.name;
        dialog('deleteProjectDialog').showModal();
      };
      item.append(remove);
      list.append(item);
    });
  } catch(error) { loading.textContent = error.message; }
}

export async function recordEvent(event) {
  if(!studio.state?.telemetry_enabled) return;
  try { await api('/telemetry/event', {event}); } catch {}
}

export function setupProjects() {
  btn('deleteProjectYes').onclick = () => actions.safe(async () => {
    const button = btn('deleteProjectYes');
    if(!deleting || button.disabled) return;
    const target = deleting;
    button.disabled = true;
    try {
      await api('/projects/delete', {project_id:target.id});
      if(deleting === target) {
        deleting = null;
        dialog('deleteProjectDialog').close();
        if(dialog('openProjectDialog').open) await showProjects();
      }
      toast('Saved project “' + target.name + '” deleted. Your open details are still here.');
    } finally { button.disabled = false; }
  }, false);
  try {
    channel = new BroadcastChannel('sisa.work');
    channel.onmessage = async (event) => {
      if(event.data?.tab === tabId || !studio.state || event.data?.identity === identity(studio.state)) return;
      try { await reportConflict(); } catch {}
    };
  } catch {}
  // A returning tab checks the server even when BroadcastChannel is unavailable.
  document.addEventListener('visibilitychange', () => {
    if(!document.hidden && studio.state && !studio.busy && !studio.savingDraft && !studio.pendingMutations)
      reportConflict().catch(() => {});
  });
  window.addEventListener('pagehide', () => {
    rememberDraft();
    if(studio.dirty && !studio.savingDraft && !studio.pendingMutations) {
      // Finish the same guarded save when the browser closes or reloads.
      fetch('/api/input', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({data:studio.data, expected_generation_token:studio.state.generation_token}), keepalive:true}).catch(() => {});
    }
    channel?.close();
  });
  btn('loadOtherTab').onclick = () => actions.safe(() => loadLatest(false));
  btn('keepThisTab').onclick = () => actions.safe(() => loadLatest(true));
  btn('projectSave').onclick = () => {
    $('projectSaveError').textContent = '';
    dialog('saveProjectDialog').showModal();
    field('projectName').focus();
  };
  btn('projectOpen').onclick = () => actions.safe(showProjects, false);
  $('saveProjectForm').onsubmit = (event) => {
    event.preventDefault();
    const button = /** @type {HTMLButtonElement} */ ($('saveProjectForm').querySelector('[type="submit"]'));
    if(button.disabled) return;
    const name = field('projectName').value.trim();
    if(!name) {
      $('projectSaveError').textContent = 'Enter a project name first.';
      field('projectName').focus();
      return;
    }
    button.disabled = true;
    $('projectSaveError').textContent = '';
    actions.safe(async () => {
      try {
        await actions.save();
        const version = studio.epoch;
        const token = studio.state.generation_token;
        const result = await api('/projects/save', {name, expected_generation_token:token});
        if(result.state && version === studio.epoch && !studio.conflict && token === studio.state.generation_token) actions.adopt(result.state);
        dialog('saveProjectDialog').close();
        toast('Project “' + result.project.name + '” saved.');
      } catch(error) {
        if(dialog('saveProjectDialog').open) $('projectSaveError').textContent = error.message;
        else toast(error.message, true);
      } finally { button.disabled = false; }
    });
  };
  field('activityEnabled').onchange = () => actions.safe(async () => {
    const wanted = field('activityEnabled').checked;
    try {
      const result = await api('/telemetry/consent', {enabled:wanted});
      studio.state.telemetry_enabled = result.enabled;
      if(result.enabled) recordEvent('start');
    } catch(error) { field('activityEnabled').checked = !wanted; throw error; }
  }, false);
}
