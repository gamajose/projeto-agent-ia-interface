(() => {
  const STORAGE_KEY = 'agent-ui-active-execution';
  let timer = null;
  let loading = false;

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function latestJourney(record) {
    const candidates = [record?.current_phase, ...(record?.events || []).slice().reverse(), ...(record?.phases || []).slice().reverse()];
    for (const item of candidates) {
      if (Array.isArray(item?.access_journey) && item.access_journey.length) return item.access_journey;
    }
    return [];
  }

  function statusLabel(status) {
    return ({ completed: 'OK', running: 'EM ANDAMENTO', failed: 'FALHOU', skipped: 'IGNORADO' })[status] || 'AGUARDANDO';
  }

  function journeyMarkup(record) {
    const journey = latestJourney(record);
    if (!journey.length) return '<div class="execution-access-empty">Aguardando detalhes do caminho SSH/VPN...</div>';
    return journey.map((item) => `<article class="execution-access-step" data-status="${esc(item.status || 'pending')}"><span class="execution-access-dot"></span><div><strong>${esc(item.label || item.step || 'Etapa')}</strong><p>${esc(item.detail || 'Sem detalhe adicional.')}</p></div><b>${esc(statusLabel(item.status))}</b></article>`).join('');
  }

  function eventRows(record) {
    const events = (record?.events || []).filter((event) => !String(event.stage || '').startsWith('command_')).slice(-18).reverse();
    if (!events.length) return '<div class="execution-access-empty">Nenhum evento de execução registrado ainda.</div>';
    return events.map((event) => `<div class="execution-event-row"><span>${esc(String(event.stage || 'evento').replaceAll('_', ' '))}</span><p>${esc(event.detail || 'Atualização recebida.')}</p></div>`).join('');
  }

  function inject(record) {
    const live = document.querySelector('.execution-live-panel');
    const layout = document.querySelector('.execution-progress-layout');
    if (!live || !layout) return;

    let panel = document.querySelector('#execution-access-live');
    if (!panel) {
      panel = document.createElement('section');
      panel.className = 'execution-access-live';
      panel.id = 'execution-access-live';
      layout.insertBefore(panel, live);
    }
    panel.innerHTML = `<header><div><p class="eyebrow">ACESSO EM TEMPO REAL</p><h3>SSH / VPN passo a passo</h3></div><span>${esc(record?.status || 'running')}</span></header><div class="execution-access-list">${journeyMarkup(record)}</div><details class="execution-event-details"><summary>Ver eventos da investigação</summary><div>${eventRows(record)}</div></details>`;

    const empty = live.querySelector('.execution-live-empty strong');
    if (empty && latestJourney(record).length) empty.textContent = 'SSH concluído; aguardando o primeiro comando de coleta';
  }

  async function refresh() {
    if (loading) return;
    const id = localStorage.getItem(STORAGE_KEY);
    const drawer = document.querySelector('#result-drawer');
    if (!id || !drawer?.classList.contains('open')) return;
    loading = true;
    try {
      const response = await fetch(`/ui/api/executions/${encodeURIComponent(id)}`);
      if (!response.ok) return;
      const record = await response.json();
      inject(record);
    } catch { /* o tracker original continua responsável pelo resultado */ }
    finally { loading = false; }
  }

  function setup() {
    timer = window.setInterval(refresh, 700);
    new MutationObserver(() => void refresh()).observe(document.body, { childList: true, subtree: true });
    void refresh();
  }

  window.addEventListener('beforeunload', () => timer && window.clearInterval(timer));
  document.addEventListener('DOMContentLoaded', setup);
})();
