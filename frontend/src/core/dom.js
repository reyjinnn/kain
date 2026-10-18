import { diagrams } from '../preview/diagrams.js';
const $ = (id) => document.getElementById(id);
const field = (id) => /** @type {HTMLInputElement} */ ($(id));
const btn = (id) => /** @type {HTMLButtonElement} */ ($(id));
const dialog = (id) => /** @type {HTMLDialogElement} */ ($(id));
const clone = (value) => JSON.parse(JSON.stringify(value));
const esc = diagrams.esc,
  cm = diagrams.cm;
const notifications = new Map();

function toast(text, warning = false, action = null) {
  const activeDialog = Array.from(document.querySelectorAll('dialog[open]')).at(-1);
  let host = activeDialog?.querySelector('.dialog-toasts');
  if (activeDialog && !host) {
    host = document.createElement('div');
    host.className = 'dialog-toasts';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    activeDialog.append(host);
  }
  host ||= $('toasts');
  const previousFocus = /** @type {HTMLElement} */ (document.activeElement);
  const el = document.createElement('div');
  el.className = 'toast' + (warning ? ' warning' : '');
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('class', 'icon toast-icon');
  icon.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', warning ? '#notice' : '#check');
  icon.append(use);
  const message = document.createElement('span');
  message.className = 'toast-message';
  message.textContent = text;
  el.append(icon, message);
  let timer;
  let remaining = 9000;
  let started = 0;
  const pause = () => {
    if (!timer) return;
    clearTimeout(timer);
    timer = null;
    remaining = Math.max(0, remaining - (performance.now() - started));
  };
  const dismiss = () => {
    const restore = el.contains(document.activeElement);
    pause();
    document.removeEventListener('visibilitychange', visibility);
    activeDialog?.removeEventListener('close', dismiss);
    notifications.delete(el);
    el.remove();
    const modal = document.querySelector('dialog[open]');
    if (restore && previousFocus?.isConnected && (!modal || modal.contains(previousFocus))) previousFocus.focus();
  };
  const resume = () => {
    if (warning || action || timer || document.hidden || el.matches(':hover') || el.contains(document.activeElement)) return;
    started = performance.now();
    timer = setTimeout(dismiss, remaining);
  };
  const visibility = () => document.hidden ? pause() : resume();
  if (action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'toast-action';
    button.textContent = action.label;
    button.onclick = async () => {
      if (button.disabled) return;
      button.disabled = true;
      try {
        await action.run();
        dismiss();
      } catch (error) {
        button.disabled = false;
        toast(error?.message || 'Action failed. Please try again.', true);
      }
    };
    el.append(button);
  }
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'icon-button toast-close';
  close.setAttribute('aria-label', 'Close notification');
  close.innerHTML = '<svg aria-hidden="true"><use href="#close" /></svg>';
  close.onclick = dismiss;
  el.append(close);
  el.addEventListener('pointerenter', pause);
  el.addEventListener('pointerleave', resume);
  el.addEventListener('focusin', pause);
  el.addEventListener('focusout', () => queueMicrotask(resume));
  document.addEventListener('visibilitychange', visibility);
  notifications.set(el, dismiss);
  for (const [item, remove] of notifications) {
    if (item !== el && (!item.isConnected || (notifications.size > 4 && !item.contains(document.activeElement)))) remove();
  }
  activeDialog?.addEventListener('close', dismiss, { once: true });
  host.append(el);
  resume();
  return dismiss;
}
export { $, field, btn, dialog, clone, esc, cm, toast };
