(() => {
  const state = {
    overview: { sites: [], problems: [] },
    skills: [],
    playbooks: [],
    siteDetails: {},
    selectedSites: new Set(),
    selectedHosts: new Set(),
    selectedProblems: new Set(),
    runId: '',
    pollTimer: null,
    loading: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

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

  function ensureModal() {
    const ui = window.AgentCompactUI;
    if (!ui) return null;
    const modal = ui.modal('noc-manual-execution-modal', 'Execução manual', '');
    $('.compact-modal-head p', modal)?.remove();
    if (modal.dataset.manualReady === '1') return modal;
    modal.dataset.manualReady = '1';
    modal.classList.add('noc-manual-modal');
    const body = $('.compact-modal-body', modal);
    body.innerHTML = `
      <section class="noc-manual-guidance">
        <div><strong>Playbook</strong><select id="noc-manual-playbook"><option value="">Automático · IA escolhe</option></select></div>
        <div><strong>Skill</strong><select id="noc-manual-skill"><option value="">Automático · IA identifica</option></select></div>
        <small>Se você escolher manualmente, a execução fica presa a esse conhecimento. Sem seleção, a IA identifica o problema, escolhe a NOC Master Skill e o procedimento correspondente.</small>
        <div class="noc-manual-prescription">
          <strong>Instrução explícita do operador <em>opcional</em></strong>
          <textarea id="noc-manual-operator-instruction" maxlength="4000" rows="3" placeholder="Ex.: systemctl stop postgresql.service; depois systemctl start postgresql.service; depois reiniciar o servidor"></textarea>
          <small>Quando uma ação operacional estiver escrita explicitamente aqui, ela vira uma prescrição do operador e tem precedência sobre o veto genérico do Ansible. Ações não prescritas continuam seguindo a política normal.</small>
        </div>
      </section>
      <section class="noc-manual-scope">
        <header class="noc-manual-summary">
          <div><strong>Escopo de atuação</strong><span>Cliente → host → sensor/erro</span></div>
          <div id="noc-manual-counts" class="noc-manual-counts"></div>
          <button type="button" class="ghost-button" id="noc-manual-clear">Limpar</button>
        </header>
        <div class="noc-manual-grid">
          <section class="noc-manual-column">
            <header><b>1</b><div><strong>Clientes</strong><small>Escolha um ou mais sites.</small></div></header>
            <input id="noc-manual-site-search" type="search" placeholder="Pesquisar cliente">
            <div id="noc-manual-sites" class="noc-manual-list"></div>
          </section>
          <section class="noc-manual-column">
            <header><b>2</b><div><strong>Hosts</strong><small>Busca por IP ou nome.</small></div></header>
            <input id="noc-manual-host-search" type="search" placeholder="Buscar por IP ou nome">
            <div id="noc-manual-hosts" class="noc-manual-list"></div>
          </section>
          <section class="noc-manual-column">
            <header><b>3</b><div><strong>Sensores / erros</strong><small>Escolha o problema que deve ser resolvido.</small></div></header>
            <input id="noc-manual-problem-search" type="search" placeholder="Pesquisar sensor ou erro">
            <div id="noc-manual-problems" class="noc-manual-list"></div>
          </section>
        </div>
        <footer class="noc-manual-actions">
          <div id="noc-manual-message">A execução manual não liga o modo automático.</div>
          <button type="button" class="primary-button" id="noc-manual-run">Arrumar selecionados</button>
        </footer>
        <div id="noc-manual-run-status" class="noc-manual-run-status" hidden></div>
      </section>`;

    ['#noc-manual-site-search', '#noc-manual-host-search', '#noc-manual-problem-search'].forEach((selector) => {
      $(selector, modal)?.addEventListener('input', renderScope);
    });
    $('#noc-manual-clear', modal)?.addEventListener('click', () => {
      state.selectedSites.clear();
      state.selectedHosts.clear();
      state.selectedProblems.clear();
      renderScope();
    });
    $('#noc-manual-run', modal)?.addEventListener('click', () => void runSelected());
    $('#noc-manual-skill', modal)?.addEventListener('change', () => {
      const skill = state.skills.find((item) => String(item.id || '') === String($('#noc-manual-skill', modal)?.value || ''));
      const playbook = $('#noc-manual-playbook', modal);
      if (skill?.playbook_id && playbook && !playbook.value) playbook.value = String(skill.playbook_id);
    });
    return modal;
  }

  function forceAutomaticControl() {
    if (document.body.dataset.nocManualAutoNormalized === '1') return;
    document.body.dataset.nocManualAutoNormalized = '1';
    void request('/ui/api/noc/autonomy').then((control) => {
      if (String(control.mode || 'automatic') === 'automatic') return;
      return request('/ui/api/noc/autonomy', {
        method: 'POST',
        body: { enabled: Boolean(control.enabled), mode: 'automatic', sites: [], hosts: [], problem_keys: [] },
      });
    }).catch(() => {});
  }

  function setupMainControl() {
    const agent = $('#noc-agent-control');
    const power = $('.noc-agent-power', agent || document);
    if (!agent || !power) return false;
    $('.noc-mode-row', agent)?.setAttribute('hidden', 'hidden');
    $('#noc-selected-scope', agent)?.setAttribute('hidden', 'hidden');
    if (!$('#noc-manual-button', power)) {
      const button = document.createElement('button');
      button.type = 'button';
      button.id = 'noc-manual-button';
      button.className = 'secondary-button noc-manual-button';
      button.textContent = 'Manual';
      button.title = 'Escolher cliente, host, sensor e instrução para uma correção pontual';
      button.addEventListener('click', () => void openManual());
      const skills = $('#skills-manager-button', power);
      if (skills) power.insertBefore(button, skills);
      else power.prepend(button);
    }
    const label = $('#noc-agent-toggle-label', power);
    if (label) label.textContent = $('#noc-agent-toggle', power)?.checked ? 'Automático ligado' : 'Automático desligado';
    const toggle = $('#noc-agent-toggle', power);
    if (toggle && toggle.dataset.automaticOnly !== '1') {
      toggle.dataset.automaticOnly = '1';
      toggle.title = 'Liga ou desliga somente o modo Automático. A execução Manual é independente.';
      toggle.addEventListener('change', () => window.setTimeout(forceAutomaticControl, 0));
    }
    forceAutomaticControl();
    return true;
  }

  async function openManual() {
    const ui = window.AgentCompactUI;
    const modal = ensureModal();
    if (!ui || !modal) return;
    ui.open(modal);
    await loadCatalogs();
    await Promise.all([...state.selectedSites].map((siteId) => loadSiteDetail(siteId, true)));
    renderScope();
    window.setTimeout(() => $('#noc-manual-site-search', modal)?.focus(), 30);
  }

  async function loadCatalogs() {
    if (state.loading) return;
    state.loading = true;
    try {
      const [overview, skills, playbooks] = await Promise.all([
        request('/ui/api/noc/checkmk-master/overview'),
        request('/ui/api/noc/skills'),
        request('/ui/api/playbooks/manage'),
      ]);
      state.overview = overview || { sites: [], problems: [] };
      state.skills = Array.isArray(skills.items) ? skills.items : [];
      state.playbooks = Array.isArray(playbooks.items) ? playbooks.items : [];
      renderCatalogs();
    } catch (error) {
      setMessage(error.message, true);
    } finally {
      state.loading = false;
    }
  }

  function renderCatalogs() {
    const modal = ensureModal();
    if (!modal) return;
    const skill = $('#noc-manual-skill', modal);
    const playbook = $('#noc-manual-playbook', modal);
    const skillValue = skill?.value || '';
    const playbookValue = playbook?.value || '';
    if (skill) {
      skill.innerHTML = '<option value="">Automático · NOC Master identifica</option>' + state.skills.map((item) => `<option value="${esc(item.id)}">${esc(item.title || item.id)}${item.playbook_id ? ` · ${esc(item.playbook_id)}` : ''}</option>`).join('');
      skill.value = skillValue;
    }
    if (playbook) {
      playbook.innerHTML = '<option value="">Automático · IA escolhe</option>' + state.playbooks.map((item) => `<option value="${esc(item.id)}">${esc(item.title || item.id)}</option>`).join('');
      playbook.value = playbookValue;
    }
  }

  const query = (selector) => String($(selector, ensureModal())?.value || '').trim().toLocaleLowerCase('pt-BR');

  async function loadSiteDetail(siteId, force = false) {
    if (!siteId || (!force && state.siteDetails[siteId])) return;
    try {
      state.siteDetails[siteId] = await request(`/ui/api/noc/checkmk-master/sites/${encodeURIComponent(siteId)}`);
    } catch (error) {
      state.siteDetails[siteId] = { hosts: [], problems: [], error: error.message };
    }
    renderScope();
  }

  function siteItems() {
    const q = query('#noc-manual-site-search');
    return (state.overview.sites || []).filter((item) => item.enabled !== false).filter((item) => !q || `${item.alias || ''} ${item.site_id || ''}`.toLocaleLowerCase('pt-BR').includes(q));
  }

  function hostItems() {
    const q = query('#noc-manual-host-search');
    const map = new Map();
    state.selectedSites.forEach((siteId) => {
      (state.siteDetails[siteId]?.hosts || []).forEach((item) => {
        const host = String(item.host_name || item.host || '').trim();
        if (!host) return;
        map.set(`${siteId}|${host}`, {
          site_id: siteId,
          host,
          address: String(item.internal_address || item.host_address || ''),
          count: Number(item.problem_count || 0),
        });
      });
    });
    return [...map.values()].sort((a, b) => a.host.localeCompare(b.host, 'pt-BR')).filter((item) => !q || `${item.host} ${item.address}`.toLocaleLowerCase('pt-BR').includes(q));
  }

  function problemItems() {
    const q = query('#noc-manual-problem-search');
    const map = new Map();
    state.selectedSites.forEach((siteId) => {
      const detail = state.siteDetails[siteId];
      const items = Array.isArray(detail?.problems)
        ? detail.problems
        : (state.overview.problems || []).filter((item) => String(item.site_id || '') === String(siteId));
      items.forEach((item) => {
        const key = String(item.problem_key || `${siteId}|${item.host || ''}|${item.service || ''}`);
        map.set(key, item);
      });
    });
    return [...map.values()].filter((item) => {
      if (state.selectedHosts.size && !state.selectedHosts.has(String(item.host || ''))) return false;
      if (!q) return true;
      return `${item.host || ''} ${item.host_address || ''} ${item.service || ''} ${item.output || ''}`.toLocaleLowerCase('pt-BR').includes(q);
    });
  }

  function pruneProblemSelection() {
    const valid = new Set(problemItems().map((item) => String(item.problem_key || '')));
    [...state.selectedProblems].forEach((problemKey) => {
      if (!valid.has(problemKey)) state.selectedProblems.delete(problemKey);
    });
  }

  function renderScope() {
    const modal = ensureModal();
    if (!modal) return;
    const sites = $('#noc-manual-sites', modal);
    const hosts = $('#noc-manual-hosts', modal);
    const problems = $('#noc-manual-problems', modal);

    if (sites) {
      const items = siteItems();
      sites.innerHTML = items.length ? items.map((item) => `
        <label class="noc-manual-option"><input type="checkbox" data-manual-site="${esc(item.site_id)}" ${state.selectedSites.has(String(item.site_id)) ? 'checked' : ''}><span><strong>${esc(item.alias || item.site_id)}</strong><small>${esc(item.site_id)} · ${esc(item.host_count || 0)} host(s) · ${esc(item.problem_count || 0)} erro(s)</small></span></label>`).join('') : '<div class="noc-manual-empty">Nenhum cliente encontrado.</div>';
      $$('[data-manual-site]', sites).forEach((input) => input.addEventListener('change', () => {
        const id = String(input.dataset.manualSite || '');
        if (input.checked) {
          state.selectedSites.add(id);
          void loadSiteDetail(id, true);
        } else {
          state.selectedSites.delete(id);
        }
        const validHosts = new Set(hostItems().map((item) => item.host));
        [...state.selectedHosts].forEach((host) => { if (!validHosts.has(host)) state.selectedHosts.delete(host); });
        pruneProblemSelection();
        renderScope();
      }));
    }

    if (hosts) {
      if (!state.selectedSites.size) hosts.innerHTML = '<div class="noc-manual-empty">Selecione primeiro o cliente.</div>';
      else {
        const items = hostItems();
        hosts.innerHTML = items.length ? items.map((item) => `
          <label class="noc-manual-option"><input type="checkbox" data-manual-host="${esc(item.host)}" ${state.selectedHosts.has(item.host) ? 'checked' : ''}><span><strong>${esc(item.host)}</strong><small>${esc(item.address || 'sem IP')} · ${item.count ? `${esc(item.count)} erro(s)` : 'sem erro ativo'}</small></span></label>`).join('') : '<div class="noc-manual-empty">Carregando ou nenhum host encontrado.</div>';
        $$('[data-manual-host]', hosts).forEach((input) => input.addEventListener('change', () => {
          if (input.checked) state.selectedHosts.add(String(input.dataset.manualHost || '')); else state.selectedHosts.delete(String(input.dataset.manualHost || ''));
          pruneProblemSelection();
          renderScope();
        }));
      }
    }

    if (problems) {
      const items = problemItems();
      problems.innerHTML = state.selectedSites.size && items.length ? items.map((item) => `
        <label class="noc-manual-option noc-manual-problem"><input type="checkbox" data-manual-problem="${esc(item.problem_key)}" ${state.selectedProblems.has(String(item.problem_key || '')) ? 'checked' : ''}><span><strong>${esc(item.service || 'Sensor')}</strong><small>${esc(item.host || '')} · ${esc(String(item.output || '').slice(0, 110))}</small></span></label>`).join('') : '<div class="noc-manual-empty">Nenhum erro ativo para o escopo selecionado.</div>';
      $$('[data-manual-problem]', problems).forEach((input) => input.addEventListener('change', () => {
        if (input.checked) state.selectedProblems.add(String(input.dataset.manualProblem || '')); else state.selectedProblems.delete(String(input.dataset.manualProblem || ''));
        renderCounts();
      }));
    }
    renderCounts();
  }

  function renderCounts() {
    const root = $('#noc-manual-counts', ensureModal());
    if (!root) return;
    root.innerHTML = `<span>${state.selectedSites.size} cliente(s)</span><span>${state.selectedHosts.size || 'todos'} host(s)</span><span>${state.selectedProblems.size || 'todos'} sensor(es)</span>`;
  }

  function setMessage(message, error = false) {
    const root = $('#noc-manual-message', ensureModal());
    if (!root) return;
    root.textContent = message;
    root.classList.toggle('error', Boolean(error));
  }

  async function runSelected() {
    const modal = ensureModal();
    if (!state.selectedSites.size) return setMessage('Selecione pelo menos um cliente.', true);
    const button = $('#noc-manual-run', modal);
    if (button) { button.disabled = true; button.textContent = 'Iniciando...'; }
    try {
      const operatorInstruction = String($('#noc-manual-operator-instruction', modal)?.value || '').trim();
      const run = await request('/ui/api/noc/autonomy/run-selected', {
        method: 'POST',
        body: {
          sites: [...state.selectedSites],
          hosts: [...state.selectedHosts],
          problem_keys: [...state.selectedProblems],
          playbook_id: String($('#noc-manual-playbook', modal)?.value || '') || null,
          skill_id: String($('#noc-manual-skill', modal)?.value || '') || null,
          operator_instruction: operatorInstruction || null,
        },
      });
      state.runId = String(run.id || '');
      setMessage(operatorInstruction
        ? 'Execução manual iniciada com prescrição explícita do operador. O modo Automático não foi alterado.'
        : 'Execução manual iniciada. O modo Automático não foi alterado.');
      renderRun(run);
      pollRun();
    } catch (error) {
      setMessage(error.message, true);
    } finally {
      if (button) { button.disabled = false; button.textContent = 'Arrumar selecionados'; }
    }
  }

  function statusLabel(status) {
    const labels = { queued: 'Aguardando worker', running: 'IA trabalhando', completed: 'Resolvido', failed: 'Não corrigido', cancelled: 'Cancelado' };
    return labels[String(status || '')] || String(status || 'Aguardando');
  }

  function renderRun(run) {
    const root = $('#noc-manual-run-status', ensureModal());
    if (!root) return;
    root.hidden = false;
    const jobs = Array.isArray(run.jobs) ? run.jobs : [];
    const progress = run.progress || {};
    if (!jobs.length) {
      root.innerHTML = `<article><header><strong>${esc(statusLabel(run.status))}</strong><span>${run.queue_position ? `posição ${esc(run.queue_position)}` : ''}</span></header><div class="noc-manual-progress"><i style="width:${esc(progress.percent || 0)}%"></i></div><small>Preparando a execução selecionada.</small></article>`;
      return;
    }
    root.innerHTML = jobs.map((job) => `
      <article data-status="${esc(job.status || 'queued')}">
        <header><div><strong>${esc(job.host || '')} · ${esc(job.service || '')}</strong><small>${esc(job.host_address || '')}</small></div><span>${esc(statusLabel(job.status))}</span></header>
        <div class="noc-manual-progress"><i style="width:${esc(job.percent || 0)}%"></i></div>
        <footer><small>${esc(job.detail || '')}</small><b>${esc(job.percent || 0)}%</b></footer>
      </article>`).join('');
  }

  function pollRun() {
    if (state.pollTimer) window.clearInterval(state.pollTimer);
    if (!state.runId) return;
    state.pollTimer = window.setInterval(async () => {
      try {
        const run = await request(`/ui/api/noc/autonomy/runs/${encodeURIComponent(state.runId)}`);
        renderRun(run);
        if (['completed', 'failed', 'cancelled'].includes(String(run.status || ''))) {
          window.clearInterval(state.pollTimer);
          state.pollTimer = null;
          await loadCatalogs();
          await Promise.all([...state.selectedSites].map((siteId) => loadSiteDetail(siteId, true)));
        }
      } catch (error) {
        setMessage(error.message, true);
        window.clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    }, 1600);
  }

  function setup() {
    setupMainControl();
    ensureModal();
  }

  document.addEventListener('DOMContentLoaded', () => window.setTimeout(setup, 80));
  document.addEventListener('click', (event) => {
    if (event.target.closest('#compact-agent-button')) window.setTimeout(setup, 40);
  });
  window.setInterval(() => { if (!document.hidden) setup(); }, 900);
  window.addEventListener('beforeunload', () => { if (state.pollTimer) window.clearInterval(state.pollTimer); });
})();