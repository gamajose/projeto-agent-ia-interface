(() => {
  const $ = (selector, root = document) => root.querySelector(selector);

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

  function bindFilterButton(id, loaderName) {
    const button = document.getElementById(id);
    if (!button || button.dataset.refinedFilter === '1') return;
    button.dataset.refinedFilter = '1';
    button.type = 'button';
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const loader = window[loaderName];
      if (typeof loader === 'function') void loader();
    }, true);
  }

  function setupFilters() {
    bindFilterButton('filter-investigations', 'loadInvestigations');
    bindFilterButton('filter-inventory', 'loadInventory');
  }

  function setupPlaybookHeader() {
    const modal = $('#compact-playbooks-modal');
    const headActions = $('.playbook-modal-head-actions', modal || document);
    if (!modal || !headActions) return;
    const toolbar = $('#playbook-manager-toolbar');
    const pager = $('#playbook-manager-pager');
    if (toolbar && toolbar.parentElement !== headActions) headActions.appendChild(toolbar);
    if (pager && pager.parentElement !== headActions) headActions.appendChild(pager);
    modal.classList.add('playbook-single-header');
  }

  function setupAnalysisHeader() {
    const modal = $('#analysis-modal');
    if (!modal) return;
    $('#execution-mode-badge', modal)?.setAttribute('hidden', 'hidden');
    const heading = $('.analysis-modal-header>div:first-child', modal);
    if (heading) heading.hidden = true;
  }

  function openAccessMonitorModal() {
    const ui = window.AgentCompactUI;
    if (!ui) return;
    const modal = ui.modal('access-monitor-register-modal', 'Cadastrar servidor', '');
    const body = $('.compact-modal-body', modal);
    if (!body) return;
    body.innerHTML = `<form id="access-monitor-register-form" class="access-monitor-register-form">
      <label><span>Nome exibido</span><input id="access-monitor-register-label" maxlength="120" placeholder="Ex.: Monitor 2" required></label>
      <label><span>IP ou hostname</span><input id="access-monitor-register-host" maxlength="255" placeholder="10.17.181.2" required></label>
      <div id="access-monitor-register-message" class="inline-form-message"></div>
      <div class="compact-toolbar"><span class="compact-toolbar-spacer"></span><button type="button" class="secondary-button" data-cancel-access>Cancelar</button><button type="submit" class="primary-button">Salvar</button></div>
    </form>`;
    $('[data-cancel-access]', modal)?.addEventListener('click', () => ui.close(modal));
    $('#access-monitor-register-form', modal)?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const message = $('#access-monitor-register-message', modal);
      const submit = event.currentTarget.querySelector('button[type="submit"]');
      const label = $('#access-monitor-register-label', modal)?.value.trim() || '';
      const host = $('#access-monitor-register-host', modal)?.value.trim() || '';
      if (!label || !host) {
        if (message) message.textContent = 'Informe o nome e o IP/hostname do servidor.';
        return;
      }
      submit.disabled = true;
      try {
        const item = await request('/ui/api/access-monitors', { method: 'POST', body: { label, host } });
        if (typeof window.loadAccessMonitors === 'function') await window.loadAccessMonitors(item.id);
        if (typeof window.toast === 'function') window.toast(`${item.label} cadastrado.`);
        ui.close(modal);
      } catch (error) {
        if (message) message.textContent = error.message;
      } finally {
        submit.disabled = false;
      }
    }, { once: true });
    ui.open(modal);
    window.setTimeout(() => $('#access-monitor-register-label', modal)?.focus(), 30);
  }

  function setupAccessMonitorRegistration() {
    const button = $('#add-access-monitor');
    if (!button) return;
    button.textContent = 'Cadastrar servidor';
    const holder = button.closest('.access-monitor-register');
    const label = holder?.querySelector(':scope > span');
    if (label) label.hidden = true;
    if (button.dataset.modalRegistration === '1') return;
    button.dataset.modalRegistration = '1';
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      openAccessMonitorModal();
    }, true);
  }

  function setupMultiHost() {
    const scope = $('#multi-host-scope');
    const toggle = $('#multi-host-enabled', scope || document);
    const config = $('#multi-host-config', scope || document);
    const row = $('.multi-host-switch-row', scope || document);
    const add = $('#multi-host-add', scope || document);
    if (!scope || !toggle || !config || !row || !add) return;

    if (add.parentElement !== row) row.appendChild(add);
    add.textContent = 'Adicionar host';

    const switchNote = $('.multi-host-switch small', scope);
    if (switchNote) switchNote.remove();
    const head = $('.multi-host-head', config);
    if (head) {
      const eyebrow = $('.eyebrow', head);
      const title = $('h3', head);
      if (eyebrow) eyebrow.remove();
      if (title) title.remove();
      const paragraph = $('p', head);
      if (paragraph) paragraph.textContent = 'O host principal será o servidor de entrada.';
    }

    const sync = () => {
      const enabled = Boolean(toggle.checked);
      config.hidden = !enabled;
      add.hidden = !enabled;
      scope.classList.toggle('multi-host-expanded', enabled);
    };
    sync();
    if (toggle.dataset.refinedCollapse !== '1') {
      toggle.dataset.refinedCollapse = '1';
      toggle.addEventListener('change', () => window.setTimeout(sync, 0));
    }
  }

  function setupProjects() {
    const view = $('#view-projects');
    if (!view) return;
    $('.project-builder-head', view)?.setAttribute('hidden', 'hidden');
    $('.project-help', view)?.setAttribute('hidden', 'hidden');
    const execute = $('#project-generate', view);
    if (execute && !execute.disabled) execute.textContent = 'Executar';
    if (execute) execute.dataset.keepTextAction = '1';
  }

  function setupAgentPolicyScope() {
    const modal = $('#compact-agent-modal');
    const agent = $('#noc-agent-control', modal || document);
    const policies = $('#cmk-policy-panel');
    if (!modal || !agent || !policies) return;

    policies.classList.add('agent-policy-scope');
    policies.open = true;
    const summary = $('summary', policies);
    if (summary) summary.innerHTML = '<strong>Categorias autorizadas</strong><small>selecione o que os agentes podem tratar automaticamente</small>';

    const modeRow = $('.noc-mode-row', agent);
    const selectedScope = $('#noc-selected-scope', agent);
    if (selectedScope && policies.parentElement !== agent) agent.insertBefore(policies, selectedScope);
    else if (!selectedScope && modeRow && policies.parentElement !== agent) modeRow.insertAdjacentElement('afterend', policies);

    $('#compact-policy-button')?.remove();
    const oldModal = $('#compact-policy-modal');
    if (oldModal && !oldModal.querySelector('#cmk-policy-panel')) oldModal.remove();

    const skills = $('#skills-manager-button');
    const power = $('.noc-agent-power', agent);
    if (skills && power) {
      skills.hidden = false;
      skills.classList.remove('compact-hidden');
      skills.textContent = 'Skills';
      if (skills.parentElement !== power) power.prepend(skills);
    }
  }

  function checkedValues(selector, attribute) {
    return [...document.querySelectorAll(`${selector}:checked`)]
      .map((item) => String(item.getAttribute(attribute) || '').trim())
      .filter(Boolean);
  }

  function setupAgentSelectedModePersistence() {
    const hostSearch = $('#noc-host-search');
    if (hostSearch) {
      hostSearch.placeholder = 'Buscar por IP ou nome';
      hostSearch.setAttribute('aria-label', 'Buscar host por IP ou nome');
      hostSearch.setAttribute('title', 'A pesquisa aceita IP ou nome do host');
    }

    document.querySelectorAll('#noc-agent-control [data-noc-mode]').forEach((button) => {
      if (button.dataset.persistInactiveMode === '1') return;
      button.dataset.persistInactiveMode = '1';
      button.addEventListener('click', () => {
        window.setTimeout(async () => {
          const toggle = $('#noc-agent-toggle');
          if (toggle?.checked) return;
          const mode = String(button.dataset.nocMode || '').trim();
          if (!['automatic', 'selected'].includes(mode)) return;
          const message = $('#noc-scope-message');
          try {
            const current = await request('/ui/api/noc/autonomy');
            const hasRenderedScope = Boolean(document.querySelector('[data-noc-site]'));
            const sites = hasRenderedScope ? checkedValues('[data-noc-site]', 'data-noc-site') : [...(current.sites || [])];
            const hosts = hasRenderedScope ? checkedValues('[data-noc-host]', 'data-noc-host') : [...(current.hosts || [])];
            const problemKeys = hasRenderedScope ? checkedValues('[data-noc-problem]', 'data-noc-problem') : [...(current.problem_keys || [])];
            await request('/ui/api/noc/autonomy', {
              method: 'POST',
              body: {
                enabled: false,
                mode,
                sites,
                hosts,
                problem_keys: problemKeys,
              },
            });
            if (message && mode === 'selected') {
              message.textContent = 'Modo selecionado salvo. Escolha o cliente, host ou sensor e depois ligue os agentes quando quiser.';
              message.classList.remove('error');
            }
          } catch (error) {
            if (message) {
              message.textContent = error.message;
              message.classList.add('error');
            }
          }
        }, 0);
      });
    });
  }

  function setup() {
    setupFilters();
    setupPlaybookHeader();
    setupAnalysisHeader();
    setupAccessMonitorRegistration();
    setupMultiHost();
    setupProjects();
    setupAgentPolicyScope();
    setupAgentSelectedModePersistence();
  }

  document.addEventListener('DOMContentLoaded', () => window.setTimeout(setup, 0));
  document.addEventListener('click', (event) => {
    if (event.target.closest('#inventory-playbooks, #compact-agent-button, [data-open-analysis], .nav-item[data-view="projects"]')) {
      window.setTimeout(setup, 40);
    }
  });
  window.setInterval(() => { if (!document.hidden) setup(); }, 900);
})();
