(() => {
  const baseShowView = showView;
  let healthLoading = false;
  let healthRefreshTimer = null;

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

  showView = function polishedShowView(name) {
    baseShowView(name);
    setDashboardActionVisibility(name);
    if (name === "health") void loadHealth();
    if (name === "settings") window.setTimeout(cleanSettingsStatusHint, 0);
  };

  function startHealthAutoRefresh() {
    if (healthRefreshTimer) return;
    healthRefreshTimer = window.setInterval(() => {
      if (!$("#view-health")?.classList.contains("active") || document.hidden) return;
      void loadHealth();
    }, 30000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    setDashboardActionVisibility("dashboard");
    startHealthAutoRefresh();
    observeSettingsStatus();
  });
})();
