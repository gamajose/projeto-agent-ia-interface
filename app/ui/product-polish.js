(() => {
  const baseShowView = showView;
  let healthLoading = false;
  let healthRefreshTimer = null;
  let nocLoading = false;
  let nocRefreshTimer = null;
  let selectedNocIncident = null;

  function cleanSettingsStatusHint() {
    const status = $("#provider-order-status");
    if (!status) return;
    const text = status.textContent.trim();
    if (/^(As alterações entram|Arraste os cards|Carregando prioridade)/i.test(text)) status.textContent = "";
  }

  function observeSettingsStatus() {
    const status = $("#provider-order-status");
    if (!status) return;
    cleanSettingsStatusHint();
    const observer = new MutationObserver(cleanSettingsStatusHint);
    observer.observe(status, { childList: true, characterData: true, subtree: true });
  }

  function setDashboardActionVisibility(name) {
    const button = $("#topbar-start-investigation");
    if (button) button.hidden = name !== "dashboard";
  }

  function overallHealthCard(data) {
    const stateName = data.status || "unknown";
    return `<article class="health-card overall-health-card" data-state="${escapeHtml(stateName)}">
      <div class="health-card-header"><h4>Estado geral</h4><span>${escapeHtml(labelStatus(stateName))}</span></div>
      <p>Versão ${escapeHtml(data.version || "—")}</p>
      <div class="health-meta"><span>${escapeHtml(data.git?.branch || "sem branch")}</span><span>${escapeHtml(data.git?.commit || "sem commit")}</span></div>
    </article>`;
  }

  loadHealth = async function compactAutomaticHealth() {
    if (healthLoading) return;
    healthLoading = true;
    const grid = $("#health-grid");
    const summary = $("#health-summary");
    const providers = $("#provider-health-list");
    if (summary) {
      summary.hidden = true;
      summary.innerHTML = "";
    }
    if (grid) {
      grid.classList.add("compact-health-grid");
      grid.innerHTML = '<div class="empty-state">Atualizando diagnóstico...</div>';
    }
    if (providers) providers.innerHTML = "";
    try {
      const data = await api("/ui/api/health");
      const queueExtra = `<div class="health-meta"><span>Modo: ${escapeHtml(data.queue?.execution_mode)}</span><span>Fila: ${escapeHtml(data.queue?.depth ?? "—")}</span></div>`;
      const playbookExtra = `<div class="health-meta"><span>${escapeHtml(data.playbooks?.count)} playbooks</span><span title="${escapeHtml(data.playbooks?.directory)}">diretório</span></div>`;
      grid.innerHTML = [
        overallHealthCard(data),
        healthCard("PostgreSQL", data.database),
        healthCard("Redis e fila", data.queue, queueExtra),
        healthCard("Worker", data.worker),
        healthCard("Playbooks", data.playbooks, playbookExtra),
      ].join("");
      providers.innerHTML = `<div class="panel-subheader"><div><p class="eyebrow">PROVEDORES</p><h3>Validação de IA</h3></div><span class="auto-refresh-note">atualização automática</span></div><div class="provider-health-grid">${(data.providers || []).map((item) => healthCard(item.label, item, `<div class="health-meta"><span>${escapeHtml(item.model || "sem modelo")}</span><span>${item.latency_ms != null ? `${escapeHtml(item.latency_ms)} ms` : "—"}</span></div>`)).join("")}</div>`;
      state.healthLoaded = true;
    } catch (error) {
      if (grid) grid.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
      toast(error.message, "error");
    } finally {
      healthLoading = false;
    }
  };

  function ensureNocStyles() {
    if (document.querySelector('link[data-noc-styles]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/ui/assets/noc.css?v=1.34.0";
    link.dataset.nocStyles = "1";
    document.head.appendChild(link);
  }

  function ensureNocUi() {
    ensureNocStyles();
    viewMeta.noc = ["NOC AUTÔNOMO", "Incidentes"];

    const nav = $(".nav");
    if (nav && !nav.querySelector('[data-view="noc"]')) {
      const button = document.createElement("button");
      button.className = "nav-item";
      button.dataset.view = "noc";
      button.innerHTML = '<span class="nav-icon">◉</span><span>NOC Autônomo</span>';
      const dashboardButton = nav.querySelector('[data-view="dashboard"]');
      dashboardButton?.insertAdjacentElement("afterend", button);
      button.addEventListener("click", () => showView("noc"));
    }

    if (!$("#view-noc")) {
      const section = document.createElement("section");
      section.className = "view";
      section.id = "view-noc";
      section.innerHTML = `
        <div class="noc-summary-grid" id="noc-summary-grid">
          <article class="noc-metric"><span>Incidentes ativos</span><strong>—</strong><small>carregando supervisor</small></article>
          <article class="noc-metric"><span>IA trabalhando</span><strong>—</strong><small>fila + investigação</small></article>
          <article class="noc-metric"><span>Precisa de você</span><strong>—</strong><small>aprovação ou atenção</small></article>
          <article class="noc-metric"><span>Resolvidos hoje</span><strong>—</strong><small>recovery do Checkmk</small></article>
        </div>
        <div class="noc-grid">
          <article class="panel">
            <div class="panel-header stacked-mobile">
              <div><p class="eyebrow">OPERAÇÃO EM TEMPO REAL</p><h3>Fila de incidentes</h3></div>
              <div class="noc-toolbar"><span class="noc-live">atualização automática</span><button class="secondary-button" id="noc-refresh" type="button">Atualizar</button></div>
            </div>
            <div class="table-wrap"><table class="noc-table"><thead><tr><th>Host</th><th>Serviço</th><th>Estado</th><th>Fluxo</th><th>Quando</th></tr></thead><tbody id="noc-incidents-table"><tr><td colspan="5" class="empty-cell">Carregando incidentes...</td></tr></tbody></table></div>
          </article>
          <article class="panel noc-detail" id="noc-detail"><div class="noc-detail-empty"><div><strong>Selecione um incidente</strong><p>Veja causa, investigação, flapping e ações que dependem de você.</p></div></div></article>
        </div>`;
      $("#view-dashboard")?.insertAdjacentElement("afterend", section);
      $("#noc-refresh")?.addEventListener("click", () => void loadNocDashboard(true));
    }
  }

  function nocStatusLabel(status) {
    const labels = {
      new: "Novo",
      queued: "Na fila",
      investigating: "Investigando",
      awaiting_approval: "Aguardando aprovação",
      watching: "Acompanhando",
      needs_attention: "Precisa de atenção",
      resolved: "Resolvido",
    };
    return labels[status] || status || "Desconhecido";
  }

  function nocStateLabel(item) {
    const state = item.current_state || "—";
    const flapping = item.flapping ? '<span class="noc-flapping">FLAPPING</span>' : "";
    return `<strong>${escapeHtml(state)}</strong>${flapping}`;
  }

  function renderNocSummary(counts = {}) {
    const working = Number(counts.queued || 0) + Number(counts.investigating || 0) + Number(counts.watching || 0);
    const human = Number(counts.awaiting_approval || 0) + Number(counts.needs_attention || 0);
    const cards = [
      ["Incidentes ativos", counts.active || 0, `${counts.flapping || 0} com flapping`],
      ["IA trabalhando", working, `${counts.investigating || 0} em investigação`],
      ["Precisa de você", human, `${counts.awaiting_approval || 0} aguardando aprovação`],
      ["Resolvidos hoje", counts.resolved_today || 0, "normalizados/encerrados"],
    ];
    const root = $("#noc-summary-grid");
    if (root) root.innerHTML = cards.map(([label, value, detail]) => `<article class="noc-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`).join("");
  }

  function renderNocRows(items = []) {
    const body = $("#noc-incidents-table");
    if (!body) return;
    body.innerHTML = items.length ? items.map((item) => `
      <tr data-noc-incident-id="${escapeHtml(item.id)}">
        <td class="noc-host"><strong>${escapeHtml(item.host || "—")}</strong><small>${escapeHtml(item.site || item.environment || "")}</small></td>
        <td class="noc-service"><strong>${escapeHtml(item.service || "—")}</strong><small>${escapeHtml(String(item.last_output || "").slice(0, 70))}</small></td>
        <td>${nocStateLabel(item)}</td>
        <td><span class="noc-status ${escapeHtml(item.status || "new")}">${escapeHtml(nocStatusLabel(item.status))}</span></td>
        <td>${escapeHtml(formatDate(item.last_seen_at || item.created_at))}</td>
      </tr>`).join("") : '<tr><td colspan="5" class="empty-cell">Nenhum incidente registrado. O Supervisor NOC está pronto para receber o webhook do Checkmk.</td></tr>';
    $$('[data-noc-incident-id]', body).forEach((row) => row.addEventListener("click", () => void openNocIncident(row.dataset.nocIncidentId)));
  }

  function renderNocDetail(item) {
    const root = $("#noc-detail");
    if (!root) return;
    selectedNocIncident = item.id;
    const events = (item.events || []).slice().reverse();
    root.innerHTML = `
      <div class="noc-detail-head"><div><p class="eyebrow">INCIDENTE</p><h3>${escapeHtml(item.host || "—")}</h3><p>${escapeHtml(item.service || "—")}</p></div><div><span class="noc-status ${escapeHtml(item.status || "new")}">${escapeHtml(nocStatusLabel(item.status))}</span>${item.flapping ? '<span class="noc-flapping">FLAPPING</span>' : ""}</div></div>
      <div class="noc-detail-actions">
        ${item.acknowledged_at ? `<span class="mode-badge">Assumido por ${escapeHtml(item.acknowledged_by || "operador")}</span>` : '<button class="secondary-button" id="noc-ack" type="button">Assumir incidente</button>'}
        ${item.investigation_id ? '<button class="secondary-button" id="noc-open-investigation" type="button">Abrir investigação</button>' : ""}
        ${item.status !== "resolved" ? '<button class="ghost-button" id="noc-resolve" type="button">Encerrar manualmente</button>' : ""}
      </div>
      <div class="noc-facts">
        <div class="noc-fact"><span>Estado Checkmk</span><strong>${escapeHtml(item.current_state || "—")}</strong></div>
        <div class="noc-fact"><span>Ambiente</span><strong>${escapeHtml(labelEnvironment(item.environment))}</strong></div>
        <div class="noc-fact"><span>Ocorrências</span><strong>${escapeHtml(item.occurrence_count || 0)}</strong></div>
        <div class="noc-fact"><span>Transições recentes</span><strong>${escapeHtml(item.recent_transition_count || 0)}</strong></div>
        <div class="noc-fact"><span>Confiança da IA</span><strong>${escapeHtml(item.confidence || 0)}%</strong></div>
        <div class="noc-fact"><span>Primeiro alerta</span><strong>${escapeHtml(formatDate(item.first_seen_at))}</strong></div>
      </div>
      <div class="noc-analysis"><h4>Causa provável</h4><p>${escapeHtml(item.probable_cause || "A investigação ainda não produziu uma causa provável.")}</p><h4>Conclusão</h4><p>${escapeHtml(item.conclusion || item.attention_reason || "Aguardando processamento.")}</p></div>
      <div class="noc-events"><h4>Linha do tempo</h4>${events.length ? events.map((event) => `<div class="noc-event ${escapeHtml(event.kind || "")}"><div class="noc-event-top"><strong>${escapeHtml(event.state || "—")}${event.deduplicated ? " · deduplicado" : ""}</strong><span>${escapeHtml(formatDate(event.timestamp))}</span></div><p>${escapeHtml(event.output || "")}</p></div>`).join("") : '<div class="empty-state">Sem eventos registrados.</div>'}</div>`;

    $("#noc-ack")?.addEventListener("click", () => void mutateNocIncident("acknowledge"));
    $("#noc-resolve")?.addEventListener("click", () => void mutateNocIncident("resolve"));
    $("#noc-open-investigation")?.addEventListener("click", () => openInvestigation(item.investigation_id));
  }

  async function openNocIncident(id) {
    if (!id) return;
    const root = $("#noc-detail");
    if (root) root.innerHTML = '<div class="noc-detail-empty"><div><strong>Carregando incidente...</strong></div></div>';
    try {
      const item = await api(`/ui/api/noc/incidents/${encodeURIComponent(id)}`);
      renderNocDetail(item);
    } catch (error) {
      if (root) root.innerHTML = `<div class="noc-detail-empty"><div><strong>Falha ao abrir incidente</strong><p>${escapeHtml(error.message)}</p></div></div>`;
      toast(error.message, "error");
    }
  }

  async function mutateNocIncident(action) {
    if (!selectedNocIncident) return;
    try {
      const options = { method: "POST" };
      if (action === "resolve") options.body = { reason: "Encerrado manualmente pelo operador na Central NOC." };
      await api(`/ui/api/noc/incidents/${encodeURIComponent(selectedNocIncident)}/${action}`, options);
      toast(action === "resolve" ? "Incidente encerrado." : "Incidente assumido.");
      await loadNocDashboard(true);
      await openNocIncident(selectedNocIncident);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function loadNocDashboard(force = false) {
    if (nocLoading) return;
    if (!force && !$("#view-noc")?.classList.contains("active")) return;
    nocLoading = true;
    try {
      const data = await api("/ui/api/noc/dashboard");
      renderNocSummary(data.counts || {});
      renderNocRows(data.recent || []);
      if (selectedNocIncident && $("#view-noc")?.classList.contains("active")) {
        void openNocIncident(selectedNocIncident);
      }
    } catch (error) {
      const body = $("#noc-incidents-table");
      if (body) body.innerHTML = `<tr><td colspan="5" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
      toast(error.message, "error");
    } finally {
      nocLoading = false;
    }
  }

  showView = function polishedShowView(name) {
    baseShowView(name);
    setDashboardActionVisibility(name);
    if (name === "health") void loadHealth();
    if (name === "settings") window.setTimeout(cleanSettingsStatusHint, 0);
    if (name === "noc") void loadNocDashboard(true);
  };

  function startHealthAutoRefresh() {
    if (healthRefreshTimer) return;
    healthRefreshTimer = window.setInterval(() => {
      if (!$("#view-health")?.classList.contains("active") || document.hidden) return;
      void loadHealth();
    }, 30000);
  }

  function startNocAutoRefresh() {
    if (nocRefreshTimer) return;
    nocRefreshTimer = window.setInterval(() => {
      if (!$("#view-noc")?.classList.contains("active") || document.hidden) return;
      void loadNocDashboard();
    }, 10000);
  }

  ensureNocUi();

  document.addEventListener("DOMContentLoaded", () => {
    setDashboardActionVisibility("dashboard");
    startHealthAutoRefresh();
    startNocAutoRefresh();
    observeSettingsStatus();
  });
})();
