(() => {
  const state = { page: 1, size: 15, query: '', order: 'recent' };
  const byId = (id) => document.getElementById(id);

  function apply() {
    const body = byId('noc-incidents-table');
    const pager = byId('noc-queue-pager');
    if (!body || !pager) return;

    const allRows = [...body.querySelectorAll('tr[data-noc-incident-id]')];
    allRows.forEach((row, index) => {
      if (!row.dataset.queueSequence) row.dataset.queueSequence = String(index);
    });

    const query = state.query.trim().toLocaleLowerCase('pt-BR');
    let rows = allRows.filter((row) => !query || row.textContent.toLocaleLowerCase('pt-BR').includes(query));
    if (state.order === 'host') {
      rows.sort((a, b) => a.cells[0].textContent.localeCompare(b.cells[0].textContent, 'pt-BR'));
    } else if (state.order === 'oldest') {
      rows.sort((a, b) => Number(b.dataset.queueSequence) - Number(a.dataset.queueSequence));
    } else {
      rows.sort((a, b) => Number(a.dataset.queueSequence) - Number(b.dataset.queueSequence));
    }
    rows.forEach((row) => body.appendChild(row));

    const pages = Math.max(1, Math.ceil(rows.length / state.size));
    state.page = Math.min(state.page, pages);
    allRows.forEach((row) => { row.hidden = true; });
    const start = (state.page - 1) * state.size;
    rows.slice(start, start + state.size).forEach((row) => { row.hidden = false; });

    pager.innerHTML = `<span>${rows.length} incidentes · página ${state.page}/${pages}</span><button type="button" class="secondary-button" data-prev>Anterior</button><button type="button" class="secondary-button" data-next>Próxima</button>`;
    const prev = pager.querySelector('[data-prev]');
    const next = pager.querySelector('[data-next]');
    prev.disabled = state.page === 1;
    next.disabled = state.page === pages;
    prev.addEventListener('click', () => { state.page -= 1; apply(); });
    next.addEventListener('click', () => { state.page += 1; apply(); });
  }

  function setup() {
    const body = byId('noc-incidents-table');
    const controls = byId('noc-queue-filter');
    if (!body || !controls) return;
    const panel = body.closest('.panel');
    if (panel && !byId('noc-queue-pager')) {
      const pager = document.createElement('div');
      pager.id = 'noc-queue-pager';
      pager.className = 'compact-pager';
      panel.appendChild(pager);
      controls.addEventListener('input', (event) => { state.query = event.target.value; state.page = 1; apply(); });
      byId('noc-queue-order')?.addEventListener('change', (event) => { state.order = event.target.value; state.page = 1; apply(); });
      byId('noc-queue-size')?.addEventListener('change', (event) => { state.size = Number(event.target.value); state.page = 1; apply(); });
    }
    apply();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup); else setup();
  setInterval(() => { if (!document.hidden) setup(); }, 1200);
})();
