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
  return String(item.target).includes(":")
    ? `[${item.target}]:${item.ssh_port}`
    : `${item.target}:${item.ssh_port}`;
}

function batchStatusLabel(status) {
  return {
    pending: "Pendente",
    running: "Em execução",
    queued: "Na fila",
    completed: "Concluída",
    failed: "Falhou",
  }[status] || status;
}

function setupBatchState() {
  state.batch = {
    importedItems: [],
    filename: null,
    warnings: [],
    results: [],
    config: { enabled: true, max_targets: 50, concurrency: 2, max_file_bytes: 1000000 },
  };
}

async function loadBatchConfig() {
  try {
    const config = await api("/ui/api/batches/config");
    state.batch.config = { ...state.batch.config, ...config };
    if (!config.enabled) {
      $("#batch-import-block")?.setAttribute("hidden", "hidden");
    }
  } catch (error) {
    state.batch.config.enabled = false;
    $("#batch-import-block")?.setAttribute("hidden", "hidden");
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
    const details = [
      item.ssh_port ? `porta ${item.ssh_port}` : null,
      item.environment ? labelEnvironment(item.environment) : null,
      item.playbook_id ? `playbook ${item.playbook_id}` : null,
    ].filter(Boolean).join(" · ");
    return `<li><strong>${escapeHtml(item.display_name || item.target)}</strong><span>${escapeHtml(item.target)}${details ? ` · ${escapeHtml(details)}` : ""}</span></li>`;
  }).join("");
  const remaining = items.length > 8 ? `<p>Mais ${items.length - 8} alvo(s) carregado(s).</p>` : "";
  const warnings = (state.batch.warnings || []).length
    ? `<p>${escapeHtml(state.batch.warnings.join(" | "))}</p>`
    : "";
  element.innerHTML = `<strong>${escapeHtml(state.batch.filename || "Arquivo")}: ${items.length} alvo(s)</strong><ul>${preview}</ul>${remaining}${warnings}`;
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
    const parsed = await api("/ui/api/batches/parse", {
      method: "POST",
      body: { filename: file.name, content },
    });
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
  };
}

function validateBatchPayloads(payloads) {
  if (!payloads.length) throw new Error("Informe ao menos um IP, hostname ou site.");
  if (payloads.length > state.batch.config.max_targets) {
    throw new Error(`O lote excede o limite de ${state.batch.config.max_targets} alvos.`);
  }
  payloads.forEach((payload, index) => {
    if (!payload.objective || payload.objective.length < 3) {
      throw new Error(`O alvo ${index + 1} (${payload.target}) não possui objetivo de análise.`);
    }
    const provider = state.providers.find((item) => item.provider === payload.provider);
    if (!provider?.selectable) {
      throw new Error(`O provedor ${payload.provider || "informado"} não está disponível para ${payload.target}.`);
    }
    if (payload.playbook_mode === "manual" && !payload.playbook_id) {
      throw new Error(`O alvo ${payload.target} está no modo manual sem playbook.`);
    }
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
  state.batch.results = payloads.map((payload, index) => ({
    payload,
    display_name: sourceItems[index]?.display_name || payload.target,
    status: "pending",
    result: null,
    error: null,
  }));
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
          result = await waitForBatchJob(result.job_id, (status) => {
            row.status = status;
            renderBatchExecution();
          });
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
  } finally {
    setSubmitting(false);
  }
}

function interceptBatchSubmit(event) {
  const parsedTargets = parseTargetList($("#target").value);
  const hasImportedMetadata = (state.batch.importedItems || []).length > 0;
  const rawTarget = $("#target").value.trim();
  const parsedSingleWithPort = parsedTargets.length === 1 && parsedTargets[0].ssh_port != null;
  const needsBatchHandling = hasImportedMetadata || parsedTargets.length > 1 || parsedSingleWithPort || rawTarget.includes(";") || rawTarget.includes(",") || rawTarget.includes("\n");

  if (!needsBatchHandling) {
    if (!$("#objective").value.trim()) {
      event.preventDefault();
      event.stopImmediatePropagation();
      toast("Descreva o objetivo da análise.", "error");
    }
    return;
  }

  event.preventDefault();
  event.stopImmediatePropagation();
  try {
    const sourceItems = importedItemsForTargets(parsedTargets);
    const base = baseAnalysisPayload();
    const payloads = sourceItems.map((item) => buildTargetPayload(item, base));
    validateBatchPayloads(payloads);
    void executeBatch(payloads, sourceItems);
  } catch (error) {
    toast(error.message, "error");
  }
}

function setupBatchExecution() {
  setupBatchState();
  void loadBatchConfig();
  $("#batch-file")?.addEventListener("change", importBatchFile);
  $("#clear-batch-file")?.addEventListener("click", clearBatchFile);
  $("#analysis-form")?.addEventListener("submit", interceptBatchSubmit, true);
  $("#clear-form")?.addEventListener("click", () => {
    clearBatchFile();
    state.batch.results = [];
  });
}

document.addEventListener("DOMContentLoaded", setupBatchExecution);
