(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  let storageLoadedAt = 0;
  let storageLoading = false;

  async function request(path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const headers = { ...(options.headers || {}) };
    let body = options.body;
    if (method !== 'GET') headers['X-Agent-UI'] = '1';
    if (body && typeof body === 'object') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, method, headers, body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  function bytes(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number) || number <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let current = number;
    let index = 0;
    while (current >= 1024 && index < units.length - 1) { current /= 1024; index += 1; }
    const digits = current >= 100 || index === 0 ? 0 : current >= 10 ? 1 : 2;
    return `${current.toFixed(digits)} ${units[index]}`;
  }

  function number(value) {
    return Number(value || 0).toLocaleString('pt-BR');
  }

  function metric(label, value) {
    return `<div><span>${label}</span><strong>${value}</strong></div>`;
  }

  function setupIncidentQueue() {
    const table = $('#noc-incidents-table');
    const panel = table?.closest('.panel');
    if (!panel) return;
    const eyebrow = $('.panel-header .eyebrow', panel);
    if (eyebrow) eyebrow.remove();
    const live = $('.noc-live', panel);
    if (live && live.dataset.compactDot !== '1') {
      live.dataset.compactDot = '1';
      live.textContent = '';
      live.title = 'Atualização automática ativa';
      live.setAttribute('aria-label', 'Atualização automática ativa');
    }
  }

  function setupCheckmkHeading() {
    const head = $('#noc-fleet-panel .cmk-master-head');
    const eyebrow = head?.querySelector('.eyebrow');
    if (eyebrow) eyebrow.remove();
  }

  function setupAgentPolicies() {
    const ui = window.AgentCompactUI;
    const agentModal = $('#compact-agent-modal');
    const policyButton = $('#compact-policy-button');
    if (!ui || !agentModal || !policyButton) return;
    let tools = $('.agent-modal-tools', agentModal);
    if (!tools) {
      tools = document.createElement('div');
      tools.className = 'compact-toolbar agent-modal-tools';
      const body = $('.compact-modal-body', agentModal);
      body?.prepend(tools);
    }
    if (tools && policyButton.parentElement !== tools) tools.appendChild(policyButton);
  }

  function setupDiscoveryLayout() {
    const modal = $('#compact-discovery-modal');
    if (!modal) return;
    const discovery = $('.fleet-contingency', modal);
    const head = $('.fleet-head.compact', discovery || modal);
    const scope = $('.fleet-scope-field', discovery || modal);
    if (head && scope && scope.previousElementSibling !== head) head.insertAdjacentElement('afterend', scope);
  }

  function setupGuardrails() {
    const ui = window.AgentCompactUI;
    const settings = $('#view-settings');
    const safety = $('#view-dashboard .safety-panel') || $('#settings-guardrails-modal .safety-panel');
    if (!ui || !settings || !safety) return;
    const modal = ui.modal('settings-guardrails-modal', 'Guardrails', 'Proteções que limitam a atuação dos agentes.');
    safety.classList.add('settings-guardrail-panel');
    const body = $('.compact-modal-body', modal);
    if (body && safety.parentElement !== body) body.appendChild(safety);

    if (!$('#settings-guardrails')) {
      const actions = $('.provider-priority-actions', settings) || $('.provider-priority-head', settings) || settings;
      const button = document.createElement('button');
      button.id = 'settings-guardrails';
      button.type = 'button';
      button.className = 'secondary-button';
      button.textContent = 'Guardrails';
      button.addEventListener('click', () => ui.open(modal));
      actions.appendChild(button);
    }
  }

  function storageCard(title, data, content) {
    const state = data?.state || 'unavailable';
    return `<article class="database-resource-card" data-state="${state}"><div class="database-resource-head"><h4>${title}</h4><span class="database-resource-state"><i></i>${state === 'available' ? 'ativo' : 'indisponível'}</span></div>${content}</article>`;
  }

  function renderStorage(data) {
    const root = $('#database-overview-grid');
    if (!root) return;
    const pg = data.postgres || {};
    const redis = data.redis || {};
    const pgContent = pg.state === 'available'
      ? `<div class="database-resource-metrics">${[
          metric('Banco', bytes(pg.database_bytes)),
          metric('Tabelas', number(pg.table_count)),
          metric('Dados', bytes(pg.table_bytes)),
          metric('Índices', bytes(pg.index_bytes)),
          metric('Linhas estimadas', number(pg.estimated_rows)),
          metric('Conexões', number(pg.connections)),
          metric('Cache hit', `${Number(pg.cache_hit_percent || 0).toFixed(1)}%`),
          metric('Consulta', `${Number(pg.latency_ms || 0).toFixed(1)} ms`),
        ].join('')}</div><div class="database-table-list"><h5>Maiores tabelas</h5>${(pg.tables || []).length ? `<table><thead><tr><th>Tabela</th><th>Total</th><th>Dados</th><th>Índices</th><th>Linhas</th></tr></thead><tbody>${pg.tables.map((item) => `<tr><td>${String(item.name || '—')}</td><td>${bytes(item.total_bytes)}</td><td>${bytes(item.table_bytes)}</td><td>${bytes(item.index_bytes)}</td><td>${number(item.estimated_rows)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty-state">Nenhuma tabela de usuário encontrada.</div>'}</div>`
      : `<div class="empty-state">${pg.detail || 'PostgreSQL indisponível.'}</div>`;
    const redisContent = redis.state === 'available'
      ? `<div class="database-resource-metrics">${[
          metric('Memória', bytes(redis.used_memory_bytes)),
          metric('RSS', bytes(redis.used_memory_rss_bytes)),
          metric('Chaves', number(redis.keys)),
          metric('Clientes', number(redis.connected_clients)),
          metric('Fila', number(redis.queue_depth)),
          metric('Ping', `${Number(redis.latency_ms || 0).toFixed(1)} ms`),
          metric('Fragmentação', Number(redis.fragmentation_ratio || 0).toFixed(2)),
          metric('Limite', Number(redis.maxmemory_bytes || 0) > 0 ? bytes(redis.maxmemory_bytes) : 'sem limite'),
        ].join('')}</div><div class="database-table-list"><h5>Fila operacional</h5><div class="database-resource-metrics">${metric('Nome', String(redis.queue || '—'))}${metric('Pendentes', number(redis.queue_depth))}</div></div>`
      : `<div class="empty-state">${redis.detail || 'Redis indisponível.'}</div>`;
    root.innerHTML = storageCard('PostgreSQL', pg, pgContent) + storageCard('Redis', redis, redisContent);
  }

  async function loadStorage(force = false) {
    if (storageLoading) return;
    if (!force && Date.now() - storageLoadedAt < 30000) return;
    const root = $('#database-overview-grid');
    if (!root) return;
    storageLoading = true;
    root.innerHTML = '<div class="empty-state">Atualizando métricas dos bancos...</div>';
    try {
      renderStorage(await request('/ui/api/observability/storage'));
      storageLoadedAt = Date.now();
    } catch (error) {
      root.innerHTML = `<div class="empty-state">${error.message}</div>`;
    } finally {
      storageLoading = false;
    }
  }

  function setupDashboard() {
    const view = $('#view-dashboard');
    const metrics = $('#metrics-grid', view || document);
    if (!view || !metrics) return;
    if (!$('#database-overview-panel')) {
      const panel = document.createElement('article');
      panel.id = 'database-overview-panel';
      panel.className = 'panel database-overview-panel';
      panel.innerHTML = `<div class="panel-header"><div><h3>Bancos e armazenamento</h3></div><button type="button" class="secondary-button" id="database-overview-refresh">Atualizar</button></div><div class="database-overview-grid" id="database-overview-grid"><div class="empty-state">Carregando métricas...</div></div>`;
      metrics.insertAdjacentElement('afterend', panel);
      $('#database-overview-refresh')?.addEventListener('click', () => void loadStorage(true));
    }
    if (view.classList.contains('active')) void loadStorage(false);
  }

  function setupInventory() {
    const view = $('#view-inventory');
    if (!view) return;
    const eyebrow = $('.panel-header>div:first-child>.eyebrow', view);
    if (eyebrow) eyebrow.remove();
    const title = $('.panel-header>div:first-child>h3', view);
    if (title) title.textContent = 'Inventário';
  }

  function openPlaybooksModal() {
    const ui = window.AgentCompactUI;
    const modal = $('#compact-playbooks-modal');
    const view = $('#view-playbooks');
    if (!ui || !modal || !view) return;
    view.classList.add('active');
    if (typeof loadPlaybookOptions === 'function') void loadPlaybookOptions().then(() => { if (typeof loadPlaybooks === 'function') void loadPlaybooks(); });
    else if (typeof loadPlaybooks === 'function') void loadPlaybooks();
    ui.open(modal);
    window.dispatchEvent(new CustomEvent('agent:playbooks-open'));
  }

  function setupPlaybooksModal() {
    const ui = window.AgentCompactUI;
    const inventory = $('#view-inventory');
    const view = $('#view-playbooks');
    if (!ui || !inventory || !view) return;
    const nav = $('.nav-item[data-view="playbooks"]');
    if (nav) nav.hidden = true;
    const modal = ui.modal('compact-playbooks-modal', 'Playbooks', '');
    const body = $('.compact-modal-body', modal);
    if (body && view.parentElement !== body) body.appendChild(view);

    let headActions = $('.playbook-modal-head-actions', modal);
    if (!headActions) {
      headActions = document.createElement('div');
      headActions.className = 'playbook-modal-head-actions';
      const close = $('.compact-modal-close', modal);
      close?.insertAdjacentElement('beforebegin', headActions);
    }
    const importButton = $('#import-playbook');
    const addButton = $('#add-playbook');
    if (importButton && importButton.parentElement !== headActions) headActions.appendChild(importButton);
    if (addButton && addButton.parentElement !== headActions) headActions.appendChild(addButton);

    if (!$('#inventory-playbooks')) {
      const filters = $('.filters', inventory) || $('.panel-header', inventory);
      const button = document.createElement('button');
      button.id = 'inventory-playbooks';
      button.type = 'button';
      button.className = 'secondary-button inventory-playbook-button';
      button.textContent = 'Playbooks';
      button.addEventListener('click', openPlaybooksModal);
      filters.appendChild(button);
    }
  }

  function setupN2History() {
    const view = $('#view-n2');
    const button = $('#n2-history-compact', view || document);
    const hostCard = $('.n2-host-card', view || document);
    const title = $('.n2-step-title', hostCard || document);
    if (!view || !button || !hostCard || !title) return;
    if (button.parentElement !== title) title.appendChild(button);
    const bar = $('.n2-compact-head', view);
    if (bar && !bar.children.length) bar.remove();
  }

  function setupAnalysisModal() {
    const modal = $('#analysis-modal');
    if (!modal || modal.dataset.refined === '1') return;
    modal.dataset.refined = '1';
    const observer = new MutationObserver(() => {
      if (modal.classList.contains('open')) {
        const panel = $('.analysis-modal-panel', modal);
        if (panel) panel.scrollTop = 0;
      }
    });
    observer.observe(modal, { attributes: true, attributeFilter: ['class'] });
  }

  function setup() {
    setupIncidentQueue();
    setupCheckmkHeading();
    setupAgentPolicies();
    setupDiscoveryLayout();
    setupGuardrails();
    setupDashboard();
    setupInventory();
    setupPlaybooksModal();
    setupN2History();
    setupAnalysisModal();
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('.nav-item[data-view="dashboard"]')) window.setTimeout(() => void loadStorage(false), 80);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup); else setup();
  window.setInterval(() => { if (!document.hidden) setup(); }, 1000);
})();
