(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function modal(id, title) {
    let node = document.getElementById(id);
    if (node) return node;
    node = document.createElement('aside');
    node.id = id;
    node.className = 'compact-modal';
    node.innerHTML = `<div class="compact-modal-backdrop" data-close></div><section class="compact-modal-panel compact-modal-wide"><header class="compact-modal-head"><h3>${title}</h3><button class="compact-modal-close" type="button" data-close>×</button></header><div class="compact-modal-body"></div></section>`;
    document.body.appendChild(node);
    node.addEventListener('click', (event) => { if (event.target.closest('[data-close]')) node.classList.remove('open'); });
    return node;
  }

  function compactN2() {
    const view = $('#view-n2');
    if (!view || !view.dataset.n2Ready || view.dataset.compactReady === '1') return;
    view.dataset.compactReady = '1';
    $('.n2-page-head', view)?.remove();
    const bar = document.createElement('div');
    bar.className = 'n2-compact-head';
    bar.innerHTML = '<button class="secondary-button" type="button" id="n2-history-compact">Histórico</button>';
    view.prepend(bar);
    const saved = $('.n2-saved-card', view);
    if (saved) {
      const history = modal('compact-n2-history', 'Histórico N2');
      $('.compact-modal-body', history).appendChild(saved);
      $('#n2-new-document', saved)?.remove();
      $('.n2-saved-head > div:first-child', saved)?.remove();
      $('#n2-history-compact')?.addEventListener('click', () => history.classList.add('open'));
    }
    const hostCard = $('.n2-host-card', view);
    const runCard = $('.n2-run-card', view);
    if (hostCard && runCard) {
      const actions = $('.n2-run-actions', runCard);
      const progress = $('#n2-progress', runCard);
      if (actions) hostCard.appendChild(actions);
      if (progress) hostCard.appendChild(progress);
      runCard.remove();
    }
    const reviewStep = $('.n2-review-card .n2-step-title > span', view);
    if (reviewStep) reviewStep.textContent = '3';
  }

  function compactAll() { compactN2(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', compactAll); else compactAll();
  setInterval(() => { if (!document.hidden) compactAll(); }, 1200);
})();
