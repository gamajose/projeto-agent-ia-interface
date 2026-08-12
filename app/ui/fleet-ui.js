(() => {
  let refreshTimer = null;
  let loading = false;
  let operational = { summary: {}, sites: [], failed_sites: [], problems: [], state: {} };
  let activeTab = "problems";

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatDate(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString("pt-BR");
  }

  function stateClass(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  }

  async function request(path, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const headers = { ...(options.headers || {}) };
    if (method !== "GET") headers["X-Agent-UI"] = "1";
    let body = options.body;
    if (body && typeof body === "object" && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, method, headers, body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  function ensureStyles() {
    if (document.querySelector('link[data-fleet-ui]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/ui/assets/fleet-ui.css?v=1.37.0";
    link.dataset.fleetUi = "1";
    document.head.appendChild(link);
  }

  function ensurePanel() {
    ensureStyles();
    const nocView = document.querySelector("#view-noc");
    if (!nocView || document.querySelector("#noc-fleet-panel")) return Boolean(nocView);
    const anchor = document.querySelector("#noc-autonomy-strip");
    const panel = document.createElement("article");
    panel.className = "panel fleet-panel";
    panel.id = "noc-fleet-panel";
    panel.innerHTML = `
      <div class="cmk-master-head">
        <div><p class="eyebrow">FONTE PRINCIPAL</p><h3>Checkmk Central</h3></div>
        <div class="cmk-master-actions">
          <button type="button" class="primary-button" id="cmk-sync">Sincronizar Checkmk</button>
          <button type="button" class="secondary-button" id="cmk-poll">Ronda agora</button>
          <button type="button" class="secondary-button" id="fleet-refresh">Atualizar</button>
        </div>
      </div>
      <div id="cmk-master-status"><div class="empty-state">Carregando...</div></div>
      <div id="cmk-action-result" class="cmk-action-result" hidden></div>

      <div class="cmk-operations" id="cmk-operations">
        <div class="cmk-tabs" role="tablist">
          <button type="button" data-cmk-tab="problems" class="active">Problemas <span id="cmk-tab-problems">0</span></button>
          <button type="button" data-cmk-tab="sites">Sites <span id="cmk-tab-sites">0</span></button>
          <button type="button" data-cmk-tab="failures">Sem resposta <span id="cmk-tab-failures">0</span></button>
        </div>
        <div class="cmk-filter"><input id="cmk-search" type="search" placeholder="Filtrar cliente, site, host, IP ou serviço"></div>
        <div id="cmk-operational-body"><div class="empty-state">Aguardando snapshot operacional...</div></div>
        <div id="cmk-site-detail" class="cmk-site-detail" hidden></div>
      </div>

      <details class="fleet-contingency">
        <summary>Descoberta de rede <small>contingência</small></summary>
        <div class="fleet-head compact">
          <div><h4>Varredura manual</h4></div>
          <div class="fleet-actions"><button type="button" class="secondary-button" id="fleet-start">Iniciar descoberta</button></div>
        </div>
        <div id="fleet-status" class="fleet-status"></div>
        <div class="fleet-columns">
          <div>
            <div class="fleet-section-title"><strong>Mapeados</strong><span id="fleet-mapped-count">0</span></div>
            <div class="table-wrap fleet-table-wrap"><table class="fleet-table"><thead><tr><th>Nome</th><th>IP</th><th>Papel</th></tr></thead><tbody id="fleet-mapped"></tbody></table></div>
          </div>
          <div>
            <div class="fleet-section-title"><strong>Não acessados</strong><span id="fleet-failed-count">0</span></div>
            <div class="table-wrap fleet-table-wrap"><table class="fleet-table"><thead><tr><th>IP</th><th>Motivo</th><th>Tentativas</th></tr></thead><tbody id="fleet-failed"></tbody></table></div>
          </div>
        </div>
      </details>`;
    if (anchor) anchor.insertAdjacentElement("afterend", panel);
    else nocView.prepend(panel);

    document.querySelector("#fleet-refresh")?.addEventListener("click", () => void loadFleet(true));
    document.querySelector("#cmk-sync")?.addEventListener("click", () => void runMasterAction("/ui/api/noc/checkmk-master/sync", "#cmk-sync", "Sincronizando..."));
    document.querySelector("#cmk-poll")?.addEventListener("click", () => void runMasterAction("/ui/api/noc/checkmk-master/poll", "#cmk-poll", "Executando ronda..."));
    document.querySelector("#fleet-start")?.addEventListener("click", () => void startFleet());
    document.querySelector("#cmk-search")?.addEventListener("input", renderOperational);
    document.querySelectorAll("[data-cmk-tab]").forEach((button) => button.addEventListener("click", () => {
      activeTab = button.dataset.cmkTab || "problems";
      document.querySelectorAll("[data-cmk-tab]").forEach((item) => item.classList.toggle("active", item === button));
      renderOperational();
    }));
    return true;
  }

  function renderMaster(data) {
    const root = document.querySelector("#cmk-master-status");
    if (!root) return;
    const patrol = data.checkmk_master || {};
    const summary = operational.summary || {};
    const state = operational.state || {};
    const total = Number(summary.sites_total || 0);
    const active = Number(summary.sites_active || 0);
    const hosts = Number(summary.hosts_total || 0);
    const problems = Number(summary.problems_active || 0);
    const jobs = Number(patrol.jobs_queued || 0);
    const last = patrol.last_completed_at || state.last_completed_at;
    const error = patrol.last_error || state.last_error;
    const failed = Number(summary.sites_failed || 0);

    root.innerHTML = `
      <div class="cmk-master-metrics">
        <div><span>Sites</span><strong>${esc(active.toLocaleString("pt-BR"))}<small> / ${esc(total.toLocaleString("pt-BR"))}</small></strong></div>
        <div><span>Hosts</span><strong>${esc(hosts.toLocaleString("pt-BR"))}</strong></div>
        <div><span>Problemas</span><strong>${esc(problems.toLocaleString("pt-BR"))}</strong></div>
        <div><span>Jobs neste ciclo</span><strong>${esc(jobs.toLocaleString("pt-BR"))}</strong></div>
      </div>
      <div class="cmk-master-line ${error ? "attention" : "healthy"}">
        <strong>${error ? "Atenção" : "Ativo"}</strong>
        <span>CMK05/master · ${esc(formatDate(last))}</span>
        <small>${failed ? `${esc(failed)} site(s) sem resposta Livestatus. Consulte a aba Sem resposta.` : "Todos os sites consultados no último ciclo responderam."}</small>
      </div>`;

    const problemTab = document.querySelector("#cmk-tab-problems");
    const siteTab = document.querySelector("#cmk-tab-sites");
    const failureTab = document.querySelector("#cmk-tab-failures");
    if (problemTab) problemTab.textContent = String(problems);
    if (siteTab) siteTab.textContent = String(active);
    if (failureTab) failureTab.textContent = String(failed);
  }

  function matchesSearch(values) {
    const input = document.querySelector("#cmk-search");
    const query = String(input?.value || "").trim().toLocaleLowerCase("pt-BR");
    if (!query) return true;
    return values.some((value) => String(value ?? "").toLocaleLowerCase("pt-BR").includes(query));
  }

  function renderProblems() {
    const items = (operational.problems || []).filter((item) => matchesSearch([
      item.client_alias, item.site_id, item.host, item.host_address, item.service, item.output, item.skill_title, item.automation_status,
    ]));
    if (!items.length) return '<div class="empty-state">Nenhum problema ativo encontrado com este filtro.</div>';
    return `<div class="cmk-table-wrap"><table class="cmk-table"><thead><tr><th>Cliente / site</th><th>Host / IP</th><th>Problema</th><th>Estado</th><th>Skill</th><th>Automação</th></tr></thead><tbody>${items.map((item) => `
      <tr>
        <td><button type="button" class="cmk-link" data-cmk-site="${esc(item.site_id)}"><strong>${esc(item.client_alias || item.site_id)}</strong><small>${esc(item.site_id)}</small></button></td>
        <td><strong>${esc(item.host || "—")}</strong><small>${esc(item.host_address || "sem IP")}</small></td>
        <td class="cmk-problem-copy"><strong>${esc(item.service || "—")}</strong><small title="${esc(item.output || "")}">${esc(item.output || "sem output")}</small></td>
        <td><span class="cmk-state ${esc(stateClass(item.state_name))}">${esc(item.state_name || item.state)}</span></td>
        <td><strong>${esc(item.skill_title || item.skill_id || "Genérica")}</strong><small>${esc(item.route_strategy || "—")}</small></td>
        <td><span class="cmk-automation ${esc(stateClass(item.automation_status))}">${esc(item.automation_status || "detected")}</span>${item.job_id ? `<small>job ${esc(item.job_id.slice(0, 8))}</small>` : ""}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  function renderSites() {
    const items = (operational.sites || []).filter((item) => item.enabled && matchesSearch([
      item.alias, item.site_id, item.livestatus_host, item.status_host, item.last_error,
    ]));
    if (!items.length) return '<div class="empty-state">Nenhum site encontrado com este filtro.</div>';
    return `<div class="cmk-table-wrap"><table class="cmk-table"><thead><tr><th>Cliente / site</th><th>Endpoint Checkmk</th><th>Hosts</th><th>Problemas</th><th>Status</th></tr></thead><tbody>${items.map((item) => `
      <tr class="cmk-site-row" data-cmk-site="${esc(item.site_id)}">
        <td><strong>${esc(item.alias || item.site_id)}</strong><small>${esc(item.site_id)}</small></td>
        <td><strong>${esc(item.livestatus_host || "—")}:${esc(item.livestatus_port || "—")}</strong><small>${esc(item.status_host || "")}</small></td>
        <td>${esc(Number(item.host_count || 0).toLocaleString("pt-BR"))}</td>
        <td><strong>${esc(Number(item.problem_count || 0).toLocaleString("pt-BR"))}</strong></td>
        <td>${item.last_error ? `<span class="cmk-state unknown">SEM RESPOSTA</span><small>${esc(item.last_error)}</small>` : '<span class="cmk-state ok">OK</span>'}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  function renderFailures() {
    const items = (operational.failed_sites || []).filter((item) => matchesSearch([
      item.alias, item.site_id, item.livestatus_host, item.error,
    ]));
    if (!items.length) return '<div class="empty-state">Nenhum site sem resposta no último snapshot.</div>';
    return `<div class="cmk-table-wrap"><table class="cmk-table"><thead><tr><th>Cliente / site</th><th>Endpoint</th><th>Erro</th><th>Última tentativa</th></tr></thead><tbody>${items.map((item) => `
      <tr class="cmk-site-row" data-cmk-site="${esc(item.site_id)}">
        <td><strong>${esc(item.alias || item.site_id)}</strong><small>${esc(item.site_id)}</small></td>
        <td>${esc(item.livestatus_host || "—")}:${esc(item.livestatus_port || "—")}</td>
        <td class="cmk-error-copy">${esc(item.error || "sem resposta")}</td>
        <td>${esc(formatDate(item.last_polled_at))}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  function renderOperational() {
    const root = document.querySelector("#cmk-operational-body");
    if (!root) return;
    root.innerHTML = activeTab === "sites" ? renderSites() : activeTab === "failures" ? renderFailures() : renderProblems();
    root.querySelectorAll("[data-cmk-site]").forEach((row) => row.addEventListener("click", () => void openSite(row.dataset.cmkSite)));
  }

  async function openSite(siteId) {
    if (!siteId) return;
    const root = document.querySelector("#cmk-site-detail");
    if (!root) return;
    root.hidden = false;
    root.innerHTML = '<div class="empty-state">Carregando cliente...</div>';
    try {
      const data = await request(`/ui/api/noc/checkmk-master/sites/${encodeURIComponent(siteId)}`);
      const site = data.site || {};
      const hosts = data.hosts || [];
      const problems = data.problems || [];
      root.innerHTML = `
        <div class="cmk-site-detail-head"><div><p class="eyebrow">CLIENTE / SITE</p><h3>${esc(site.alias || site.site_id)}</h3><small>${esc(site.site_id)} · ${esc(site.livestatus_host || "—")}:${esc(site.livestatus_port || "—")}</small></div><button type="button" class="secondary-button" id="cmk-close-site">Fechar</button></div>
        ${site.last_error ? `<div class="cmk-site-error"><strong>Livestatus:</strong> ${esc(site.last_error)}</div>` : ""}
        <div class="cmk-site-metrics"><span><strong>${esc(hosts.length)}</strong> hosts</span><span><strong>${esc(problems.length)}</strong> problemas</span><span>${site.shared_endpoint ? "endpoint compartilhado" : "endpoint dedicado"}</span></div>
        <div class="cmk-detail-grid">
          <div><h4>Hosts</h4><div class="cmk-table-wrap detail"><table class="cmk-table"><thead><tr><th>Host</th><th>IP interno</th><th>Tipo</th><th>Estado</th><th>Problemas</th></tr></thead><tbody>${hosts.length ? hosts.map((host) => `<tr><td><strong>${esc(host.host_name)}</strong></td><td>${esc(host.internal_address || "—")}</td><td>${esc(host.host_kind || "—")}</td><td>${esc(host.state)}</td><td>${esc(host.problem_count || 0)}</td></tr>`).join("") : '<tr><td colspan="5" class="empty-cell">Nenhum host coletado.</td></tr>'}</tbody></table></div></div>
          <div><h4>Problemas do cliente</h4><div class="cmk-site-problems">${problems.length ? problems.map((problem) => `<article><div><span class="cmk-state ${esc(stateClass(problem.state_name))}">${esc(problem.state_name)}</span><strong>${esc(problem.host)}</strong><small>${esc(problem.host_address || "—")}</small></div><div><strong>${esc(problem.service)}</strong><p>${esc(problem.output || "sem output")}</p><small>${esc(problem.skill_title || "Skill genérica")} · ${esc(problem.automation_status || "detected")}</small></div></article>`).join("") : '<div class="empty-state">Nenhum problema ativo.</div>'}</div></div>
        </div>`;
      root.querySelector("#cmk-close-site")?.addEventListener("click", () => { root.hidden = true; root.innerHTML = ""; });
    } catch (error) {
      root.innerHTML = `<div class="cmk-site-detail-head"><strong>Falha ao abrir site</strong><button type="button" class="secondary-button" id="cmk-close-site">Fechar</button></div><div class="empty-state">${esc(error.message)}</div>`;
      root.querySelector("#cmk-close-site")?.addEventListener("click", () => { root.hidden = true; });
    }
  }

  function renderDiscovery(data) {
    const root = document.querySelector("#fleet-status");
    const start = document.querySelector("#fleet-start");
    if (!root || !start) return;
    const run = data.run || {};
    const phase = data.phase || "not_started";
    const progress = Number(data.progress_percent || 0);
    const scanned = Number(run.scanned || 0);
    const total = Number(run.total_candidates || 0);
    const accessible = Number(run.accessible || 0);
    const inaccessible = Number(run.inaccessible || 0);
    start.disabled = phase === "running";
    start.textContent = phase === "running" ? "Em execução" : "Iniciar descoberta";
    root.innerHTML = `
      <div class="fleet-state ${phase === "running" ? "running" : phase === "inventory_ready" ? "ready" : "idle"}">
        <div class="fleet-state-copy"><strong>${phase === "running" ? "Descoberta em andamento" : phase === "inventory_ready" ? "Última descoberta concluída" : "Não iniciada"}</strong><span>${esc(scanned.toLocaleString("pt-BR"))}${total ? ` / ${esc(total.toLocaleString("pt-BR"))}` : ""} processados</span></div>
        <strong class="fleet-percent">${esc(progress.toFixed(1))}%</strong>
      </div>
      <div class="fleet-progress"><span style="width:${Math.min(100, Math.max(0, progress))}%"></span></div>
      <div class="fleet-metrics mini"><div><span>Acessíveis</span><strong>${esc(accessible.toLocaleString("pt-BR"))}</strong></div><div><span>Não acessados</span><strong>${esc(inaccessible.toLocaleString("pt-BR"))}</strong></div></div>`;
  }

  function renderMapped(data) {
    const body = document.querySelector("#fleet-mapped");
    const count = document.querySelector("#fleet-mapped-count");
    if (!body) return;
    const items = Array.isArray(data.mapped) ? data.mapped : [];
    if (count) count.textContent = String(data.assets?.total ?? items.length);
    body.innerHTML = items.length ? items.slice(0, 30).map((item) => `<tr><td><strong>${esc(item.name || item.client_name || item.address)}</strong></td><td>${esc(item.address)}</td><td>${esc((item.roles || []).join(" + ") || item.environment || "—")}</td></tr>`).join("") : '<tr><td colspan="3" class="empty-cell">Nenhum.</td></tr>';
  }

  function renderNetworkFailures(data) {
    const body = document.querySelector("#fleet-failed");
    const count = document.querySelector("#fleet-failed-count");
    if (!body) return;
    const items = Array.isArray(data.not_accessed) ? data.not_accessed : [];
    const totals = data.assets?.by_access_status || {};
    const failedTotal = Object.entries(totals).reduce((sum, [status, value]) => status === "ok" ? sum : sum + Number(value || 0), 0);
    if (count) count.textContent = String(failedTotal);
    body.innerHTML = items.length ? items.slice(0, 30).map((item) => `<tr><td><strong>${esc(item.address)}</strong></td><td>${esc(item.access_status || "erro")}</td><td>${esc(item.consecutive_failures || 0)}</td></tr>`).join("") : '<tr><td colspan="3" class="empty-cell">Nenhum.</td></tr>';
  }

  async function loadFleet(showError = false) {
    if (loading || !ensurePanel()) return;
    loading = true;
    try {
      const [fleetData, operationalData] = await Promise.all([
        request("/ui/api/noc/fleet"),
        request("/ui/api/noc/checkmk-master/overview"),
      ]);
      operational = operationalData || operational;
      renderMaster(fleetData);
      renderOperational();
      renderDiscovery(fleetData);
      renderMapped(fleetData);
      renderNetworkFailures(fleetData);
    } catch (error) {
      if (showError) window.alert(error.message);
      const root = document.querySelector("#cmk-master-status");
      if (root) root.innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
    } finally {
      loading = false;
    }
  }

  async function runMasterAction(path, selector, busyLabel) {
    const button = document.querySelector(selector);
    const original = button?.textContent || "Executar";
    const resultBox = document.querySelector("#cmk-action-result");
    if (button) { button.disabled = true; button.textContent = busyLabel; }
    if (resultBox) { resultBox.hidden = false; resultBox.textContent = "Coletando sites, hosts, estados e preparando incidentes..."; }
    try {
      const result = await request(path, { method: "POST" });
      if (resultBox) {
        resultBox.textContent = result.status === "completed"
          ? `${Number(result.sites_ok || 0).toLocaleString("pt-BR")} sites responderam · ${Number(result.hosts_seen || 0).toLocaleString("pt-BR")} hosts · ${Number(result.problems_seen || 0).toLocaleString("pt-BR")} problemas · ${Number(result.jobs_queued || 0).toLocaleString("pt-BR")} jobs iniciados`
          : `Ronda: ${result.status || "sem resultado"}`;
      }
      await loadFleet(true);
    } catch (error) {
      if (resultBox) resultBox.textContent = error.message;
      window.alert(error.message);
    } finally {
      if (button) { button.disabled = false; button.textContent = original; }
    }
  }

  async function startFleet() {
    const button = document.querySelector("#fleet-start");
    if (button) button.disabled = true;
    try {
      await request("/ui/api/noc/fleet/start", { method: "POST" });
      await loadFleet(true);
    } catch (error) {
      window.alert(error.message);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function boot() {
    let attempts = 0;
    const waiter = window.setInterval(() => {
      attempts += 1;
      if (ensurePanel()) {
        window.clearInterval(waiter);
        void loadFleet(false);
        refreshTimer = window.setInterval(() => void loadFleet(false), 10000);
      } else if (attempts > 120) {
        window.clearInterval(waiter);
      }
    }, 500);
  }

  window.loadFleet = loadFleet;
  window.addEventListener("beforeunload", () => { if (refreshTimer) window.clearInterval(refreshTimer); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
