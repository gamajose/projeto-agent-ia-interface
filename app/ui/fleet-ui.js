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
    const response = await fetch(path, { ...options, method, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  function ensureStyles() {
    if (document.querySelector('link[data-fleet-ui]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/ui/assets/fleet-ui.css?v=1.34.0";
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
      <div class="fleet-head">
        <div>
          <p class="eyebrow">DESCOBERTA DOS AMBIENTES</p>
          <h3>Mapeamento da frota</h3>
          <p class="fleet-description">A descoberta completa só inicia quando você mandar. Depois de iniciada, continua em segundo plano até terminar e retoma do cursor salvo se o serviço reiniciar.</p>
        </div>
        <div class="fleet-actions">
          <button type="button" class="primary-button" id="fleet-start">Iniciar descoberta completa</button>
          <button type="button" class="secondary-button" id="fleet-patrol-now" hidden>Rodar ronda agora</button>
          <button type="button" class="secondary-button" id="fleet-refresh">Atualizar</button>
        </div>
      </div>
      <div id="fleet-status" class="fleet-status"><div class="empty-state">Carregando estado da descoberta...</div></div>
      <div class="fleet-columns">
        <div>
          <div class="fleet-section-title"><strong>Mapeados recentemente</strong><span id="fleet-mapped-count">0</span></div>
          <div class="table-wrap fleet-table-wrap"><table class="fleet-table"><thead><tr><th>Nome Monitor 1</th><th>IP VPN</th><th>Hostname</th><th>Papel</th><th>Checkmk</th></tr></thead><tbody id="fleet-mapped"><tr><td colspan="5" class="empty-cell">Nenhum ativo mapeado ainda.</td></tr></tbody></table></div>
        </div>
        <div>
          <div class="fleet-section-title"><strong>Não acessados</strong><span id="fleet-failed-count">0</span></div>
          <div class="table-wrap fleet-table-wrap"><table class="fleet-table"><thead><tr><th>IP</th><th>Motivo</th><th>Tentativas</th><th>Última checagem</th></tr></thead><tbody id="fleet-failed"><tr><td colspan="4" class="empty-cell">Nenhuma falha registrada.</td></tr></tbody></table></div>
        </div>
      </div>`;
    if (anchor) anchor.insertAdjacentElement("afterend", panel);
    else nocView.prepend(panel);

    document.querySelector("#fleet-refresh")?.addEventListener("click", () => void loadFleet(true));
    document.querySelector("#fleet-start")?.addEventListener("click", () => void startFleet());
    document.querySelector("#fleet-patrol-now")?.addEventListener("click", () => void patrolNow());
    return true;
  }

  function renderStatus(data) {
    const root = document.querySelector("#fleet-status");
    const start = document.querySelector("#fleet-start");
    const patrolButton = document.querySelector("#fleet-patrol-now");
    if (!root || !start) return;
    const run = data.run || {};
    const assets = data.assets || {};
    const patrol = data.patrol || {};
    const phase = data.phase || "not_started";
    const progress = Number(data.progress_percent || 0);
    const scanned = Number(run.scanned || 0);
    const total = Number(run.total_candidates || 0);
    const accessible = Number(run.accessible || 0);
    const inaccessible = Number(run.inaccessible || 0);
    const monitors = Number(assets.monitoring_detected || run.monitoring_detected || 0);

    let title = "Descoberta ainda não iniciada";
    let detail = "Clique em Iniciar descoberta completa quando quiser construir o inventário inicial.";
    let stateClass = "idle";
    if (phase === "running") {
      title = data.stalled ? "Possível travamento detectado" : "Descoberta em andamento";
      detail = data.stalled
        ? `Sem atualização de progresso há ${Math.max(1, Math.round(Number(data.heartbeat_age_seconds || 0) / 60))} minuto(s). O cursor permanece salvo no PostgreSQL.`
        : `Processando a faixa em segundo plano. Última atualização: ${formatDate(run.updated_at)}.`;
      stateClass = data.stalled ? "stalled" : "running";
    } else if (phase === "inventory_ready") {
      title = "Inventário inicial concluído";
      detail = `A descoberta terminou em ${formatDate(run.completed_at)}. A partir daqui a ronda dos Checkmks encontrados é automática.`;
      stateClass = "ready";
    }

    start.disabled = phase === "running";
    start.textContent = phase === "running" ? "Descoberta em execução" : phase === "inventory_ready" ? "Nova descoberta completa" : "Iniciar descoberta completa";
    if (patrolButton) patrolButton.hidden = phase !== "inventory_ready";

    const patrolText = phase === "inventory_ready"
      ? (patrol.running
          ? `Ronda automática em execução · ciclo ${patrol.cycle || 0}`
          : patrol.last_completed_at
            ? `Última ronda: ${formatDate(patrol.last_completed_at)} · ${patrol.monitors_checked || 0} monitor(es) · ${patrol.problems_seen || 0} problema(s) observado(s) · ${patrol.new_incidents || 0} incidente(s) novo(s)`
            : "Ronda automática aguardando o próximo ciclo do worker")
      : "A ronda automática só começa depois que o inventário inicial estiver completo.";

    root.innerHTML = `
      <div class="fleet-state ${stateClass}">
        <div class="fleet-state-copy"><strong>${esc(title)}</strong><span>${esc(detail)}</span></div>
        <strong class="fleet-percent">${esc(progress.toFixed(2))}%</strong>
      </div>
      <div class="fleet-progress"><span style="width:${Math.min(100, Math.max(0, progress))}%"></span></div>
      <div class="fleet-patrol-line"><strong>Ronda:</strong><span>${esc(patrolText)}</span>${patrol.last_error ? `<small>${esc(patrol.last_error)}</small>` : ""}</div>
      <div class="fleet-metrics">
        <div><span>Processados</span><strong>${esc(scanned.toLocaleString("pt-BR"))}${total ? ` / ${esc(total.toLocaleString("pt-BR"))}` : ""}</strong></div>
        <div><span>Acessíveis</span><strong>${esc(accessible.toLocaleString("pt-BR"))}</strong></div>
        <div><span>Checkmk encontrados</span><strong>${esc(monitors.toLocaleString("pt-BR"))}</strong></div>
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
      ? items.slice(0, 40).map((item) => `
        <tr>
          <td><strong>${esc(item.name || item.client_name || item.address)}</strong></td>
          <td>${esc(item.address)}</td>
          <td>${esc(item.hostname || "—")}</td>
          <td>${esc((item.roles || []).join(" + ") || item.environment || "—")}</td>
          <td>${item.monitoring_detected ? `<strong>Sim</strong><small>${esc((item.checkmk_sites || []).join(", ") || `${item.monitoring_confidence || 0}%`)}</small>` : "Não"}</td>
        </tr>`).join("")
      : '<tr><td colspan="5" class="empty-cell">Nenhum ativo mapeado ainda.</td></tr>';
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
      ? items.slice(0, 40).map((item) => `
        <tr>
          <td><strong>${esc(item.address)}</strong>${item.client_name ? `<small>${esc(item.client_name)}</small>` : ""}</td>
          <td><span class="fleet-failure">${esc(item.access_status || "erro")}</span></td>
          <td>${esc(item.consecutive_failures || 0)}</td>
          <td>${esc(formatDate(item.last_checked_at))}</td>
        </tr>`).join("")
      : '<tr><td colspan="4" class="empty-cell">Nenhuma falha registrada.</td></tr>';
  }

  async function loadFleet(showError = false) {
    if (loading || !ensurePanel()) return;
    loading = true;
    try {
      const data = await request("/ui/api/noc/fleet");
      renderStatus(data);
      renderMapped(data);
      renderFailures(data);
    } catch (error) {
      if (showError) alert(error.message);
      const root = document.querySelector("#fleet-status");
      if (root) root.innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
    } finally {
      loading = false;
    }
  }

  async function startFleet() {
    const button = document.querySelector("#fleet-start");
    if (button) button.disabled = true;
    try {
      await request("/ui/api/noc/fleet/start", { method: "POST" });
      await loadFleet(true);
    } catch (error) {
      alert(error.message);
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function patrolNow() {
    const button = document.querySelector("#fleet-patrol-now");
    if (button) button.disabled = true;
    try {
      await request("/ui/api/noc/fleet/patrol", { method: "POST" });
      await loadFleet(true);
    } catch (error) {
      alert(error.message);
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

  window.addEventListener("beforeunload", () => {
    if (refreshTimer) window.clearInterval(refreshTimer);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
