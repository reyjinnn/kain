import { $, btn } from '../core/dom.js';
import { tourCopy } from '../config/copy.en.js';
import { studio } from '../core/state.js';

export function setupTour() {
  let seen = false;
  try { seen = localStorage.getItem('sisa.tour') === 'seen'; } catch {}
  if(seen || studio.conflict || new URLSearchParams(location.search).has('test')) return;
  let index = 0;
  const update = () => {
    $('microTour').hidden = false;
    $('tourText').textContent = tourCopy[index];
    $('tourCount').textContent = (index + 1) + ' of 3';
    btn('tourNext').textContent = index === 2 ? 'Start your story' : 'Continue tour';
  };
  const done = (focus = true) => {
    $('microTour').hidden = true;
    try { localStorage.setItem('sisa.tour', 'seen'); } catch {}
    if(focus) $('storyInput').focus();
  };
  btn('tourNext').onclick = () => { if(index === 2) done(); else { index++; update(); } };
  btn('tourSkip').onclick = () => done();
  $('composer').addEventListener('submit', () => done(false), {once:true});
  btn('demoStart').addEventListener('click', () => done(false), {once:true});
  btn('manualStart').addEventListener('click', () => done(false), {once:true});
  update();
}
