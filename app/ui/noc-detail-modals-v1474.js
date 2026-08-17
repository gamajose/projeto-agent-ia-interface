(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  const stateClass = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-');

  async function request(path) {
    const response = await fetch(path);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  function formatDate(value) {
    if (!value) return 'sem horário registrado';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString('pt-BR');
  }

  function modal(id, title, subtitle = '') {
    const ui = window.AgentCompactUI;
    if (!ui) return null;
    const node = ui.modal(id, title, subtitle);
    node.classList.add('noc-detail-modal');
    return node;
  }

  function openModal(node) {
    window.AgentCompactUI?.open?.(node);
  }

  function setModalTitle(node, title, subtitle = '') {
    const head = $('.compact-modal-head > div', node);
    if (!head) return;
    const heading = $('h3', head);
    if (heading) heading.textContent = title;
    let paragraph = $('p', head);
    if (subtitle) {
      if (!paragraph) {
        paragraph = document.createElement('p');
        head.appendChild(paragraph);
      }
      paragraph.textContent = subtitle;
    } else {
      paragraph?.remove();
    }
  }

  function batchMemberSearchText(member) {
    const alerts = Array.isArray(member.alerts) ? member.alerts : [];
    return [
      member.client_alias,
      member.site_id,
      member.host,
      member.host_address,
      ...alerts.flatMap((alert) => [alert.service, alert.state, alert.output, alert.automation_status]),
    ].join(' ').toLocaleLowerCase('pt-BR');
  }

  function renderMemberAlerts(member) {
    const alerts = Array.isArray(member.alerts) ? member.alerts : [];
    const visible = alerts.slice(0, 3);
    const extra = Math.max(0, alerts.length - visible.length);
    if (!visible.length) return '<span class="noc-detail-muted">Sem sensor detalhado.</span>';
    return `${visible.map((alert) => `
      <div class="noc-detail-alert-line">
        <span class="cmk-state ${esc(stateClass(alert.state))}">${esc(alert.state || '—')}</span>
        <div><strong>${esc(alert.service || 'Sensor')}</strong><small>${esc(alert.output || 'sem output')}</small></div>
      </div>`).join('')}${extra ? `<small class="noc-detail-more">+ ${extra} alerta(s) neste host</small>` : ''}`;
  }

  function filterBatchMembers(modalNode) {
    const query = String($('#noc-batch-detail-search', modalNode)?.value || '').trim().toLocaleLowerCase('pt-BR');
    let visible = 0;
    $$('[data-batch-member]', modalNode).forEach((row) => {
      const show = !query || String(row.dataset.search || '').includes(query);
      row.hidden = !show;
      if (show) visible += 1;
    });
    const count = $('#noc-batch-detail-visible', modalNode);
    if (count) count.textContent = `${visible} host(s) exibido(s)`;
  }

  async function openBatchDetail(procedureId) {
    if (!procedureId) return;
    const node = modal(
      'noc-problem-group-detail-modal',
      'Hosts do problema',
      'Valide empresa, host, IP e sensores antes de executar a correção em lote.',
    );
    if (!node) return;
    const body = $('.compact-modal-body', node);
    if (!body) return;
    setModalTitle(node, 'Hosts do problema', 'Valide empresa, host, IP e sensores antes de executar a correção em lote.');
    body.innerHTML = '<div class="noc-detail-loading">Carregando hosts do problema…</div>';
    openModal(node);

    try {
      const data = await request(`/ui/api/noc/problem-groups/${encodeURIComponent(procedureId)}/detail`);
      const members = Array.isArray(data.members) ? data.members : [];
      setModalTitle(node, data.title || 'Hosts do problema', String(data.procedure_id || procedureId));
      body.innerHTML = `
        <section class="noc-batch-detail-summary">
          <div><span>HOSTS</span><strong>${Number(data.host_count || 0).toLocaleString('pt-BR')}</strong></div>
          <div><span>ALERTAS</span><strong>${Number(data.problem_count || 0).toLocaleString('pt-BR')}</strong></div>
          <div><span>CLIENTES / SITES</span><strong>${Number(data.site_count || 0).toLocaleString('pt-BR')}</strong></div>
          <div><span>ÚLTIMA FOTOGRAFIA</span><strong>${esc(formatDate(data.last_completed_at))}</strong></div>
        </section>
        ${data.snapshot_running ? '<div class="noc-detail-info">Uma nova ronda do Checkmk está em andamento. Esta lista representa a última fotografia concluída e será atualizada quando você usar “Atualizar problemas”.</div>' : ''}
        <div class="noc-batch-detail-tools">
          <input id="noc-batch-detail-search" type="search" placeholder="Buscar empresa, site, host, IP ou sensor">
          <span id="noc-batch-detail-visible">${members.length} host(s) exibido(s)</span>
        </div>
        <div class="noc-batch-detail-table-wrap">
          <table class="noc-batch-detail-table">
            <thead><tr><th>Empresa / site</th><th>Host / IP</th><th>Qtd.</th><th>Sensores / erros</th></tr></thead>
            <tbody>${members.length ? members.map((member) => `
              <tr data-batch-member data-search="${esc(batchMemberSearchText(member))}">
                <td><strong>${esc(member.client_alias || member.site_id || '—')}</strong><small>${esc(member.site_id || '—')}</small></td>
                <td><strong>${esc(member.host || '—')}</strong><small>${esc(member.host_address || 'sem IP')}</small></td>
                <td><b>${Number(member.alert_count || 0).toLocaleString('pt-BR')}</b></td>
                <td>${renderMemberAlerts(member)}</td>
              </tr>`).join('') : '<tr><td colspan="4" class="empty-cell">Nenhum host ativo pertence mais a este problema.</td></tr>'}</tbody>
          </table>
        </div>`;
      $('#noc-batch-detail-search', node)?.addEventListener('input', () => filterBatchMembers(node));
    } catch (error) {
      body.innerHTML = `<div class="noc-detail-error">${esc(error.message)}</div>`;
    }
  }

  function renderSiteProblems(node, problems, selectedHost = '') {
    const holder = $('#noc-cmk-site-problems-list', node);
    const title = $('#noc-cmk-site-problems-title', node);
    if (!holder || !title) return;
    const selected = String(selectedHost || '');
    const items = selected ? problems.filter((problem) => String(problem.host || '') === selected) : problems;
    title.textContent = selected ? `Problemas de ${selected}` : 'Problemas do cliente';
    holder.innerHTML = items.length ? items.map((problem) => `
      <article>
        <div><span class="cmk-state ${esc(stateClass(problem.state_name))}">${esc(problem.state_name || problem.state || '—')}</span><strong>${esc(problem.host || '—')}</strong><small>${esc(problem.host_address || '—')}</small></div>
        <div><strong>${esc(problem.service || '—')}</strong><p>${esc(problem.output || 'sem output')}</p><small>${esc(problem.skill_title || 'Skill genérica')} · ${esc(problem.automation_status || 'detected')}</small></div>
      </article>`).join('') : '<div class="empty-state">Este host não possui problema ativo.</div>';
    $$('[data-modal-host]', node).forEach((row) => row.classList.toggle('selected', row.dataset.modalHost === selected));
    $('#noc-cmk-show-all', node)?.classList.toggle('active', !selected);
  }

  async function openCheckmkSite(siteId, selectedHost = '') {
    if (!siteId) return;
    const node = modal(
      'noc-checkmk-site-detail-modal',
      'Cliente / site',
      'Hosts e problemas atuais do Checkmk Central.',
    );
    if (!node) return;
    const body = $('.compact-modal-body', node);
    if (!body) return;
    setModalTitle(node, 'Cliente / site', 'Hosts e problemas atuais do Checkmk Central.');
    body.innerHTML = '<div class="noc-detail-loading">Carregando cliente…</div>';
    openModal(node);

    const legacy = $('#cmk-site-detail');
    if (legacy) {
      legacy.hidden = true;
      legacy.innerHTML = '';
    }

    try {
      const data = await request(`/ui/api/noc/checkmk-master/sites/${encodeURIComponent(siteId)}`);
      const site = data.site || {};
      const hosts = Array.isArray(data.hosts) ? data.hosts : [];
      const problems = Array.isArray(data.problems) ? data.problems : [];
      setModalTitle(node, site.alias || site.site_id || 'Cliente / site', `${site.site_id || siteId} · ${site.livestatus_host || '—'}:${site.livestatus_port || '—'}`);
      body.innerHTML = `
        ${site.last_error ? `<div class="cmk-site-error"><strong>Livestatus:</strong> ${esc(site.last_error)}</div>` : ''}
        <div class="cmk-site-metrics"><span><strong>${hosts.length}</strong> hosts</span><span><strong>${problems.length}</strong> problemas</span><span>${site.shared_endpoint ? 'endpoint compartilhado' : 'endpoint dedicado'}</span></div>
        <div class="noc-cmk-detail-grid">
          <section>
            <h4>Hosts <small>clique para filtrar os problemas</small></h4>
            <div class="cmk-table-wrap detail"><table class="cmk-table"><thead><tr><th>Host</th><th>IP interno</th><th>Tipo</th><th>Estado</th><th>Problemas</th></tr></thead><tbody>${hosts.length ? hosts.map((host) => `
              <tr class="noc-cmk-host-row" data-modal-host="${esc(host.host_name)}"><td><strong>${esc(host.host_name)}</strong></td><td>${esc(host.internal_address || '—')}</td><td>${esc(host.host_kind || '—')}</td><td>${esc(host.state)}</td><td>${esc(host.problem_count || 0)}</td></tr>`).join('') : '<tr><td colspan="5" class="empty-cell">Nenhum host coletado.</td></tr>'}</tbody></table></div>
          </section>
          <section>
            <div class="cmk-site-problem-head"><h4 id="noc-cmk-site-problems-title">Problemas do cliente</h4><button type="button" class="ghost-button active" id="noc-cmk-show-all">Todos (${problems.length})</button></div>
            <div class="cmk-site-problems" id="noc-cmk-site-problems-list"></div>
          </section>
        </div>`;
      renderSiteProblems(node, problems, selectedHost);
      $$('[data-modal-host]', node).forEach((row) => row.addEventListener('click', () => renderSiteProblems(node, problems, row.dataset.modalHost || '')));
      $('#noc-cmk-show-all', node)?.addEventListener('click', () => renderSiteProblems(node, problems, ''));
    } catch (error) {
      body.innerHTML = `<div class="noc-detail-error">${esc(error.message)}</div>`;
    }
  }

  function centralRowContext(target) {
    const operationalBody = target.closest?.('#cmk-operational-body');
    if (!operationalBody) return null;
    const row = target.closest('tr');
    if (!row) return null;
    const direct = row.dataset.cmkSite;
    const nested = row.querySelector('[data-cmk-site]')?.dataset.cmkSite;
    const siteId = String(direct || nested || '').trim();
    if (!siteId) return null;
    let host = '';
    if (row.querySelector('.cmk-problem-copy')) {
      host = String(row.children?.[1]?.querySelector?.('strong')?.textContent || '').trim();
    }
    return { siteId, host };
  }

  document.addEventListener('click', (event) => {
    const context = centralRowContext(event.target);
    if (!context) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    void openCheckmkSite(context.siteId, context.host);
  }, true);

  document.addEventListener('click', (event) => {
    const card = event.target.closest?.('.noc-problem-batch-card');
    if (!card || event.target.closest('.noc-problem-batch-run')) return;
    const procedureId = String(card.dataset.procedure || '').trim();
    if (!procedureId) return;
    void openBatchDetail(procedureId);
  });
})();
