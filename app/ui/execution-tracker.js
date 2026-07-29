(() => {
  const STORAGE_KEY = "agent-ui-active-execution";
  const PIPELINE = [
    { stage: "provider_validation", label: "Validando e selecionando a IA" },
    { stage: "target_resolution", label: "Resolvendo alvo e playbook" },
    { stage: "ssh_connection", label: "Conectando e validando o SSH" },
    { stage: "evidence_analysis", label: "Coletando e analisando evidências" },
    { stage: "result_persistence", label: "Salvando resultado e inventário" },
    { stage: "completed", label: "Investigação concluída" },
  ];
  const STAGE_ALIASES = {
    provider_selected: "provider_validation",
    target_resolved: "target_resolution",
    ssh_connected: "ssh_connection",
    queue_submission: "target_resolution",
    queue_wait: "evidence_analysis",
  };
  let activeExecution = null;
  let pollTimer = null;
  let showingFinalResult = false;

  const stageLabels = {
    queued: "Aguardando início",
    execution_started: "Execução recebida",
    provider_validation: "Validando e selecionando a IA",
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

  const baseTrackedShowResult = showResult;
  showResult = function showResultWithoutProgressCollision(result) {
    showingFinalResult = true;
    return baseTrackedShowResult(result);
  };

  function executionTrayMarkup() {
    return `<aside class="execution-tray" id="execution-tray" role="status" aria-live="polite">
      <span class="execution-tray-spinner" aria-hidden="true"></span>
      <div class="execution-tray-copy"><strong>Investigação em andamento</strong><span>Aguardando início...</span><div class="execution-tray-progress"><i></i></div></div>
      <span class="execution-tray-percent">0%</span>
      <button class="execution-tray-dismiss" type="button" aria-label="Dispensar acompanhamento">×</button>
    </aside>`;
  }

  function canonicalStage(stage) {
    return STAGE_ALIASES[stage] || stage;
  }

  function currentPhase(record) {
    return record?.current_phase || [...(record?.phases || [])].reverse()[0] || {
      stage: record?.status || "queued",
      detail: "Aguardando atualização.",
      percent: record?.percent || 0,
    };
  }

  function phaseTitle(phase) {
    return stageLabels[phase?.stage] || String(phase?.stage || "Processando").replaceAll("_", " ");
  }

  function percentValue(record = activeExecution) {
    const value = Number(record?.percent ?? currentPhase(record)?.percent ?? (record?.status === "completed" ? 100 : 0));
    return Math.max(0, Math.min(100, Number.isFinite(value) ? Math.round(value) : 0));
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
    const percent = percentValue(record);
    tray.classList.add("visible");
    tray.dataset.status = record.status || "running";
    tray.querySelector("strong").textContent = record.status === "completed"
      ? "Investigação concluída"
      : record.status === "failed"
        ? "Investigação com falha"
        : `Analisando ${record.target || "o alvo"}`;
    tray.querySelector(".execution-tray-copy > span").textContent = record.status === "completed"
      ? "Clique para abrir o resultado."
      : record.status === "failed"
        ? (record.error || phase.detail || "Clique para ver os detalhes.")
        : `${phaseTitle(phase)} · ${phase.detail || "em andamento"}`;
    tray.querySelector(".execution-tray-percent").textContent = `${percent}%`;
    tray.querySelector(".execution-tray-progress i").style.width = `${percent}%`;
  }

  function phaseMap(record) {
    const map = new Map();
    (record.phases || []).forEach((phase) => {
      const stage = canonicalStage(phase.stage);
      const previous = map.get(stage);
      if (!previous || Number(phase.percent || 0) >= Number(previous.percent || 0)) map.set(stage, { ...phase, stage });
    });
    const current = currentPhase(record);
    const currentStage = canonicalStage(current.stage);
    const previous = map.get(currentStage);
    map.set(currentStage, { ...(previous || {}), ...current, stage: currentStage });
    return map;
  }

  function timelineMarkup(record) {
    const current = currentPhase(record);
    const currentStage = canonicalStage(current.stage);
    const phases = phaseMap(record);
    const percent = percentValue(record);
    const rows = PIPELINE.map((definition) => {
      const phase = phases.get(definition.stage) || { stage: definition.stage, detail: "Aguardando a etapa anterior." };
      const failed = record.status === "failed" && currentStage === definition.stage;
      const completed = record.status === "completed" || phase.status === "completed";
      const active = !completed && !failed && currentStage === definition.stage;
      const css = failed ? "failed" : completed ? "completed" : active ? "active" : "pending";
      return `<div class="timeline-item ${css}"><span></span><div><strong>${escapeHtml(definition.label)}</strong><p>${escapeHtml(phase.detail || "Aguardando a etapa anterior.")}</p></div></div>`;
    }).join("");
    return `<div class="execution-progress-summary"><div class="execution-progress-title"><strong>${escapeHtml(record.target || "Investigação")}</strong><b>${percent}%</b></div><div class="execution-progress-bar"><i style="width:${percent}%"></i></div><p>O acompanhamento continua mesmo com este painel fechado. Abrir uma investigação antiga não interrompe nem substitui esta execução.</p><div class="execution-progress-meta"><span class="mode-badge">${escapeHtml(record.provider || "IA automática")}</span>${record.model ? `<span class="mode-badge">${escapeHtml(record.model)}</span>` : ""}<span class="mode-badge">${escapeHtml(record.execution_mode || "inline")}</span></div></div><div class="execution-timeline">${rows}</div>`;
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
    if ($("#result-drawer")?.classList.contains("open") && !showingFinalResult) {
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
        current_phase: { stage: "failed", status: "failed", detail: error.message, percent: percentValue(activeExecution) },
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
      showingFinalResult = false;
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
