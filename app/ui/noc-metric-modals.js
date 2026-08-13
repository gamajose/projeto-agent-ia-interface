(() => {
  const groups = {
    'IA trabalhando': ['queued', 'investigating', 'watching'],
    'Precisa de você': ['awaiting_approval', 'needs_attention'],
    'Resolvidos hoje': ['resolved'],
  };

  function matches(label, item) {
    const states = groups[label];
    if (states) return states.includes(item.status);
    return item.status !== 'resolved';
  }

  async function show(card) {
    const ui = window.AgentCompactUI;
    if (!ui) return;
    const label = card.querySelector('span')?.textContent?.trim() || 'Incidentes ativos';
    const modal = ui.modal('noc-metric-modal', label, 'Detalhes do grupo selecionado.');
    const body = modal.querySelector('.compact-modal-body');
    body.innerHTML = '<div class="empty-state">Carregando...</div>';
    ui.open(modal);
    try {
      const response = await fetch('/ui/api/noc/dashboard');
      const data = await response.json();
      const items = (data.recent || []).filter((item) => matches(label, item));
      body.innerHTML = items.length ? items.map((item) => `<article class="compact-skill-card"><strong>${item.host || '—'}</strong><p>${item.service || '—'} · ${item.current_state || '—'}</p><small>${item.status || '—'}</small></article>`).join('') : '<div class="empty-state">Nenhum item neste grupo.</div>';
    } catch (error) {
      body.innerHTML = `<div class="empty-state">${error.message}</div>`;
    }
  }

  function bind() {
    document.querySelectorAll('#noc-summary-grid .noc-metric').forEach((card) => {
      if (card.dataset.metricModal === '1') return;
      card.dataset.metricModal = '1';
      card.tabIndex = 0;
      card.addEventListener('click', () => void show(card));
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
  setInterval(bind, 1200);
})();
