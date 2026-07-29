(() => {
  const STORAGE_KEY = "agent-ui-active-execution";
  let activeExecution = null;
  let pollTimer = null;
  let showingFinalResult = false;

  const stageLabels = {
    queued: "Aguardando início",
    execution_started: "Execução recebida",
    provider_validation: "Validando a IA",
    provider_selected: "IA selecionada",
    target_resolution: "Resolvendo alvo e playbook",
    target_resolved: "Alvo resolvido",
    ssh_connection: "Conectando por SSH",
    ssh_connected: "SSH validado",
    evidence_analysis: "Coletando e analisando evidências",
    result_persistence: "Salvando resultado e inventário",
    queue_submission: "Enviando para a fila",
    queue_wait: "Execução no worker",
    completed: "Investigação concluída",
    failed: "Investigação falhou",
  };

  function executionTrayMarkup() {
    return `<aside class="execution-tray" id="execution-tray" role="status" aria-live="polite">
      <span class="execution-tray-spinner" aria-hidden="true"></span>
      <div class="execution-tray-copy"><strong>Investigação em andamento</strong><span>Aguardando início...</span></div>
      <button class="execution-tray-dismiss" type="button" aria-label="Dispensar acompanhamento">×</button>
    </aside>`;
  }

  function currentPhase(record) {
    return record?.current_phase || [...(record?.phases || [])].reverse()[0] || {
      stage: record?.status || "queued",
      detail: "Aguardando atualização.",
    };
  }

  function phaseTitle(phase) {
    return stageLabels[phase?.stage] || String(phase?.stage || "Processando").replaceAll("_", " ");
  }

  function isRunning(record = activeExecution) {
    return ["queued", "running"].includes(record?.status);
  }

  function saveActiveId(id) {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  }

  function renderTray(record) {
    const tray = $("#execution-tray");
    if (!tray || !record) return;
    const phase = currentPhase(record);
    tray.classList.add("visible");
    tray.dataset.status = record.status || "running";
    tray.querySelector("strong").textContent = record.status === "completed"
      ? "Investigação concluída"
      : record.status === "failed"
        ? "Investigação com falha"
        : `Analisando ${record.target || "o alvo"}`;
    tray.querySelector(".execution-tray-copy span").textContent = record.status === "completed"
      ? "Clique para abrir o resultado."
      : record.status === "failed"
        ? (record.error || phase.detail || "Clique para ver os detalhes.")
        : `${phaseTitle(phase)} · ${phase.detail || "em andamento"}`;
  }

  function timelineMarkup(record) {
    const phases = [...(record.phases || [])];
    const current = currentPhase(record);
    if (!phases.some((item) => item.stage === current.stage)) phases.push(current);
    const rows = phases.map((phase) => {
      const completed = phase.status === "completed" || (record.status === "completed" && phase.stage !== "failed");
      const failed = phase.status === "failed";
      const active = !completed && !failed && phase.stage === current.stage;
      const css = failed ? "active" : completed ? "completed" : active ? "active" : "";
      return `<div class="timeline-item ${css}"><span></span><div><strong>${escapeHtml(phaseTitle(phase))}</strong><p>${escapeHtml(phase.detail || "Etapa registrada pelo backend.")}</p></div></div>`;
    }).join("");
    return `<div class="execution-progress-summary"><strong>${escapeHtml(record.target || "Investigação")}</strong><p>O acompanhamento continua mesmo que este painel seja fechado. Use o cartão fixo no canto da tela para voltar.</p><div class="execution-progress-meta"><span class="mode-badge">${escapeHtml(record.provider || "IA automática")}</span>${record.model ? `<span class="mode-badge">${escapeHtml(record.model)}</span>` : ""}<span class="mode-badge">${escapeHtml(record.execution_mode || "inline")}</span></div></div><div class="execution-timeline">${rows}</div>`;
  }

  function renderProgressDrawer(record, force = false) {
    const drawer = $("#result-drawer");
    if (!drawer || (!force && !drawer.classList.contains("open")) || showingFinalResult) return;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    $("#result-title").textContent = record.status === "failed" ? "Falha na investigação" : "Investigação em andamento";
    $("#result-content").innerHTML = timelineMarkup(record);
  }

  function invalidateViews() {
    state.dashboardLoaded = false;
    state.investigationsLoaded = false;
    state.inventoryLoaded = false;
    if ($("#view-inventory")?.classList.contains("active")) void loadInventory();
    if ($("#view-investigations")?.classList.contains("active")) void loadInvestigations();
  }

  function completeExecution(record) {
    activeExecution = record;
    renderTray(record);
    setSubmitting(false);
    invalidateViews();
    if ($("#result-drawer")?.classList.contains("open")) {
      showingFinalResult = true;
      showResult(record.result || {});
    } else {
      toast("Investigação concluída. Clique no acompanhamento para abrir o resultado.");
    }
  }

  function failExecution(record) {
    activeExecution = record;
    renderTray(record);
    setSubmitting(false);
    renderProgressDrawer(record);
    toast(record.error || "A investigação falhou.", "error");
  }

  function schedulePoll(id) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(() => void pollExecution(id), 1100);
  }

  async function pollExecution(id) {
    try {
      const record = await api(`/ui/api/executions/${encodeURIComponent(id)}`);
      if (!activeExecution || activeExecution.execution_id !== id) return;
      activeExecution = record;
      renderTray(record);
      renderProgressDrawer(record);
      if (record.status === "completed") {
        completeExecution(record);
        return;
      }
      if (record.status === "failed") {
        failExecution(record);
        return;
      }
      schedulePoll(id);
    } catch (error) {
      if (activeExecution?.execution_id !== id) return;
      activeExecution = {
        ...activeExecution,
        status: "failed",
        error: error.message,
        current_phase: { stage: "failed", status: "failed", detail: error.message },
      };
      failExecution(activeExecution);
    }
  }

  function analysisPayload() {
    return {
      target: $("#target").value.trim(),
      objective: $("#objective").value.trim(),
      environment: $("#environment").value,
      mode: $("#mode").value,
      ssh_port: $("#ssh-port").value ? Number($("#ssh-port").value) : null,
      provider: $("#provider").value,
      model: $("#model").value || null,
      playbook_mode: $("#playbook-mode").value,
      playbook_id: $("#playbook-id").value || null,
    };
  }

  async function startTrackedAnalysis(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (isRunning()) {
      toast("Já existe uma investigação em andamento. Abra o acompanhamento para ver a etapa atual.", "error");
      renderProgressDrawer(activeExecution, true);
      return;
    }
    const payload = analysisPayload();
    const provider = state.providers.find((item) => item.provider === payload.provider);
    if (!provider?.selectable) return toast("Selecione um provedor disponível antes de iniciar.", "error");
    if (!payload.objective || payload.objective.length < 3) return toast("Descreva o objetivo da análise.", "error");
    if (payload.playbook_mode === "manual" && !payload.playbook_id) return toast("Selecione o playbook manual.", "error");

    setSubmitting(true);
    showingFinalResult = false;
    showExecutionStart(payload);
    try {
      const record = await api("/ui/api/executions", { method: "POST", body: payload });
      activeExecution = record;
      saveActiveId(record.execution_id);
      renderTray(record);
      renderProgressDrawer(record, true);
      schedulePoll(record.execution_id);
    } catch (error) {
      setSubmitting(false);
      toast(error.message, "error");
      $("#result-content").innerHTML = `<div class="result-section error-section"><h3>Investigação não iniciada</h3><p>${escapeHtml(error.message)}</p></div>`;
    }
  }

  function openTrackedExecution() {
    if (!activeExecution) return;
    if (activeExecution.status === "completed" && activeExecution.result) {
      showingFinalResult = true;
      showResult(activeExecution.result);
      return;
    }
    showingFinalResult = false;
    renderProgressDrawer(activeExecution, true);
  }

  function dismissTrackedExecution(event) {
    event.stopPropagation();
    if (isRunning()) return;
    activeExecution = null;
    showingFinalResult = false;
    saveActiveId(null);
    $("#execution-tray")?.classList.remove("visible");
  }

  async function restoreExecution() {
    const id = localStorage.getItem(STORAGE_KEY);
    if (!id) return;
    try {
      activeExecution = await api(`/ui/api/executions/${encodeURIComponent(id)}`);
      renderTray(activeExecution);
      if (isRunning()) {
        setSubmitting(true);
        schedulePoll(id);
      }
    } catch {
      activeExecution = null;
      saveActiveId(null);
    }
  }

  function setupExecutionTracking() {
    if (!$("#execution-tray")) document.body.insertAdjacentHTML("beforeend", executionTrayMarkup());
    $("#execution-tray")?.addEventListener("click", openTrackedExecution);
    $("#execution-tray .execution-tray-dismiss")?.addEventListener("click", dismissTrackedExecution);
    $("#analysis-form")?.addEventListener("submit", startTrackedAnalysis, true);
    void restoreExecution();
  }

  document.addEventListener("DOMContentLoaded", setupExecutionTracking);
})();
