import { dialog } from '../core/dom.js';

function setupOverlays() {
  const helpButton = document.getElementById('helpOpen');
  let scrollLocked = false;
  let previousOverflow = '';
  const sync = () => {
    const open = document.querySelector('dialog[open]');
    if (Boolean(open) !== scrollLocked) {
      if (open) {
        previousOverflow = document.documentElement.style.overflow;
        document.documentElement.style.overflow = 'hidden';
      } else {
        document.documentElement.style.overflow = previousOverflow;
      }
      scrollLocked = Boolean(open);
    }
    helpButton.setAttribute('aria-expanded', String(dialog('helpDialog').open));
  };
  for (const panel of Array.from(document.querySelectorAll('dialog'))) {
    if (panel.id !== 'consentDialog') {
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'icon-button dialog-close';
      close.setAttribute('aria-label', 'Close ' + (document.getElementById(panel.getAttribute('aria-labelledby') || '')?.textContent?.trim() || 'dialog'));
      close.innerHTML = '<svg aria-hidden="true"><use href="#close" /></svg>';
      close.addEventListener('click', () => panel.close());
      panel.prepend(close);
    }
    panel.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const controls = Array.from(panel.querySelectorAll('button, a[href], input, select, textarea, summary, [tabindex]'))
        .filter(node => node instanceof HTMLElement && node.tabIndex >= 0 && !node.matches(':disabled') && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
      const first = /** @type {HTMLElement} */ (controls[0]);
      const last = /** @type {HTMLElement} */ (controls.at(-1));
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    });
    let backdropDown = false;
    const outside = event => {
      const bounds = panel.getBoundingClientRect();
      return event.target === panel && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom);
    };
    panel.addEventListener('pointerdown', event => { backdropDown = outside(event); });
    panel.addEventListener('pointerup', event => {
      if (backdropDown && outside(event) && panel.dataset.backdropClose === 'true') panel.close();
      backdropDown = false;
    });
    panel.addEventListener('close', () => {
      panel.querySelector('.dialog-toasts')?.remove();
      sync();
    });
    new MutationObserver(sync).observe(panel, { attributes: true, attributeFilter: ['open'] });
  }
  sync();
}

export { setupOverlays };
