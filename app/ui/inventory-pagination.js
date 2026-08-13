(() => {
  const state = { page: 1, size: 24, order: 'asc', busy: false };
  const byId = (id) => document.getElementById(id);

  function render() {
    const grid = byId('inventory-grid');
    const footer = byId('inventory-pager');
    if (!grid || !footer || state.busy) return;
    state.busy = true;
    try {
      const cards = [...grid.children].filter((node) => !node.classList.contains('empty-state'));
      cards.sort((a, b) => {
        const value = a.textContent.localeCompare(b.textContent, 'pt-BR');
        return state.order === 'desc' ? -value : value;
      });
      cards.forEach((card) => grid.appendChild(card));
      const pages = Math.max(1, Math.ceil(cards.length / state.size));
      state.page = Math.min(state.page, pages);
      cards.forEach((card) => { card.hidden = true; });
      const start = (state.page - 1) * state.size;
      cards.slice(start, start + state.size).forEach((card) => { card.hidden = false; });
      footer.innerHTML = `<span>${cards.length} itens · página ${state.page}/${pages}</span><button type="button" class="secondary-button" data-prev ${state.page === 1 ? 'disabled' : ''}>Anterior</button><button type="button" class="secondary-button" data-next ${state.page === pages ? 'disabled' : ''}>Próxima</button>`;
      footer.querySelector('[data-prev]')?.addEventListener('click', () => { state.page -= 1; render(); });
      footer.querySelector('[data-next]')?.addEventListener('click', () => { state.page += 1; render(); });
    } finally {
      state.busy = false;
    }
  }

  function setup() {
    const view = byId('view-inventory');
    const grid = byId('inventory-grid');
    if (!view || !grid) return;
    if (!byId('inventory-order')) {
      const filters = view.querySelector('.filters');
      if (filters) {
        const order = document.createElement('select');
        order.id = 'inventory-order';
        order.innerHTML = '<option value="asc">A-Z</option><option value="desc">Z-A</option>';
        order.addEventListener('change', () => { state.order = order.value; state.page = 1; render(); });
        filters.appendChild(order);
        const size = document.createElement('select');
        size.id = 'inventory-page-size';
        size.innerHTML = '<option value="24">24/página</option><option value="48">48/página</option><option value="96">96/página</option>';
        size.addEventListener('change', () => { state.size = Number(size.value); state.page = 1; render(); });
        filters.appendChild(size);
      }
    }
    if (!byId('inventory-pager')) {
      const footer = document.createElement('div');
      footer.id = 'inventory-pager';
      footer.className = 'compact-pager';
      grid.insertAdjacentElement('afterend', footer);
    }
    if (!grid.dataset.paginationObserver) {
      grid.dataset.paginationObserver = '1';
      new MutationObserver(() => { if (!state.busy) setTimeout(render, 0); }).observe(grid, { childList: true });
    }
    render();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup); else setup();
  setInterval(() => { if (!document.hidden) setup(); }, 1500);
})();
