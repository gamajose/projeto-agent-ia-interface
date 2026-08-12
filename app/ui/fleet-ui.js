(() => {
  let refreshTimer = null;
  let loading = false;

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
    link.href = "/ui/assets/fleet-ui.css?v=1.36.0";
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

      <details class="fleet-contingency">
        <summary>Descoberta de rede <small>contingência</small></summary>
        <div class="fleet-head compact">
          <div><h4>Varredura manual</h4></div>
          <div class="fleet-actions">
            <button type="button" class="secondary-button" id="fleet-start">Iniciar descoberta</button>
          </div>
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
    document.querySelector("#cmk-sync")?.addEventListener("click", () => void runMasterAction("/ui/api/noc/checkmk-master/sync", "#cmk-sync"));
    document.querySelector("#cmk-poll")?.addEventListener("click", () => void runMasterAction("/ui/api/noc/checkmk-master/poll", "#cmk-poll"));
    document.querySelector("#fleet-start")?.addEventListener("click", () => void startFleet());
    return true;
  }

  function renderMaster(data) {
    const root = document.querySelector("#cmk-master-status");
    if (!root) return;
    const patrol = data.checkmk_master || {};
    const master = patrol.master || {};
    const total = Number(master.sites_total || 0);
    const active = Number(master.sites_active || 0);
    const hosts = Number(master.hosts_total || 0);
    const problems = Number(patrol.problems_seen ?? master.problems ?? 0);
    const jobs = Number(patrol.jobs_queued || 0);
    const guarded = Number(patrol.guarded_sites || 0);
    const last = patrol.last_completed_at || master.last_poll_at || master.last_sync_at;
    const error = patrol.last_error || master.last_error;
    const recent = Array.isArray(master.recent_problems) ? master.recent_problems : [];

    root.innerHTML = `
      <div class="cmk-master-metrics">
        <div><span>Sites</span><strong>${esc(active.toLocaleString("pt-BR"))}<small> / ${esc(total.toLocaleString("pt-BR"))}</small></strong></div>
        <div><span>Hosts</span><strong>${esc(hosts.toLocaleString("pt-BR"))}</strong></div>
        <div><span>Problemas</span><strong>${esc(problems.toLocaleString("pt-BR"))}</strong></div>
        <div><span>Jobs</span><strong>${esc(jobs.toLocaleString("pt-BR"))}</strong></div>
      </div>
      <div class="cmk-master-line ${error ? "attention" : "healthy"}">
        <strong>${error ? "Atenção" : "Ativo"}</strong>
        <span>${esc(master.source || "CMK05/master")} · ${esc(formatDate(last))}</span>
        ${guarded ? `<small>${esc(guarded)} site(s) em endpoint compartilhado aguardando rota segura.</small>` : ""}
        ${error ? `<small>${esc(error)}</small>` : ""}
      </div>
      ${recent.length ? `<div class="cmk-recent">${recent.slice(0, 8).map((item) => `
        <article><span>${esc(item.site_id || "-")} · ${esc(item.state || "ALERT")}</span><strong>${esc(item.host || "-")}</strong><small>${esc(item.service || "-")}</small></article>`).join("")}</div>` : ""}`;
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
      <div class="fleet-metrics mini">
        <div><span>Acessíveis</span><strong>${esc(accessible.toLocaleString("pt-BR"))}</strong></div>
        <div><span>Não acessados</span><strong>${esc(inaccessible.toLocaleString("pt-BR"))}</strong></div>
      </div>`;
  }

  function renderMapped(data) {
    const body = document.querySelector("#fleet-mapped");
    const count = document.querySelector("#fleet-mapped-count");
    if (!body) return;
    const items = Array.isArray(data.mapped) ? data.mapped : [];
    if (count) count.textContent = String(data.assets?.total ?? items.length);
    body.innerHTML = items.length
      ? items.slice(0, 30).map((item) => `<tr><td><strong>${esc(item.name || item.client_name || item.address)}</strong></td><td>${esc(item.address)}</td><td>${esc((item.roles || []).join(" + ") || item.environment || "—")}</td></tr>`).join("")
      : '<tr><td colspan="3" class="empty-cell">Nenhum.</td></tr>';
  }

  function renderFailures(data) {
    const body = document.querySelector("#fleet-failed");
    const count = document.querySelector("#fleet-failed-count");
    if (!body) return;
    const items = Array.isArray(data.not_accessed) ? data.not_accessed : [];
    const totals = data.assets?.by_access_status || {};
    const failedTotal = Object.entries(totals).reduce((sum, [status, value]) => status === "ok" ? sum : sum + Number(value || 0), 0);
    if (count) count.textContent = String(failedTotal);
    body.innerHTML = items.length
      ? items.slice(0, 30).map((item) => `<tr><td><strong>${esc(item.address)}</strong></td><td>${esc(item.access_status || "erro")}</td><td>${esc(item.consecutive_failures || 0)}</td></tr>`).join("")
      : '<tr><td colspan="3" class="empty-cell">Nenhum.</td></tr>';
  }

  async function loadFleet(showError = false) {
    if (loading || !ensurePanel()) return;
    loading = true;
    try {
      const data = await request("/ui/api/noc/fleet");
      renderMaster(data);
      renderDiscovery(data);
      renderMapped(data);
      renderFailures(data);
    } catch (error) {
      if (showError) window.alert(error.message);
      const root = document.querySelector("#cmk-master-status");
      if (root) root.innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
    } finally {
      loading = false;
    }
  }

  async function runMasterAction(path, selector) {
    const button = document.querySelector(selector);
    if (button) button.disabled = true;
    try {
      await request(path, { method: "POST" });
      await loadFleet(true);
    } catch (error) {
      window.alert(error.message);
    } finally {
      if (button) button.disabled = false;
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
        refreshTimer = window.setInterval(() => void loadFleet(false), 5000);
      } else if (attempts > 120) {
        window.clearInterval(waiter);
      }
    }, 500);
  }

  window.loadFleet = loadFleet;
  window.addEventListener("beforeunload", () => {
    if (refreshTimer) window.clearInterval(refreshTimer);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
