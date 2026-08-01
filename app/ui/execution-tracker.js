(() => {
  const STORAGE_KEY = "agent-ui-active-execution";
  const BASE_PIPELINE = [
    { stage: "provider_validation", label: "Validando e selecionando a IA" },
    { stage: "target_resolution", label: "Resolvendo alvo e playbook" },
    { stage: "ssh_connection", label: "Conectando e validando o SSH" },
    { stage: "evidence_analysis", label: "Coletando e analisando evidências" },
    { stage: "result_persistence", label: "Salvando resultado e inventário" },
    { stage: "completed", label: "Investigação concluída" },
  ];
  const QUEUE_PIPELINE = [
    { stage: "worker_wait", label: "Aguardando worker operacional" },
    ...BASE_PIPELINE,
  ];
  const STAGE_ALIASES = {
    provider_selected: "provider_validation",
    target_resolved: "target_resolution",
    ssh_connected: "ssh_connection",
    queue_submission: "worker_wait",
    queue_wait: "worker_wait",
    multi_host_scope: "target_resolution",
    multi_host_triage: "evidence_analysis",
    multi_host_primary: "evidence_analysis",
    multi_host_handoff: "evidence_analysis",
    command_started: "evidence_analysis",
    command_output: "evidence_analysis",
    command_completed: "evidence_analysis",
    command_cancelled: "evidence_analysis",
  };
  const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);
  const COMMAND_STAGES = new Set(["command_started", "command_output", "command_completed", "command_cancelled"]);
  let activeExecution = null;
  let pollTimer = null;
  let eventStream = null;
  let streamFailures = 0;
  let showingFinalResult = false;

  const stageLabels = {
    queued: "Aguardando início",
    execution_started: "Execução recebida",
    worker_wait: "Aguardando worker operacional",
    provider_validation: "Validando e selecionando a IA",
    provider_selected: "IA selecionada",
    target_resolution: "Resolvendo alvo e playbook",
    target_resolved: "Alvo resolvido",
    ssh_connection: "Conectando por SSH",
    ssh_connected: "SSH validado",
    multi_host_scope: "Preparando escopo multi-host",
    multi_host_triage: "Triagem rápida dos hosts",
    multi_host_primary: "Investigando servidor de entrada",
    multi_host_handoff: "Mudando para host relacionado",
    evidence_analysis: "Coletando e analisando evidências",
    command_started: "Executando comando",
    command_output: "Recebendo saída do comando",
    command_completed: "Comando finalizado",
    command_cancelled: "Comando interrompido",
    result_persistence: "Salvando resultado e inventário",
    queue_submission: "Enviando para a fila",
    queue_wait: "Aguardando worker",
    completed: "Investigação concluída",
    failed: "Investigação falhou",
    cancelled: "Investigação cancelada",
  };

  const baseTrackedShowResult = showResult;
  showResult = function showResultWithoutProgressCollision(result) {
    showingFinalResult = true;
    return baseTrackedShowResult(result);
  };

  function executionTrayMarkup() {
    return `<aside class="execution-tray" id="execution-tray" role="button" tabindex="0" aria-live="polite" aria-label="Abrir acompanhamento da investigação">
      <span class="execution-tray-spinner" aria-hidden="true"></span>
      <div class="execution-tray-copy"><strong>Investigação em andamento</strong><span>Aguardando início...</span><div class="execution-tray-progress"><i></i></div></div>
      <span class="execution-tray-percent">0%</span>
      <button class="execution-tray-dismiss" type="button" aria-label="Dispensar acompanhamento">×</button>
    </aside>`;
  }

  function pipelineFor(record) {
    return record?.execution_mode === "queue" ? QUEUE_PIPELINE : BASE_PIPELINE;
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

  function currentCanonicalStage(record) {
    const current = currentPhase(record);
    const canonical = canonicalStage(current.stage);
    if (!["failed", "cancelled"].includes(canonical)) return canonical;
    const previous = [...(record?.phases || [])].reverse().find((phase) => !["failed", "cancelled"].includes(canonicalStage(phase.stage)));
    return canonicalStage(previous?.stage || "evidence_analysis");
  }

  function phaseTitle(phase) {
    return stageLabels[phase?.stage] || String(phase?.stage || "Processando").replaceAll("_", " ");
  }

  function percentValue(record = activeExecution) {
    const value = Number(record?.percent ?? currentPhase(record)?.percent ?? (record?.status === "completed" ? 100 : 0));
    return Math.max(0, Math.min(100, Number.isFinite(value) ? Math.round(value) : 0));
  }

  function isRunning(record = activeExecution) {
    return ["queued", "running", "cancelling"].includes(record?.status);
  }

  function saveActiveId(id) {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  }

  function formatDuration(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    const minutes = Math.floor(value / 60);
    const remainder = value % 60;
    if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}min`;
    if (minutes) return `${minutes}min ${remainder}s`;
    return `${remainder}s`;
  }

  function elapsedSeconds(record) {
    const start = Date.parse(record?.started_at || record?.created_at || "");
    const end = TERMINAL_STATUSES.has(record?.status) ? Date.parse(record?.completed_at || record?.updated_at || "") : Date.now();
    if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
    return Math.max(0, Math.round((end - start) / 1000));
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
        : record.status === "cancelled"
          ? "Investigação cancelada"
          : record.status === "cancelling"
            ? "Cancelando investigação"
            : `Analisando ${record.target || "o alvo"}`;
    tray.querySelector(".execution-tray-copy > span").textContent = record.status === "completed"
      ? "Clique para abrir o resultado."
      : record.status === "failed"
        ? (record.error || phase.detail || "Clique para ver os detalhes.")
        : record.status === "cancelled"
          ? (phase.detail || "A coleta foi interrompida pelo operador.")
          : `${phaseTitle(phase)} · ${phase.detail || "em andamento"}`;
    tray.querySelector(".execution-tray-percent").textContent = `${percent}%`;
    tray.querySelector(".execution-tray-progress i").style.width = `${percent}%`;
  }

  function phaseMap(record) {
    const map = new Map();
    (record.phases || []).forEach((phase) => {
      const stage = canonicalStage(phase.stage);
      const previous = map.get(stage);
      if (!previous || Date.parse(phase.updated_at || "") >= Date.parse(previous.updated_at || "")) map.set(stage, { ...phase, stage });
    });
    const current = currentPhase(record);
    const currentStage = currentCanonicalStage(record);
    const previous = map.get(currentStage);
    map.set(currentStage, { ...(previous || {}), ...current, stage: currentStage });
    return map;
  }

  function commandActivities(record) {
    const commands = new Map();
    (record?.events || []).forEach((event) => {
      if (!COMMAND_STAGES.has(event.stage) || !event.command_id) return;
      const previous = commands.get(event.command_id) || {};
      commands.set(event.command_id, { ...previous, ...event });
    });
    return [...commands.values()].sort((left, right) => Date.parse(left.updated_at || "") - Date.parse(right.updated_at || ""));
  }

  function commandStatus(event) {
    if (event.stage === "command_cancelled" || event.status === "cancelled") return ["cancelled", "Cancelado"];
    if (event.status === "failed" || Number(event.exit_code) > 0) return ["failed", `Falhou${event.exit_code !== undefined ? ` · código ${event.exit_code}` : ""}`];
    if (event.stage === "command_completed") return ["completed", `Concluído${event.exit_code !== undefined ? ` · código ${event.exit_code}` : ""}`];
    return ["running", "Em execução"];
  }

  function commandMarkup(record) {
    const commands = commandActivities(record);
    if (!commands.length) {
      const phase = currentPhase(record);
      return `<div class="execution-live-empty"><span></span><div><strong>Nenhum comando iniciado ainda</strong><p>${escapeHtml(phase.detail || "O Agent está preparando a coleta.")}</p></div></div>`;
    }
    return commands.slice(-12).reverse().map((event) => {
      const [status, label] = commandStatus(event);
      const stdout = String(event.stdout_tail || "").trim();
      const stderr = String(event.stderr_tail || "").trim();
      const output = [stdout ? `SAÍDA\n${stdout}` : "", stderr ? `ERRO\n${stderr}` : ""].filter(Boolean).join("\n\n");
      const host = event.host ? ` · ${event.host}` : "";
      return `<article class="execution-command" data-status="${status}">
        <div class="execution-command-head"><span class="execution-command-state"></span><div><strong>${escapeHtml(event.command || "Comando remoto")}</strong><small>${escapeHtml(label)}${escapeHtml(host)}${event.elapsed_seconds !== undefined ? ` · ${escapeHtml(formatDuration(event.elapsed_seconds))}` : ""}</small></div></div>
        ${output ? `<pre>${escapeHtml(output)}</pre>` : `<p class="execution-command-wait">Aguardando saída do comando...</p>`}
      </article>`;
    }).join("");
  }

  function livePanelMarkup(record) {
    const canCancel = ["queued", "running"].includes(record.status);
    const cancelling = record.status === "cancelling";
    const worker = currentPhase(record)?.worker || record.worker || "";
    const transport = record.store_backend ? `<span><b>Eventos</b>${escapeHtml(record.store_backend === "redis" ? "SSE · Redis" : "SSE · memória")}</span>` : "";
    return `<section class="execution-live-panel">
      <header class="execution-live-header"><div><p class="eyebrow">AGENT EM TEMPO REAL</p><h3>Coleta e comandos</h3></div><button class="danger-button execution-cancel-button" type="button" data-cancel-execution ${canCancel ? "" : "disabled"}>${cancelling ? "Cancelando..." : record.status === "cancelled" ? "Coleta cancelada" : "Cancelar coleta"}</button></header>
      <div class="execution-live-meta"><span><b>Tempo</b>${escapeHtml(formatDuration(elapsedSeconds(record)))}</span><span><b>Etapa</b>${escapeHtml(phaseTitle(currentPhase(record)))}</span>${worker ? `<span><b>Worker</b>${escapeHtml(worker)}</span>` : ""}${transport}</div>
      <div class="execution-command-list">${commandMarkup(record)}</div>
    </section>`;
  }

  function timelineMarkup(record) {
    const currentStage = currentCanonicalStage(record);
    const phases = phaseMap(record);
    const pipeline = pipelineFor(record);
    const currentIndex = Math.max(0, pipeline.findIndex((definition) => definition.stage === currentStage));
    const percent = percentValue(record);
    const rows = pipeline.map((definition, index) => {
      const phase = phases.get(definition.stage) || { stage: definition.stage, detail: "Aguardando a etapa anterior." };
      const failed = record.status === "failed" && currentStage === definition.stage;
      const cancelled = record.status === "cancelled" && currentStage === definition.stage;
      const cancelling = record.status === "cancelling" && currentStage === definition.stage;
      const inferredCompleted = index < currentIndex && !["queued"].includes(record.status);
      const completed = record.status === "completed" || phase.status === "completed" || inferredCompleted;
      const active = !completed && !failed && !cancelled && (currentStage === definition.stage || cancelling);
      const css = failed ? "failed" : cancelled ? "cancelled" : completed ? "completed" : active ? (cancelling ? "cancelling" : "active") : "pending";
      return `<div class="timeline-item ${css}"><span></span><div><strong>${escapeHtml(definition.label)}</strong><p>${escapeHtml(phase.detail || "Aguardando a etapa anterior.")}</p></div></div>`;
    }).join("");
    return `<div class="execution-progress-summary"><div class="execution-progress-title"><strong>${escapeHtml(record.target || "Investigação")}</strong><b>${percent}%</b></div><div class="execution-progress-bar"><i style="width:${percent}%"></i></div><p>O acompanhamento continua mesmo com este painel fechado. Clique no card inferior para voltar aos comandos em execução.</p><div class="execution-progress-meta"><span class="mode-badge">${escapeHtml(record.provider || "IA automática")}</span>${record.model ? `<span class="mode-badge">${escapeHtml(record.model)}</span>` : ""}<span class="mode-badge">${escapeHtml(record.execution_mode || "inline")}</span></div></div><div class="execution-progress-layout"><div class="execution-timeline">${rows}</div>${livePanelMarkup(record)}</div>`;
  }

  function bindProgressActions() {
    $("[data-cancel-execution]")?.addEventListener("click", cancelTrackedExecution);
  }

  function renderProgressDrawer(record, force = false) {
    const drawer = $("#result-drawer");
    if (!drawer || (!force && !drawer.classList.contains("open")) || showingFinalResult) return;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    $("#result-title").textContent = record.status === "failed"
      ? "Falha na investigação"
      : record.status === "cancelled"
        ? "Investigação cancelada"
        : record.status === "cancelling"
          ? "Cancelando investigação"
          : "Investigação em andamento";
    $("#result-content").innerHTML = timelineMarkup(record);
    bindProgressActions();
  }

  function invalidateViews() {
    state.dashboardLoaded = false;
    state.investigationsLoaded = false;
    state.inventoryLoaded = false;
    if ($("#view-inventory")?.classList.contains("active")) void loadInventory();
    if ($("#view-investigations")?.classList.contains("active")) void loadInvestigations();
  }

  function closeEventStream() {
    if (eventStream) eventStream.close();
    eventStream = null;
  }

  function completeExecution(record) {
    closeEventStream();
    clearTimeout(pollTimer);
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
    closeEventStream();
    activeExecution = record;
    renderTray(record);
    setSubmitting(false);
    renderProgressDrawer(record);
    toast(record.error || "A investigação falhou.", "error");
  }

  function cancelExecutionView(record) {
    closeEventStream();
    activeExecution = record;
    renderTray(record);
    setSubmitting(false);
    renderProgressDrawer(record);
    toast("A coleta foi cancelada com segurança.");
  }

  function applyExecutionRecord(record) {
    if (!record || !activeExecution || activeExecution.execution_id !== record.execution_id) return;
    activeExecution = record;
    renderTray(record);
    renderProgressDrawer(record);
    if (record.status === "completed") return completeExecution(record);
    if (record.status === "failed") return failExecution(record);
    if (record.status === "cancelled") return cancelExecutionView(record);
  }

  function mergeProgressEvent(event) {
    if (!activeExecution || !event) return;
    const phase = {
      ...event,
      updated_at: event.updated_at || new Date().toISOString(),
    };
    const phases = [...(activeExecution.phases || [])];
    const index = phases.findIndex((item) => item.stage === phase.stage);
    if (index >= 0) phases[index] = { ...phases[index], ...phase };
    else phases.push(phase);
    const events = [...(activeExecution.events || []), phase].slice(-500);
    activeExecution = {
      ...activeExecution,
      status: phase.status === "cancelling" ? "cancelling" : activeExecution.status,
      percent: Math.max(percentValue(activeExecution), Number(phase.percent || 0)),
      current_phase: phase,
      phases,
      events,
      updated_at: phase.updated_at,
    };
    renderTray(activeExecution);
    renderProgressDrawer(activeExecution);
  }

  function schedulePoll(id) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(() => void pollExecution(id), 1200);
  }

  async function pollExecution(id) {
    try {
      const record = await api(`/ui/api/executions/${encodeURIComponent(id)}`);
      if (!activeExecution || activeExecution.execution_id !== id) return;
      applyExecutionRecord(record);
      if (isRunning(record)) schedulePoll(id);
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

  function startEventStream(id) {
    closeEventStream();
    clearTimeout(pollTimer);
    if (!window.EventSource) {
      schedulePoll(id);
      return;
    }
    const source = new EventSource(`/ui/api/executions/${encodeURIComponent(id)}/events`);
    eventStream = source;
    source.addEventListener("progress", (message) => {
      streamFailures = 0;
      try {
        mergeProgressEvent(JSON.parse(message.data));
      } catch {
        schedulePoll(id);
      }
    });
    source.addEventListener("snapshot", (message) => {
      streamFailures = 0;
      try {
        applyExecutionRecord(JSON.parse(message.data));
      } catch {
        schedulePoll(id);
      }
    });
    source.onerror = () => {
      streamFailures += 1;
      closeEventStream();
      if (activeExecution?.execution_id === id && isRunning()) schedulePoll(id);
    };
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
      startEventStream(record.execution_id);
    } catch (error) {
      setSubmitting(false);
      toast(error.message, "error");
      $("#result-content").innerHTML = `<div class="result-section error-section"><h3>Investigação não iniciada</h3><p>${escapeHtml(error.message)}</p></div>`;
    }
  }

  async function cancelTrackedExecution(event) {
    event?.stopPropagation();
    if (!activeExecution || !["queued", "running"].includes(activeExecution.status)) return;
    if (!window.confirm("Cancelar a coleta atual? O Agent interromperá o comando remoto em execução sem reiniciar o servidor.")) return;
    const button = event?.currentTarget;
    if (button) {
      button.disabled = true;
      button.textContent = "Cancelando...";
    }
    try {
      activeExecution = await api(`/ui/api/executions/${encodeURIComponent(activeExecution.execution_id)}/cancel`, { method: "POST" });
      renderTray(activeExecution);
      renderProgressDrawer(activeExecution, true);
      startEventStream(activeExecution.execution_id);
    } catch (error) {
      if (button) button.disabled = false;
      toast(error.message, "error");
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
    closeEventStream();
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
        startEventStream(id);
      }
    } catch {
      activeExecution = null;
      saveActiveId(null);
    }
  }

  function setupExecutionTracking() {
    if (!$("#execution-tray")) document.body.insertAdjacentHTML("beforeend", executionTrayMarkup());
    $("#execution-tray")?.addEventListener("click", openTrackedExecution);
    $("#execution-tray")?.addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) {
        event.preventDefault();
        openTrackedExecution();
      }
    });
    $("#execution-tray .execution-tray-dismiss")?.addEventListener("click", dismissTrackedExecution);
    $("#analysis-form")?.addEventListener("submit", startTrackedAnalysis, true);
    void restoreExecution();
  }

  window.addEventListener("beforeunload", closeEventStream);
  document.addEventListener("DOMContentLoaded", setupExecutionTracking);
})();