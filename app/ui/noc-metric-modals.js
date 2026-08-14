(() => {
  const groups = {
    'IA trabalhando': ['queued', 'investigating', 'watching'],
    'Precisa de você': ['awaiting_approval', 'needs_attention'],
    'Resolvidos hoje': ['resolved'],
  };

  const statusLabels = {
    new: 'Novo',
    queued: 'Na fila',
    investigating: 'Investigando',
    awaiting_approval: 'Aguardando aprovação',
    watching: 'Acompanhando',
    needs_attention: 'Precisa de atenção',
    resolved: 'Resolvido',
  };

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function matches(label, item) {
    const states = groups[label];
    if (states) return states.includes(item.status);
    return item.status !== 'resolved';
  }

  function statusLabel(status) {
    return statusLabels[status] || status || 'Desconhecido';
  }

  function whyHere(item) {
    if (item.status === 'queued') return 'A investigação foi enfileirada e está aguardando um worker operacional disponível.';
    if (item.status === 'investigating') return 'A IA está trabalhando neste incidente e ainda está coletando/analisando evidências.';
    if (item.status === 'watching') return 'A IA concluiu a etapa atual e está acompanhando o Checkmk para confirmar se o serviço permanece saudável.';
    if (item.status === 'awaiting_approval') return item.attention_reason || 'A IA encontrou uma ação que exige aprovação humana antes de executar.';
    if (item.status === 'needs_attention') {
      return item.attention_reason || item.autonomy?.reason || item.conclusion || 'A IA não conseguiu concluir o fluxo com segurança e precisa de uma decisão ou intervenção humana.';
    }
    if (item.status === 'resolved') {
      return item.conclusion || item.probable_cause || `O Checkmk confirmou a recuperação${item.resolution_source ? ` (${item.resolution_source})` : ''}.`;
    }
    return item.attention_reason || 'Incidente em processamento pelo Supervisor NOC.';
  }

  function eventDetail(event) {
    return event.output || event.reason || event.error || event.runtime?.plugin_output || event.detail || '';
  }

  function eventTitle(event) {
    if (event.event_type) return String(event.event_type).replaceAll('_', ' ');
    if (event.state) return String(event.state);
    return event.kind || 'evento';
  }

  async function fetchJson(path) {
    const response = await fetch(path);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Erro HTTP ${response.status}`);
    return data;
  }

  async function investigationExtra(item) {
    if (!item.investigation_id) return null;
    try {
      return await fetchJson(`/ui/api/investigations/${encodeURIComponent(item.investigation_id)}`);
    } catch (_error) {
      return null;
    }
  }

  function renderEvidence(investigation) {
    const evidence = Array.isArray(investigation?.evidence) ? investigation.evidence : [];
    if (!evidence.length) return '<p class="empty-state">Nenhuma evidência detalhada registrada nesta investigação.</p>';
    return evidence.slice(-8).map((row) => {
      const tool = row.tool || row.command || 'coleta';
      const status = row.status || (row.exit_code === 0 ? 'ok' : row.exit_code != null ? `exit ${row.exit_code}` : 'registrada');
      const detail = row.summary || row.stdout || row.output || row.stderr || row.purpose || '';
      return `<article class="noc-host-evidence"><div><strong>${escapeHtml(tool)}</strong><span>${escapeHtml(status)}</span></div>${detail ? `<p>${escapeHtml(String(detail).slice(0, 1200))}</p>` : ''}</article>`;
    }).join('');
  }

  async function showIncidentDetail(incidentId, groupLabel) {
    const ui = window.AgentCompactUI;
    if (!ui) return;
    const modal = ui.modal('noc-metric-host-detail-modal', 'Detalhes do incidente', 'Validação, motivo e andamento do host selecionado.');
    modal.classList.add('noc-metric-host-detail-modal');
    const title = modal.querySelector('.compact-modal-head h3');
    const subtitle = modal.querySelector('.compact-modal-head p');
    if (title) title.textContent = 'Detalhes do incidente';
    if (subtitle) subtitle.textContent = 'Validação, motivo e andamento do host selecionado.';
    const body = modal.querySelector('.compact-modal-body');
    body.innerHTML = '<div class="empty-state">Carregando detalhes do incidente...</div>';
    ui.open(modal);

    try {
      const item = await fetchJson(`/ui/api/noc/incidents/${encodeURIComponent(incidentId)}`);
      const investigation = await investigationExtra(item);
      const analysis = investigation?.analysis || {};
      const probableCause = item.probable_cause || analysis.probable_cause || 'Ainda não determinada com evidência suficiente.';
      const conclusion = item.conclusion || analysis.conclusion || analysis.summary || 'A investigação ainda não produziu uma conclusão final.';
      const autonomy = item.autonomy || {};
      const runtime = item.last_checkmk_runtime || {};
      const events = Array.isArray(item.events) ? item.events.slice(-10).reverse() : [];
      const needsHuman = ['awaiting_approval', 'needs_attention'].includes(item.status);

      body.innerHTML = `
        <section class="noc-host-detail-summary">
          <div><span>Host</span><strong>${escapeHtml(item.host || '—')}</strong></div>
          <div><span>Serviço</span><strong>${escapeHtml(item.service || '—')}</strong></div>
          <div><span>Estado Checkmk</span><strong>${escapeHtml(item.current_state || '—')}</strong></div>
          <div><span>Fluxo</span><strong>${escapeHtml(statusLabel(item.status))}</strong></div>
          <div><span>Site / ambiente</span><strong>${escapeHtml(item.site || item.environment || '—')}</strong></div>
          <div><span>Confiança</span><strong>${escapeHtml(item.confidence ?? analysis.confidence ?? 0)}%</strong></div>
        </section>

        <section class="noc-host-detail-block ${needsHuman ? 'needs-human' : ''}">
          <h4>${needsHuman ? 'Por que precisa de você' : `Por que está em “${escapeHtml(groupLabel)}”`}</h4>
          <p>${escapeHtml(whyHere(item))}</p>
          ${needsHuman && item.approval_available ? '<p><strong>Ação esperada:</strong> revisar a investigação e decidir sobre a ação proposta antes de qualquer alteração.</p>' : ''}
        </section>

        <section class="noc-host-detail-grid">
          <article class="noc-host-detail-block"><h4>Causa provável</h4><p>${escapeHtml(probableCause)}</p></article>
          <article class="noc-host-detail-block"><h4>Conclusão da IA</h4><p>${escapeHtml(conclusion)}</p></article>
          <article class="noc-host-detail-block"><h4>Última saída do sensor</h4><p>${escapeHtml(item.last_output || runtime.plugin_output || 'Sem saída registrada.')}</p></article>
          <article class="noc-host-detail-block"><h4>Autonomia / validação</h4><p>${escapeHtml(autonomy.reason || (autonomy.eligible === true ? 'Elegível para atuação autônoma dentro das políticas configuradas.' : autonomy.eligible === false ? 'Não elegível para correção autônoma.' : 'Ainda não avaliada.'))}</p>${runtime.status || runtime.state != null ? `<p><strong>Checkmk agora:</strong> ${escapeHtml(runtime.status || '—')} / ${escapeHtml(runtime.state ?? '—')}</p>` : ''}</article>
        </section>

        ${investigation ? `<section class="noc-host-detail-block"><h4>Evidências da investigação</h4><div class="noc-host-evidence-list">${renderEvidence(investigation)}</div></section>` : ''}

        <section class="noc-host-detail-block"><h4>Linha do tempo</h4><div class="noc-host-event-list">${events.length ? events.map((event) => `<div class="noc-host-event"><strong>${escapeHtml(eventTitle(event))}</strong><small>${escapeHtml(event.timestamp || event.updated_at || '')}</small>${eventDetail(event) ? `<p>${escapeHtml(String(eventDetail(event)).slice(0, 1000))}</p>` : ''}</div>`).join('') : '<p class="empty-state">Nenhum evento registrado.</p>'}</div></section>

        <div class="noc-host-detail-actions">
          ${item.investigation_id ? '<button type="button" class="secondary-button" data-open-full-investigation>Abrir investigação completa</button>' : ''}
          ${item.status !== 'resolved' ? '<button type="button" class="secondary-button" data-recheck-incident>Revalidar Checkmk</button>' : ''}
        </div>`;

      body.querySelector('[data-open-full-investigation]')?.addEventListener('click', () => {
        if (typeof window.openInvestigation === 'function') window.openInvestigation(item.investigation_id);
      });
      body.querySelector('[data-recheck-incident]')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        button.textContent = 'Revalidando...';
        try {
          const response = await fetch(`/ui/api/noc/incidents/${encodeURIComponent(item.id)}/recheck`, {
            method: 'POST',
            headers: { 'X-Agent-UI': '1' },
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || `Erro HTTP ${response.status}`);
          await showIncidentDetail(item.id, groupLabel);
        } catch (error) {
          button.disabled = false;
          button.textContent = 'Revalidar Checkmk';
          if (typeof window.toast === 'function') window.toast(error.message, 'error');
        }
      });
    } catch (error) {
      body.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    }
  }

  function bindIncidentCards(body, groupLabel) {
    body.querySelectorAll('[data-metric-incident-id]').forEach((card) => {
      const open = () => void showIncidentDetail(card.dataset.metricIncidentId, groupLabel);
      card.addEventListener('click', open);
      card.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          open();
        }
      });
    });
  }

  async function show(card) {
    const ui = window.AgentCompactUI;
    if (!ui) return;
    const label = card.querySelector('span')?.textContent?.trim() || 'Incidentes ativos';
    const modal = ui.modal('noc-metric-modal', label, 'Clique em um host para ver a validação e o motivo do estado atual.');
    const title = modal.querySelector('.compact-modal-head h3');
    const subtitle = modal.querySelector('.compact-modal-head p');
    if (title) title.textContent = label;
    if (subtitle) subtitle.textContent = 'Clique em um host para ver a validação e o motivo do estado atual.';
    const body = modal.querySelector('.compact-modal-body');
    body.innerHTML = '<div class="empty-state">Carregando...</div>';
    ui.open(modal);
    try {
      const response = await fetch('/ui/api/noc/dashboard');
      const data = await response.json();
      const items = (data.recent || []).filter((item) => matches(label, item));
      body.innerHTML = items.length ? items.map((item) => `<article class="compact-skill-card noc-metric-incident-card" data-metric-incident-id="${escapeHtml(item.id)}" tabindex="0" role="button" aria-label="Abrir detalhes de ${escapeHtml(item.host || 'host')}"><div class="noc-metric-card-main"><strong>${escapeHtml(item.host || '—')}</strong><p>${escapeHtml(item.service || '—')} · ${escapeHtml(item.current_state || '—')}</p><small>${escapeHtml(statusLabel(item.status))}</small></div><span class="noc-metric-card-arrow" aria-hidden="true">›</span></article>`).join('') : '<div class="empty-state">Nenhum item neste grupo.</div>';
      bindIncidentCards(body, label);
    } catch (error) {
      body.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    }
  }

  function ensureStyles() {
    if (document.getElementById('noc-metric-detail-styles')) return;
    const style = document.createElement('style');
    style.id = 'noc-metric-detail-styles';
    style.textContent = `
      .noc-metric-incident-card{display:flex;align-items:center;justify-content:space-between;gap:12px;cursor:pointer;transition:border-color .16s ease,transform .16s ease,background .16s ease}.noc-metric-incident-card:hover,.noc-metric-incident-card:focus-visible{border-color:var(--accent,#61d9ff);transform:translateY(-1px);outline:none;background:rgba(97,217,255,.04)}.noc-metric-card-main{min-width:0}.noc-metric-card-arrow{font-size:26px;line-height:1;color:var(--muted,#93a4b8)}
      #noc-metric-host-detail-modal{z-index:1300}#noc-metric-host-detail-modal .compact-modal-panel{max-height:min(88vh,920px);overflow:auto}.noc-host-detail-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:12px}.noc-host-detail-summary>div,.noc-host-detail-block{border:1px solid var(--border,#294057);background:rgba(255,255,255,.018);border-radius:12px;padding:12px}.noc-host-detail-summary span{display:block;font-size:11px;text-transform:uppercase;color:var(--muted,#93a4b8);margin-bottom:4px}.noc-host-detail-summary strong{display:block;overflow-wrap:anywhere}.noc-host-detail-block{margin-bottom:10px}.noc-host-detail-block h4{margin:0 0 7px}.noc-host-detail-block p{margin:4px 0;white-space:pre-wrap;overflow-wrap:anywhere}.noc-host-detail-block.needs-human{border-color:rgba(255,180,65,.48);background:rgba(255,180,65,.05)}.noc-host-detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.noc-host-evidence-list,.noc-host-event-list{display:grid;gap:7px}.noc-host-evidence,.noc-host-event{border-top:1px solid var(--border,#294057);padding-top:8px}.noc-host-evidence:first-child,.noc-host-event:first-child{border-top:0;padding-top:0}.noc-host-evidence>div{display:flex;justify-content:space-between;gap:10px}.noc-host-evidence span,.noc-host-event small{color:var(--muted,#93a4b8);font-size:11px}.noc-host-detail-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;padding-top:4px}@media(max-width:820px){.noc-host-detail-summary,.noc-host-detail-grid{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  function bind() {
    ensureStyles();
    document.querySelectorAll('#noc-summary-grid .noc-metric').forEach((card) => {
      if (card.dataset.metricModal === '1') return;
      card.dataset.metricModal = '1';
      card.tabIndex = 0;
      card.addEventListener('click', () => void show(card));
      card.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          void show(card);
        }
      });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
  setInterval(bind, 1200);
})();
