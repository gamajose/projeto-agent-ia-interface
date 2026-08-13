(() => {
  const state = {
    ready: false,
    loading: false,
    control: { enabled: false, mode: "automatic", sites: [], hosts: [], problem_keys: [] },
    overview: { sites: [], problems: [], summary: {} },
    skills: [],
    selectedSites: new Set(),
    selectedHosts: new Set(),
    selectedProblems: new Set(),
    selectionInitialized: false,
    runId: "",
    runTimer: null,
    refreshTimer: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  async function request(path, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const headers = { ...(options.headers || {}) };
    let body = options.body;
    if (method !== "GET") headers["X-Agent-UI"] = "1";
    if (body && typeof body === "object") {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, method, headers, body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  function isNocActive() {
    return Boolean($("#view-noc")?.classList.contains("active"));
  }

  function ensurePanel() {
    const fleet = $("#noc-fleet-panel");
    if (!fleet) return false;
    if ($("#noc-agent-control")) return true;

    const panel = document.createElement("section");
    panel.id = "noc-agent-control";
    panel.className = "noc-agent-control";
    panel.innerHTML = `
      <div class="noc-agent-head">
        <div class="noc-agent-title">
          <span class="noc-agent-orb" aria-hidden="true"></span>
          <div><p class="eyebrow">CONTROLE DOS AGENTES</p><h3>Atuação autônoma</h3><small>O Checkmk continua lendo inventário e alertas mesmo com os agentes desligados.</small></div>
        </div>
        <div class="noc-agent-power">
          <span class="noc-agent-state" id="noc-agent-state">OBSERVAÇÃO</span>
          <label class="noc-power-switch" title="Autorizar ou bloquear acesso autônomo aos ambientes">
            <input type="checkbox" id="noc-agent-toggle" aria-label="Ligar ou desligar agentes autônomos">
            <i></i><span id="noc-agent-toggle-label">Desligado</span>
          </label>
        </div>
      </div>

      <div class="noc-mode-row">
        <div class="noc-mode-selector" role="group" aria-label="Modo de atuação">
          <button type="button" data-noc-mode="automatic">Automático</button>
          <button type="button" data-noc-mode="selected">Selecionado</button>
        </div>
        <div class="noc-mode-copy" id="noc-mode-copy"></div>
      </div>

      <div class="noc-selected-scope" id="noc-selected-scope" hidden>
        <div class="noc-scope-summary">
          <div><strong>Escopo de atuação</strong><span>Cliente → host → sensor/erro</span></div>
          <div class="noc-scope-counts" id="noc-scope-counts"></div>
          <button type="button" class="ghost-button" id="noc-clear-scope">Limpar</button>
        </div>
        <div class="noc-scope-grid">
          <section class="noc-scope-column">
            <header><b>1</b><div><strong>Clientes</strong><small>Escolha um ou mais sites.</small></div></header>
            <input class="noc-scope-search" id="noc-site-search" type="search" placeholder="Pesquisar cliente">
            <div class="noc-scope-list" id="noc-scope-sites"></div>
          </section>
          <section class="noc-scope-column">
            <header><b>2</b><div><strong>Hosts</strong><small>Sem host marcado = todos do cliente.</small></div></header>
            <input class="noc-scope-search" id="noc-host-search" type="search" placeholder="Pesquisar host">
            <div class="noc-scope-list" id="noc-scope-hosts"></div>
          </section>
          <section class="noc-scope-column">
            <header><b>3</b><div><strong>Sensores / erros</strong><small>Sem sensor marcado = todos dos hosts.</small></div></header>
            <input class="noc-scope-search" id="noc-problem-search" type="search" placeholder="Pesquisar sensor ou erro">
            <div class="noc-scope-list" id="noc-scope-problems"></div>
          </section>
        </div>
        <div class="noc-scope-actions">
          <div id="noc-scope-message">A atuação pontual funciona mesmo com a chave principal desligada.</div>
          <button type="button" class="secondary-button" id="noc-apply-scope">Salvar escopo</button>
          <button type="button" class="primary-button" id="noc-run-selected">Arrumar selecionados</button>
        </div>
        <div class="noc-selected-run" id="noc-selected-run" hidden></div>
      </div>

      <div class="noc-skill-zone">
        <div class="noc-skill-head"><div><p class="eyebrow">ESPECIALISTAS</p><h4>Skills dos agentes</h4></div><span id="noc-skill-count"></span></div>
        <div class="noc-skill-strip" id="noc-skill-strip"><div class="empty-state">Carregando skills...</div></div>
      </div>`;

    const masterHead = fleet.querySelector(".cmk-master-head");
    if (masterHead) masterHead.insertAdjacentElement("beforebegin", panel);
    else fleet.prepend(panel);

    $("#noc-agent-toggle")?.addEventListener("change", () => void toggleAgents());
    $$('[data-noc-mode]').forEach((button) => button.addEventListener("click", () => void changeMode(button.dataset.nocMode)));
    $("#noc-clear-scope")?.addEventListener("click", clearScope);
    $("#noc-apply-scope")?.addEventListener("click", () => void saveControl());
    $("#noc-run-selected")?.addEventListener("click", () => void runSelected());
    ["#noc-site-search", "#noc-host-search", "#noc-problem-search"].forEach((selector) => {
      $(selector)?.addEventListener("input", renderScope);
    });

    const sync = $("#cmk-sync");
    const poll = $("#cmk-poll");
    if (sync) sync.textContent = "Sincronizar dados";
    if (poll) poll.textContent = "Atualizar problemas";
    return true;
  }

  function initializeSelection() {
    if (state.selectionInitialized) return;
    state.selectedSites = new Set(state.control.sites || []);
    state.selectedHosts = new Set(state.control.hosts || []);
    state.selectedProblems = new Set(state.control.problem_keys || []);
    state.selectionInitialized = true;
  }

  function renderControl() {
    if (!ensurePanel()) return;
    initializeSelection();
    const enabled = Boolean(state.control.enabled);
    const mode = state.control.mode === "selected" ? "selected" : "automatic";
    const toggle = $("#noc-agent-toggle");
    if (toggle) toggle.checked = enabled;
    const label = $("#noc-agent-toggle-label");
    if (label) label.textContent = enabled ? "Ligado" : "Desligado";
    const badge = $("#noc-agent-state");
    if (badge) {
      badge.textContent = enabled ? (mode === "automatic" ? "ATUANDO · AUTO" : "ATUANDO · FILTRADO") : "OBSERVAÇÃO";
      badge.dataset.active = enabled ? "1" : "0";
    }
    $$('[data-noc-mode]').forEach((button) => button.classList.toggle("active", button.dataset.nocMode === mode));
    const selected = $("#noc-selected-scope");
    if (selected) selected.hidden = mode !== "selected";
    const copy = $("#noc-mode-copy");
    if (copy) copy.innerHTML = mode === "automatic"
      ? '<strong>Automático</strong><span>Quando ligado, os agentes avaliam todos os problemas elegíveis pelas políticas.</span>'
      : '<strong>Selecionado</strong><span>Você define exatamente cliente, host e, se quiser, o sensor que pode ser tratado.</span>';
    renderScope();
    renderTopStrip();
  }

  function normalizedQuery(selector) {
    return String($(selector)?.value || "").trim().toLocaleLowerCase("pt-BR");
  }

  function siteItems() {
    const query = normalizedQuery("#noc-site-search");
    return (state.overview.sites || [])
      .filter((site) => site.enabled !== false)
      .filter((site) => !query || `${site.alias || ""} ${site.site_id || ""}`.toLocaleLowerCase("pt-BR").includes(query));
  }

  function problemItems() {
    return Array.isArray(state.overview.problems) ? state.overview.problems : [];
  }

  function hostItems() {
    if (!state.selectedSites.size) return [];
    const query = normalizedQuery("#noc-host-search");
    const map = new Map();
    problemItems()
      .filter((item) => state.selectedSites.has(String(item.site_id || "")))
      .forEach((item) => {
        const host = String(item.host || "").trim();
        if (!host) return;
        const key = `${item.site_id}|${host}`;
        if (!map.has(key)) map.set(key, {
          site_id: String(item.site_id || ""),
          host,
          address: String(item.host_address || ""),
          count: 0,
        });
        map.get(key).count += 1;
      });
    return [...map.values()].filter((item) => !query || `${item.host} ${item.address} ${item.site_id}`.toLocaleLowerCase("pt-BR").includes(query));
  }

  function scopedProblems() {
    if (!state.selectedSites.size) return [];
    const query = normalizedQuery("#noc-problem-search");
    return problemItems().filter((item) => {
      if (!state.selectedSites.has(String(item.site_id || ""))) return false;
      if (state.selectedHosts.size && !state.selectedHosts.has(String(item.host || ""))) return false;
      if (!query) return true;
      return `${item.host || ""} ${item.service || ""} ${item.output || ""}`.toLocaleLowerCase("pt-BR").includes(query);
    });
  }

  function pruneSelection() {
    const allowedHosts = new Set(hostItems().map((item) => item.host));
    if (state.selectedSites.size) {
      [...state.selectedHosts].forEach((host) => { if (!allowedHosts.has(host)) state.selectedHosts.delete(host); });
      const allowedProblems = new Set(scopedProblems().map((item) => String(item.problem_key || "")));
      [...state.selectedProblems].forEach((key) => { if (!allowedProblems.has(key)) state.selectedProblems.delete(key); });
    } else {
      state.selectedHosts.clear();
      state.selectedProblems.clear();
    }
  }

  function renderScope() {
    if (!$("#noc-selected-scope") || state.control.mode !== "selected") return;
    pruneSelection();
    const sitesRoot = $("#noc-scope-sites");
    const hostsRoot = $("#noc-scope-hosts");
    const problemsRoot = $("#noc-scope-problems");

    if (sitesRoot) {
      const items = siteItems();
      sitesRoot.innerHTML = items.length ? items.map((site) => `
        <label class="noc-scope-option">
          <input type="checkbox" data-noc-site="${esc(site.site_id)}" ${state.selectedSites.has(String(site.site_id)) ? "checked" : ""}>
          <span><strong>${esc(site.alias || site.site_id)}</strong><small>${esc(site.site_id)} · ${esc(site.problem_count || 0)} erro(s)</small></span>
        </label>`).join("") : '<div class="noc-scope-empty">Nenhum cliente encontrado.</div>';
      $$('[data-noc-site]', sitesRoot).forEach((input) => input.addEventListener("change", () => {
        if (input.checked) state.selectedSites.add(input.dataset.nocSite); else state.selectedSites.delete(input.dataset.nocSite);
        renderScope();
      }));
    }

    if (hostsRoot) {
      if (!state.selectedSites.size) {
        hostsRoot.innerHTML = '<div class="noc-scope-empty">Selecione primeiro o cliente.</div>';
      } else {
        const items = hostItems();
        hostsRoot.innerHTML = items.length ? items.map((item) => `
          <label class="noc-scope-option">
            <input type="checkbox" data-noc-host="${esc(item.host)}" ${state.selectedHosts.has(item.host) ? "checked" : ""}>
            <span><strong>${esc(item.host)}</strong><small>${esc(item.address || "sem IP")} · ${esc(item.count)} erro(s)</small></span>
          </label>`).join("") : '<div class="noc-scope-empty">Nenhum host com erro ativo.</div>';
        $$('[data-noc-host]', hostsRoot).forEach((input) => input.addEventListener("change", () => {
          if (input.checked) state.selectedHosts.add(input.dataset.nocHost); else state.selectedHosts.delete(input.dataset.nocHost);
          renderScope();
        }));
      }
    }

    if (problemsRoot) {
      const items = scopedProblems();
      problemsRoot.innerHTML = state.selectedSites.size && items.length ? items.map((item) => `
        <label class="noc-scope-option noc-problem-option">
          <input type="checkbox" data-noc-problem="${esc(item.problem_key)}" ${state.selectedProblems.has(String(item.problem_key || "")) ? "checked" : ""}>
          <span><strong>${esc(item.service || "Sensor")}</strong><small>${esc(item.host || "")} · ${esc(String(item.output || "").slice(0, 80))}</small></span>
        </label>`).join("") : '<div class="noc-scope-empty">Selecione um cliente para listar os erros.</div>';
      $$('[data-noc-problem]', problemsRoot).forEach((input) => input.addEventListener("change", () => {
        if (input.checked) state.selectedProblems.add(input.dataset.nocProblem); else state.selectedProblems.delete(input.dataset.nocProblem);
        renderCounts();
      }));
    }
    renderCounts();
  }

  function renderCounts() {
    const root = $("#noc-scope-counts");
    if (!root) return;
    root.innerHTML = `<span>${state.selectedSites.size} cliente(s)</span><span>${state.selectedHosts.size || "todos"} host(s)</span><span>${state.selectedProblems.size || "todos"} sensor(es)</span>`;
  }

  function clearScope() {
    state.selectedSites.clear();
    state.selectedHosts.clear();
    state.selectedProblems.clear();
    renderScope();
  }

  function scopePayload(enabled = state.control.enabled) {
    return {
      enabled: Boolean(enabled),
      mode: state.control.mode === "selected" ? "selected" : "automatic",
      sites: [...state.selectedSites],
      hosts: [...state.selectedHosts],
      problem_keys: [...state.selectedProblems],
    };
  }

  async function saveControl(options = {}) {
    const payload = scopePayload(options.enabled ?? state.control.enabled);
    if (payload.enabled && payload.mode === "selected" && !payload.sites.length) {
      throw new Error("Selecione pelo menos um cliente antes de ligar o modo selecionado.");
    }
    state.control = await request("/ui/api/noc/autonomy", { method: "POST", body: payload });
    state.selectionInitialized = true;
    renderControl();
    return state.control;
  }

  async function toggleAgents() {
    const toggle = $("#noc-agent-toggle");
    try {
      await saveControl({ enabled: Boolean(toggle?.checked) });
      flash(Boolean(toggle?.checked)
        ? "Agentes autorizados no escopo exibido."
        : "Agentes desligados. O Checkmk permanece somente observando.");
    } catch (error) {
      if (toggle) toggle.checked = Boolean(state.control.enabled);
      flash(error.message, true);
    }
  }

  async function changeMode(mode) {
    if (!['automatic', 'selected'].includes(mode)) return;
    state.control = { ...state.control, mode };
    renderControl();
    if (state.control.enabled) {
      try {
        await saveControl({ enabled: true });
      } catch (error) {
        state.control = { ...state.control, mode: mode === 'automatic' ? 'selected' : 'automatic' };
        renderControl();
        flash(error.message, true);
      }
    }
  }

  function flash(message, error = false) {
    const root = $("#noc-scope-message");
    if (!root) return;
    root.textContent = message;
    root.classList.toggle("error", Boolean(error));
  }

  async function runSelected() {
    if (!state.selectedSites.size) {
      flash("Selecione pelo menos um cliente para a atuação pontual.", true);
      return;
    }
    const button = $("#noc-run-selected");
    if (button) { button.disabled = true; button.textContent = "Enfileirando..."; }
    try {
      const run = await request("/ui/api/noc/autonomy/run-selected", {
        method: "POST",
        body: {
          sites: [...state.selectedSites],
          hosts: [...state.selectedHosts],
          problem_keys: [...state.selectedProblems],
        },
      });
      state.runId = String(run.id || "");
      renderRun(run);
      pollRun();
      flash("Escopo enviado ao worker. Só os itens selecionados podem abrir investigação.");
    } catch (error) {
      flash(error.message, true);
    } finally {
      if (button) { button.disabled = false; button.textContent = "Arrumar selecionados"; }
    }
  }

  function renderRun(run) {
    const root = $("#noc-selected-run");
    if (!root) return;
    root.hidden = false;
    const result = run.result || {};
    const status = String(run.status || "queued");
    root.innerHTML = `<div><span>Execução pontual</span><strong>${esc(status === "queued" ? "Na fila" : status === "running" ? "Coletando e distribuindo" : status === "completed" ? "Distribuída" : status)}</strong></div><div><span>Jobs iniciados</span><strong>${esc(result.jobs_queued ?? "—")}</strong></div><div><span>Problemas vistos</span><strong>${esc(result.problems_seen ?? "—")}</strong></div>`;
  }

  function pollRun() {
    if (state.runTimer) window.clearInterval(state.runTimer);
    if (!state.runId) return;
    state.runTimer = window.setInterval(async () => {
      try {
        const run = await request(`/ui/api/noc/autonomy/runs/${encodeURIComponent(state.runId)}`);
        renderRun(run);
        if (!["queued", "running"].includes(String(run.status || ""))) {
          window.clearInterval(state.runTimer);
          state.runTimer = null;
          await load(true);
        }
      } catch {
        window.clearInterval(state.runTimer);
        state.runTimer = null;
      }
    }, 1800);
  }

  function renderSkills() {
    const root = $("#noc-skill-strip");
    const count = $("#noc-skill-count");
    if (count) count.textContent = `${state.skills.length} especialistas`;
    if (!root) return;
    root.innerHTML = state.skills.length ? state.skills.map((skill) => `
      <article class="noc-skill-card">
        <span>${esc(skill.id || "skill")}</span>
        <strong>${esc(skill.title || skill.id)}</strong>
        <small>${skill.playbook_id ? `Playbook: ${esc(skill.playbook_id)}` : "IA monta a investigação"}</small>
        <em>${esc(skill.target_strategy || "internal_ssh")}</em>
      </article>`).join("") : '<div class="empty-state">Nenhuma skill operacional encontrada.</div>';
  }

  function renderTopStrip() {
    const strip = $("#noc-autonomy-strip");
    if (!strip) return;
    const enabled = Boolean(state.control.enabled);
    const mode = state.control.mode === "selected" ? "selecionado" : "automático";
    const marker = enabled ? "on" : "off";
    strip.dataset.agentRuntime = marker;
    strip.classList.toggle("agents-on", enabled);
    strip.classList.toggle("agents-off", !enabled);
    strip.innerHTML = enabled
      ? `<strong>Agentes ligados · ${esc(mode)}</strong><span>O Checkmk observa tudo; acesso e correção seguem o escopo autorizado.</span>`
      : '<strong>Agentes desligados · somente observação</strong><span>Inventário e erros continuam atualizados. Nenhum host é acessado automaticamente.</span>';
  }

  async function load(force = false) {
    if (state.loading || (!force && !isNocActive())) return;
    if (!ensurePanel()) return;
    state.loading = true;
    try {
      const [control, overview, skills] = await Promise.all([
        request("/ui/api/noc/autonomy"),
        request("/ui/api/noc/checkmk-master/overview"),
        request("/ui/api/noc/skills"),
      ]);
      const revisionChanged = String(control.revision || "") !== String(state.control.revision || "");
      state.control = control || state.control;
      state.overview = overview || state.overview;
      state.skills = skills.items || [];
      if (revisionChanged && !state.selectionInitialized) initializeSelection();
      renderControl();
      renderSkills();
    } catch (error) {
      flash(error.message, true);
    } finally {
      state.loading = false;
    }
  }

  function bindStripGuard() {
    const strip = $("#noc-autonomy-strip");
    if (!strip || strip.dataset.agentGuard === "1") return;
    strip.dataset.agentGuard = "1";
    new MutationObserver(() => {
      if (!strip.dataset.agentRuntime) renderTopStrip();
      else {
        const expected = state.control.enabled ? "ligados" : "desligados";
        if (!strip.textContent.toLocaleLowerCase("pt-BR").includes(expected)) renderTopStrip();
      }
    }).observe(strip, { childList: true, subtree: true });
  }

  function boot() {
    let attempts = 0;
    const waiter = window.setInterval(() => {
      attempts += 1;
      if (ensurePanel()) {
        window.clearInterval(waiter);
        bindStripGuard();
        if (isNocActive()) void load(true);
        state.refreshTimer = window.setInterval(() => {
          if (!document.hidden && isNocActive()) void load(false);
        }, 30000);
      } else if (attempts > 160) {
        window.clearInterval(waiter);
      }
    }, 250);
    document.addEventListener("click", (event) => {
      if (event.target.closest?.('[data-view="noc"]')) window.setTimeout(() => void load(true), 0);
    });
  }

  window.addEventListener("beforeunload", () => {
    if (state.refreshTimer) window.clearInterval(state.refreshTimer);
    if (state.runTimer) window.clearInterval(state.runTimer);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
