function parseTargetList(value) {
  const rows = [];
  const seen = new Set();
  String(value || "").split(/\r?\n/).forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) return;
    line.split(/[;,]/).forEach((rawToken) => {
      const token = rawToken.trim();
      if (!token) return;
      let target = token;
      let sshPort = null;
      const bracketed = token.match(/^\[([^\]]+)\]:(\d{1,5})$/);
      if (bracketed) {
        target = bracketed[1];
        sshPort = Number(bracketed[2]);
      } else if ((token.match(/:/g) || []).length === 1) {
        const [host, port] = token.split(":");
        if (host && /^\d{1,5}$/.test(port || "")) {
          target = host.trim();
          sshPort = Number(port);
        }
      } else if (token.includes("|")) {
        const position = token.lastIndexOf("|");
        const port = token.slice(position + 1).trim();
        if (/^\d{1,5}$/.test(port)) {
          target = token.slice(0, position).trim();
          sshPort = Number(port);
        }
      }
      if (!target || (sshPort != null && (sshPort < 1 || sshPort > 65535))) return;
      const key = `${target.toLowerCase()}|${sshPort || ""}`;
      if (seen.has(key)) return;
      seen.add(key);
      const row = { target };
      if (sshPort != null) row.ssh_port = sshPort;
      rows.push(row);
    });
  });
  return rows;
}

function targetInputToken(item) {
  if (!item.ssh_port) return item.target;
  return String(item.target).includes(":") ? `[${item.target}]:${item.ssh_port}` : `${item.target}:${item.ssh_port}`;
}

function batchStatusLabel(status) {
  return { pending: "Pendente", running: "Em execução", queued: "Na fila", completed: "Concluída", failed: "Falhou" }[status] || status;
}

function setupBatchState() {
  state.batch = {
    importedItems: [],
    filename: null,
    warnings: [],
    results: [],
    accessMonitors: [],
    config: { enabled: true, max_targets: 50, concurrency: 2, max_file_bytes: 1000000 },
  };
}

async function loadBatchConfig() {
  try {
    const config = await api("/ui/api/batches/config");
    state.batch.config = { ...state.batch.config, ...config };
    if (!config.enabled) $("#batch-import-block")?.setAttribute("hidden", "hidden");
  } catch (error) {
    state.batch.config.enabled = false;
    $("#batch-import-block")?.setAttribute("hidden", "hidden");
  }
}

function setupAccessMonitorControls() {
  const targetRow = $("#analysis-form .target-entry-row");
  if (!targetRow || $("#access-monitor")) return;
  const block = document.createElement("div");
  block.className = "compact-settings-grid access-monitor-grid";
  block.innerHTML = `
    <label><span>Servidor de acesso</span><select id="access-monitor"><option value="monitor1">Carregando monitores...</option></select></label>
    <label class="range-scan-field"><span>Varredura</span><span class="project-check-field"><input id="range-scan" type="checkbox"><span>Pesquisar por faixa</span></span></label>
    <div class="access-monitor-register"><span>Novo ponto de acesso</span><button type="button" class="secondary-button" id="add-access-monitor">Cadastrar novo servidor</button></div>
    <div class="range-scan-hint"><span>Faixa</span><small id="range-scan-help">Desmarcado: o IP informado é analisado como um único servidor.</small></div>`;
  targetRow.insertAdjacentElement("afterend", block);
  const fileLabel = $("#analysis-form .attach-field > span");
  if (fileLabel) fileLabel.textContent = "Playbook";
  const attachButton = $("#attach-batch-file");
  if (attachButton) {
    attachButton.title = "Anexar playbook/lote de alvos";
    const label = attachButton.querySelector("span");
    if (label) label.textContent = "Anexar";
  }
  $("#range-scan")?.addEventListener("change", updateRangeScanHint);
  $("#add-access-monitor")?.addEventListener("click", registerAccessMonitor);
}

function updateRangeScanHint() {
  const enabled = Boolean($("#range-scan")?.checked);
  const help = $("#range-scan-help");
  const target = $("#target");
  if (help) help.textContent = enabled
    ? "Ativo: 172.27.233 significa toda a rede 172.27.233.0/24. CIDR e intervalos também são aceitos."
    : "Desmarcado: o IP informado é analisado como um único servidor.";
  if (target) target.placeholder = enabled
    ? "Ex.: 172.27.233 ou 172.27.233.0/24"
    : "172.27.232.203 ou servidor:2222";
}

async function loadAccessMonitors(selectedId = null) {
  try {
    const data = await api("/ui/api/access-monitors");
    state.batch.accessMonitors = data.items || [];
    const select = $("#access-monitor");
    if (!select) return;
    select.innerHTML = state.batch.accessMonitors.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)} — ${escapeHtml(item.host)}</option>`).join("");
    const preferred = selectedId || data.default || "monitor1";
    if (state.batch.accessMonitors.some((item) => item.id === preferred)) select.value = preferred;
  } catch (error) {
    const select = $("#access-monitor");
    if (select) select.innerHTML = '<option value="monitor1">Monitor 1 — configuração atual</option>';
  }
}

async function registerAccessMonitor() {
  const label = window.prompt("Nome do novo servidor de acesso:", "Novo monitor");
  if (!label) return;
  const host = window.prompt("IP ou hostname do servidor de acesso:", "");
  if (!host) return;
  try {
    const item = await api("/ui/api/access-monitors", { method: "POST", body: { label, host } });
    await loadAccessMonitors(item.id);
    toast(`${item.label} cadastrado. Usuário e senha continuam os mesmos de SSH_SRV_VPN_*.`);
  } catch (error) {
    toast(error.message, "error");
  }
}

function importedItemsForTargets(parsedTargets) {
  const pools = new Map();
  (state.batch.importedItems || []).forEach((item) => {
    const key = String(item.target || "").toLowerCase();
    const rows = pools.get(key) || [];
    rows.push(item);
    pools.set(key, rows);
  });
  return parsedTargets.map((parsed) => {
    const key = parsed.target.toLowerCase();
    const candidates = pools.get(key) || [];
    let imported = null;
    if (parsed.ssh_port != null) {
      const index = candidates.findIndex((item) => Number(item.ssh_port || 0) === parsed.ssh_port);
      if (index >= 0) imported = candidates.splice(index, 1)[0];
    }
    if (!imported && candidates.length) imported = candidates.shift();
    return { ...(imported || {}), ...parsed, target: parsed.target };
  });
}

function renderImportedBatch() {
  const element = $("#batch-import-summary");
  const clearButton = $("#clear-batch-file");
  const items = state.batch.importedItems || [];
  if (!items.length) {
    element.innerHTML = "";
    element.hidden = true;
    clearButton.hidden = true;
    return;
  }
  element.hidden = false;
  clearButton.hidden = false;
  const preview = items.slice(0, 8).map((item) => {
    const details = [item.ssh_port ? `porta ${item.ssh_port}` : null, item.environment ? labelEnvironment(item.environment) : null, item.playbook_id ? `playbook ${item.playbook_id}` : null].filter(Boolean).join(" · ");
    return `<li><strong>${escapeHtml(item.display_name || item.target)}</strong><span>${escapeHtml(item.target)}${details ? ` · ${escapeHtml(details)}` : ""}</span></li>`;
  }).join("");
  const remaining = items.length > 8 ? `<p>Mais ${items.length - 8} alvo(s) carregado(s).</p>` : "";
  const warnings = (state.batch.warnings || []).length ? `<p>${escapeHtml(state.batch.warnings.join(" | "))}</p>` : "";
  element.innerHTML = `<strong>${escapeHtml(state.batch.filename || "Playbook")}: ${items.length} alvo(s)</strong><ul>${preview}</ul>${remaining}${warnings}`;
}

async function importBatchFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > state.batch.config.max_file_bytes) {
    toast(`O arquivo excede o limite de ${state.batch.config.max_file_bytes} bytes.`, "error");
    event.target.value = "";
    return;
  }
  try {
    const content = await file.text();
    const parsed = await api("/ui/api/batches/parse", { method: "POST", body: { filename: file.name, content } });
    state.batch.importedItems = parsed.items || [];
    state.batch.filename = parsed.filename || file.name;
    state.batch.warnings = parsed.warnings || [];
    state.batch.config = { ...state.batch.config, ...(parsed.limits || {}) };
    $("#target").value = state.batch.importedItems.map(targetInputToken).join("\n");
    renderImportedBatch();
    toast(`${state.batch.importedItems.length} alvo(s) importado(s).`);
  } catch (error) {
    state.batch.importedItems = [];
    state.batch.filename = null;
    state.batch.warnings = [];
    renderImportedBatch();
    event.target.value = "";
    toast(error.message, "error");
  }
}

function clearBatchFile() {
  state.batch.importedItems = [];
  state.batch.filename = null;
  state.batch.warnings = [];
  $("#batch-file").value = "";
  renderImportedBatch();
}

function baseAnalysisPayload() {
  return {
    objective: $("#objective").value.trim(),
    environment: $("#environment").value,
    mode: $("#mode").value,
    ssh_port: $("#ssh-port").value ? Number($("#ssh-port").value) : null,
    provider: $("#provider").value,
    model: $("#model").value || null,
    playbook_mode: $("#playbook-mode").value,
    playbook_id: $("#playbook-id").value || null,
    access_monitor_id: $("#access-monitor")?.value || "monitor1",
    range_scan: false,
  };
}

function buildTargetPayload(item, base) {
  const playbookId = item.playbook_id || base.playbook_id || null;
  const playbookMode = item.playbook_mode || (playbookId ? "manual" : base.playbook_mode);
  return {
    target: item.target,
    objective: item.objective || base.objective,
    environment: item.environment || base.environment,
    mode: item.mode || base.mode,
    ssh_port: item.ssh_port ?? base.ssh_port,
    provider: item.provider || base.provider,
    model: item.model || base.model,
    playbook_mode: playbookMode,
    playbook_id: playbookId,
    access_monitor_id: base.access_monitor_id,
    range_scan: false,
  };
}

function validateBatchPayloads(payloads) {
  if (!payloads.length) throw new Error("Informe ao menos um IP, hostname ou site.");
  if (payloads.length > state.batch.config.max_targets) throw new Error(`O lote excede o limite de ${state.batch.config.max_targets} alvos.`);
  payloads.forEach((payload) => {
    const provider = state.providers.find((item) => item.provider === payload.provider);
    if (!provider?.selectable) throw new Error(`O provedor ${payload.provider || "informado"} não está disponível para ${payload.target}.`);
    if (payload.playbook_mode === "manual" && !payload.playbook_id) throw new Error(`O alvo ${payload.target} está no modo manual sem playbook.`);
  });
}

async function waitForBatchJob(jobId, onStatus) {
  while (true) {
    const job = await api(`/ui/api/jobs/${encodeURIComponent(jobId)}`);
    onStatus?.(job.status === "running" ? "running" : "queued");
    if (job.status === "completed") return job.result;
    if (job.status === "failed") throw new Error(job.error || "A execução na fila falhou.");
    await new Promise((resolve) => setTimeout(resolve, 2200));
  }
}

function evidenceMarkup(result) {
  const evidence = result?.evidence || [];
  const range = result?.range_scan || result?.analysis?.range_scan || null;
  const evidenceHtml = evidence.length ? `<section class="result-section execution-evidence"><h3>Comandos executados e retornos</h3><p>A IA executou estas validações. Use as saídas abaixo como evidência/print; não é necessário repetir os comandos.</p><div class="execution-evidence-list">${evidence.map((item, index) => {
    const title = item.purpose || item.tool || `Validação ${index + 1}`;
    const command = item.command || item.tool || "coleta estruturada";
    const stdout = String(item.stdout || "").trim();
    const stderr = String(item.stderr || "").trim();
    return `<article class="execution-evidence-card"><div><strong>${escapeHtml(title)}</strong><span>${item.orchestrator === "ansible" ? "ANSIBLE" : "IA"} · exit ${escapeHtml(item.exit_code ?? "—")}</span></div><code>${escapeHtml(command)}</code>${stdout ? `<pre>${escapeHtml(stdout)}</pre>` : '<pre>Sem saída em stdout.</pre>'}${stderr ? `<small class="evidence-stderr">stderr: ${escapeHtml(stderr)}</small>` : ""}</article>`;
  }).join("")}</div></section>` : "";
  const rangeHtml = range?.hosts?.length ? `<section class="result-section"><h3>Servidores encontrados na faixa</h3><div class="batch-result-list">${range.hosts.map((host) => `<article class="batch-result-item" data-state="${escapeHtml(host.status || "inconclusive")}"><div><strong>${escapeHtml(host.hostname || host.address)}</strong><span>${escapeHtml(host.address)}${host.ssh_port ? `:${escapeHtml(host.ssh_port)}` : ""}</span><small>${escapeHtml(host.probable_cause || host.triage_summary || host.error || "Triagem concluída")}</small></div><div><span class="mode-badge">${escapeHtml(host.deep_analyzed ? "Análise profunda" : "Triagem")}</span></div></article>`).join("")}</div></section>` : "";
  return `${rangeHtml}${evidenceHtml}`;
}

const _showResultBase = showResult;
showResult = function showResultWithExecutedEvidence(result) {
  _showResultBase(result);
  const content = $("#result-content");
  const markup = evidenceMarkup(result);
  if (content && markup) content.insertAdjacentHTML("beforeend", markup);
};

function renderBatchExecution() {
  const rows = state.batch.results || [];
  const completed = rows.filter((item) => item.status === "completed").length;
  const failed = rows.filter((item) => item.status === "failed").length;
  const running = rows.filter((item) => ["running", "queued"].includes(item.status)).length;
  const pending = rows.filter((item) => item.status === "pending").length;
  $("#result-drawer").classList.add("open");
  $("#result-drawer").setAttribute("aria-hidden", "false");
  $("#result-title").textContent = `Execução em lote · ${completed}/${rows.length}`;
  $("#result-content").innerHTML = `<section class="result-section batch-overview"><h3>Andamento</h3><p>${completed} concluída(s) · ${failed} falha(s) · ${running} em execução · ${pending} pendente(s)</p></section><div class="batch-result-list">${rows.map((row, index) => {
    const error = row.error ? `<small>${escapeHtml(row.error)}</small>` : "";
    const action = row.result ? `<button type="button" class="ghost-button" data-batch-result="${index}">Ver resultado</button>` : "";
    return `<article class="batch-result-item" data-state="${escapeHtml(row.status)}"><div><strong>${escapeHtml(row.display_name || row.payload.target)}</strong><span>${escapeHtml(row.payload.target)}${row.payload.ssh_port ? `:${escapeHtml(row.payload.ssh_port)}` : ""}</span>${error}</div><div><span class="mode-badge">${escapeHtml(batchStatusLabel(row.status))}</span>${action}</div></article>`;
  }).join("")}</div>`;
  $$('[data-batch-result]', $("#result-content")).forEach((button) => button.addEventListener("click", () => {
    const row = state.batch.results[Number(button.dataset.batchResult)];
    if (row?.result) showResult(row.result);
  }));
}

async function executeBatch(payloads, sourceItems) {
  state.batch.results = payloads.map((payload, index) => ({ payload, display_name: sourceItems[index]?.display_name || payload.target, status: "pending", result: null, error: null }));
  setSubmitting(true);
  renderBatchExecution();
  let cursor = 0;
  const workerCount = Math.min(state.batch.config.concurrency || 2, payloads.length);
  async function worker() {
    while (true) {
      const index = cursor;
      cursor += 1;
      if (index >= payloads.length) return;
      const row = state.batch.results[index];
      row.status = "running";
      renderBatchExecution();
      try {
        let result = await api("/ui/api/batches/investigations", { method: "POST", body: row.payload });
        if (result.job_id) {
          row.status = "queued";
          renderBatchExecution();
          result = await waitForBatchJob(result.job_id, (status) => { row.status = status; renderBatchExecution(); });
        }
        row.result = result;
        row.status = "completed";
      } catch (error) {
        row.error = error.message;
        row.status = "failed";
      }
      renderBatchExecution();
    }
  }
  try {
    await Promise.all(Array.from({ length: workerCount }, () => worker()));
    state.dashboardLoaded = false;
    state.investigationsLoaded = false;
    state.inventoryLoaded = false;
    const failures = state.batch.results.filter((item) => item.status === "failed").length;
    toast(failures ? `Lote concluído com ${failures} falha(s).` : "Lote concluído e registrado no histórico.", failures ? "error" : "success");
  } finally { setSubmitting(false); }
}

async function executeEnhancedSingle(payload) {
  setSubmitting(true);
  showExecutionStart(payload);
  try {
    let result = await api("/ui/api/investigations", { method: "POST", body: payload });
    if (result.job_id) result = await pollJob(result.job_id);
    showResult(result);
    state.dashboardLoaded = false;
    state.investigationsLoaded = false;
    state.inventoryLoaded = false;
    toast(payload.range_scan ? "Varredura concluída. Os servidores encontrados foram analisados." : "Investigação concluída. As saídas executadas estão no resultado.");
  } catch (error) {
    toast(error.message, "error");
    $("#result-content").innerHTML = `<div class="result-section error-section"><h3>Investigação não iniciada</h3><p>${escapeHtml(error.message)}</p></div>`;
  } finally { setSubmitting(false); }
}

function interceptBatchSubmit(event) {
  const rawTarget = $("#target").value.trim();
  const rangeScan = Boolean($("#range-scan")?.checked);
  const parsedTargets = parseTargetList(rawTarget);
  const hasImportedMetadata = (state.batch.importedItems || []).length > 0;
  const parsedSingleWithPort = parsedTargets.length === 1 && parsedTargets[0].ssh_port != null;
  const needsBatchHandling = hasImportedMetadata || parsedTargets.length > 1 || parsedSingleWithPort || rawTarget.includes(";") || rawTarget.includes(",") || rawTarget.includes("\n");

  event.preventDefault();
  event.stopImmediatePropagation();
  try {
    const base = baseAnalysisPayload();
    if (rangeScan) {
      if (!rawTarget) throw new Error("Informe a faixa a pesquisar.");
      const provider = state.providers.find((item) => item.provider === base.provider);
      if (!provider?.selectable) throw new Error("Selecione uma IA disponível.");
      if (base.playbook_mode === "manual" && !base.playbook_id) throw new Error("Selecione o playbook manual.");
      void executeEnhancedSingle({ ...base, target: rawTarget, range_scan: true });
      return;
    }
    if (!needsBatchHandling) {
      if (!rawTarget) throw new Error("Informe o IP ou hostname do servidor.");
      const provider = state.providers.find((item) => item.provider === base.provider);
      if (!provider?.selectable) throw new Error("Selecione uma IA disponível.");
      if (base.playbook_mode === "manual" && !base.playbook_id) throw new Error("Selecione o playbook manual.");
      void executeEnhancedSingle({ ...base, target: rawTarget, range_scan: false });
      return;
    }
    const sourceItems = importedItemsForTargets(parsedTargets);
    const payloads = sourceItems.map((item) => buildTargetPayload(item, base));
    validateBatchPayloads(payloads);
    void executeBatch(payloads, sourceItems);
  } catch (error) {
    toast(error.message, "error");
  }
}

function setupBatchExecution() {
  setupBatchState();
  setupAccessMonitorControls();
  void Promise.all([loadBatchConfig(), loadAccessMonitors()]);
  $("#batch-file")?.addEventListener("change", importBatchFile);
  $("#clear-batch-file")?.addEventListener("click", clearBatchFile);
  $("#analysis-form")?.addEventListener("submit", interceptBatchSubmit, true);
  $("#clear-form")?.addEventListener("click", () => {
    clearBatchFile();
    state.batch.results = [];
    if ($("#range-scan")) $("#range-scan").checked = false;
    updateRangeScanHint();
  });
}

document.addEventListener("DOMContentLoaded", setupBatchExecution);
