const state = {
  session: null,
  dashboardLoaded: false,
  investigationsLoaded: false,
  inventoryLoaded: false,
  playbooksLoaded: false,
  healthLoaded: false,
  providersLoaded: false,
  approvalToken: null,
  currentInvestigationId: null,
  providers: [],
  playbooks: [],
};

const viewMeta = {
  dashboard: ["CENTRAL OPERACIONAL", "Visão geral"],
  analysis: ["INVESTIGAÇÃO GUIADA", "Nova análise"],
  investigations: ["MEMÓRIA OPERACIONAL", "Investigações"],
  inventory: ["ALVOS CONHECIDOS", "Inventário"],
  playbooks: ["AUTOMAÇÃO CONTROLADA", "Playbooks"],
  health: ["AUTODIAGNÓSTICO", "Saúde da aplicação"],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.headers || {}) } };
  if (init.body && typeof init.body !== "string") {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }
  if ((init.method || "GET").toUpperCase() !== "GET") init.headers["X-Agent-UI"] = "1";
  const response = await fetch(path, init);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail : payload;
    throw new Error(detail || `Erro HTTP ${response.status}`);
  }
  return payload;
}

function toast(message, type = "success") {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show ${type === "error" ? "error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.className = "toast"; }, 4200);
}

function initials(name) {
  return String(name || "OP").split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function formatDuration(ms) {
  const value = Number(ms || 0);
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(1)} s`;
  return `${(value / 60000).toFixed(1)} min`;
}

function labelStatus(status) {
  const labels = { healthy: "Saudável", attention: "Atenção", critical: "Crítico", inconclusive: "Inconclusivo" };
  return labels[status] || status || "Inconclusivo";
}

function labelEnvironment(environment) {
  const labels = { production: "Produção", standby: "Standby", monitoring: "Monitoramento", training: "Treinamento", unknown: "Desconhecido" };
  return labels[environment] || environment || "Desconhecido";
}

function providerStateLabel(value) {
  const labels = {
    available: "Disponível",
    degraded: "Degradado",
    unavailable: "Indisponível",
    misconfigured: "Configuração inválida",
    not_configured: "Não configurado",
  };
  return labels[value] || value || "Desconhecido";
}

function statusBadge(status) {
  const safeStatus = ["healthy", "attention", "critical", "inconclusive"].includes(status) ? status : "inconclusive";
  return `<span class="status-badge ${safeStatus}">${escapeHtml(labelStatus(status))}</span>`;
}

function environmentBadge(environment) {
  return `<span class="environment-badge">${escapeHtml(labelEnvironment(environment))}</span>`;
}

function providerBadge(provider, model) {
  if (!provider) return "";
  return `<span class="mode-badge">${escapeHtml(provider)}${model ? ` · ${escapeHtml(model)}` : ""}</span>`;
}

function showView(name) {
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  const [eyebrow, title] = viewMeta[name] || viewMeta.dashboard;
  $("#page-eyebrow").textContent = eyebrow;
  $("#page-title").textContent = title;
  window.scrollTo({ top: 0, behavior: "smooth" });

  if (name === "dashboard" && !state.dashboardLoaded) loadDashboard();
  if (name === "investigations" && !state.investigationsLoaded) loadInvestigations();
  if (name === "inventory" && !state.inventoryLoaded) loadInventory();
  if (name === "playbooks" && !state.playbooksLoaded) loadPlaybooks();
  if (name === "health" && !state.healthLoaded) loadHealth();
}

function setupTheme() {
  const stored = localStorage.getItem("agent-ui-theme") || "dark";
  document.documentElement.dataset.theme = stored;
  $("#theme-toggle").textContent = stored === "light" ? "☾" : "☼";
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("agent-ui-theme", next);
  $("#theme-toggle").textContent = next === "light" ? "☾" : "☼";
}

async function loadSession() {
  state.session = await api("/ui/api/session");
  $("#operator-name").textContent = state.session.operator;
  $("#operator-avatar").textContent = initials(state.session.operator);
  $("#execution-mode-badge").textContent = `modo ${state.session.execution_mode}`;
  $("#safety-list").innerHTML = state.session.safe_rules.map((rule) => `<li>${escapeHtml(rule)}</li>`).join("");
  await Promise.all([loadProviderOptions(), loadPlaybookOptions()]);
}

async function loadProviderOptions() {
  const data = await api("/ui/api/ai/providers");
  state.providers = data.items || [];
  const select = $("#provider");
  select.innerHTML = state.providers.map((item) => {
    const unavailable = item.selectable ? "" : " disabled";
    return `<option value="${escapeHtml(item.provider)}"${unavailable}>${escapeHtml(item.label)} — ${escapeHtml(providerStateLabel(item.state))}</option>`;
  }).join("");

  const defaultName = data.default_provider || state.session?.default_provider;
  const selected = state.providers.find((item) => item.provider === defaultName && item.selectable)
    || state.providers.find((item) => item.selectable)
    || state.providers[0];
  if (selected) select.value = selected.provider;
  state.providersLoaded = true;
  updateProviderSelection();
}

function updateProviderSelection() {
  const provider = state.providers.find((item) => item.provider === $("#provider").value);
  const model = $("#model");
  if (!provider) {
    model.innerHTML = '<option value="">Nenhum modelo disponível</option>';
    model.disabled = true;
    renderProviderStatus(null);
    return;
  }
  const options = provider.options || [];
  model.innerHTML = options.length
    ? options.map((item) => `<option value="${escapeHtml(item.value)}"${item.available === false ? " disabled" : ""}${item.default ? " selected" : ""}>${escapeHtml(item.label)}</option>`).join("")
    : `<option value="${escapeHtml(provider.model || "")}">${escapeHtml(provider.model || "Modelo padrão")}</option>`;
  model.disabled = !provider.selectable || options.length <= 1;
  renderProviderStatus(provider);
}

function renderProviderStatus(provider) {
  const element = $("#provider-status");
  if (!provider) {
    element.dataset.state = "unavailable";
    element.innerHTML = '<span class="provider-status-dot"></span><div><strong>Nenhum provedor disponível</strong><p>Revise o painel de saúde e o arquivo .env.</p></div>';
    return;
  }
  element.dataset.state = provider.state;
  element.innerHTML = `<span class="provider-status-dot"></span><div><strong>${escapeHtml(provider.label)} — ${escapeHtml(providerStateLabel(provider.state))}</strong><p>${escapeHtml(provider.detail)}${provider.latency_ms != null ? ` · ${escapeHtml(provider.latency_ms)} ms` : ""}</p></div>`;
}

async function loadPlaybookOptions() {
  const data = await api("/ui/api/playbooks");
  state.playbooks = data.items || [];
  const select = $("#playbook-id");
  select.innerHTML = '<option value="">Selecione um playbook</option>' + state.playbooks.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}</option>`).join("");
  updatePlaybookMode();
}

function updatePlaybookMode() {
  const manual = $("#playbook-mode").value === "manual";
  $("#playbook-id").disabled = !manual;
  $("#playbook-id").required = manual;
  if (!manual) $("#playbook-id").value = "";
}

function updateCorrectionMode() {
  const allowed = ["monitoring", "training"].includes($("#environment").value);
  const option = $('#mode option[value="correct"]');
  option.disabled = !allowed;
  if (!allowed && $("#mode").value === "correct") $("#mode").value = "propose";
  $("#mode-help").textContent = allowed
    ? "A ação só ocorre após segunda IA, token e aprovação humana."
    : "Correção só pode ser solicitada em monitoramento ou treinamento.";
}

function renderMetrics(metrics) {
  const byStatus = metrics.by_status || {};
  const byMode = metrics.by_mode || {};
  const approval = metrics.approval_executions || {};
  const cards = [
    ["Investigações", metrics.investigations_total || 0, "total persistido no PostgreSQL"],
    ["Duração média", formatDuration(metrics.average_duration_ms), "tempo médio de análise"],
    ["Casos críticos", byStatus.critical || 0, `${byStatus.attention || 0} casos em atenção`],
    ["Aprovações validadas", approval.validated || approval.completed || approval.success || 0, `${byMode.propose || 0} análises no modo propor`],
  ];
  $("#metrics-grid").innerHTML = cards.map(([label, value, detail]) => `<article class="metric-card"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${escapeHtml(value)}</div><div class="metric-detail">${escapeHtml(detail)}</div></article>`).join("");
}

function investigationRow(item, columns = "recent") {
  const hostname = item.hostname || item.target;
  const targetLine = item.hostname && item.target !== item.hostname ? item.target : "";
  if (columns === "recent") {
    return `<tr class="clickable" data-investigation-id="${escapeHtml(item.id)}"><td><strong>${escapeHtml(hostname)}</strong><small>${escapeHtml(targetLine)}</small></td><td>${environmentBadge(item.environment)}</td><td>${statusBadge(item.status)}</td><td>${escapeHtml(item.confidence ?? 0)}%</td><td>${escapeHtml(formatDate(item.created_at))}</td></tr>`;
  }
  return `<tr class="clickable" data-investigation-id="${escapeHtml(item.id)}"><td><strong>${escapeHtml(hostname)}</strong><small>${escapeHtml(targetLine)}</small></td><td title="${escapeHtml(item.objective)}">${escapeHtml(String(item.objective || "").slice(0, 82))}${String(item.objective || "").length > 82 ? "…" : ""}</td><td>${escapeHtml(item.playbook?.title || "Automático")}</td><td>${environmentBadge(item.environment)}</td><td>${statusBadge(item.status)}</td><td>${escapeHtml(item.confidence ?? 0)}%</td><td>${escapeHtml(formatDate(item.created_at))}</td></tr>`;
}

function bindInvestigationRows(root = document) {
  $$('[data-investigation-id]', root).forEach((row) => row.addEventListener("click", () => openInvestigation(row.dataset.investigationId)));
}

async function loadDashboard() {
  try {
    const data = await api("/ui/api/dashboard");
    renderMetrics(data.metrics || {});
    const items = data.recent?.items || [];
    $("#recent-investigations").innerHTML = items.length ? items.map((item) => investigationRow(item, "recent")).join("") : '<tr><td colspan="5" class="empty-cell">Nenhuma investigação registrada ainda.</td></tr>';
    bindInvestigationRows($("#recent-investigations"));
    state.dashboardLoaded = true;
  } catch (error) {
    $("#metrics-grid").innerHTML = '<article class="metric-card"><div class="metric-label">Falha ao carregar</div><div class="metric-detail">Verifique PostgreSQL e configuração da API.</div></article>';
    toast(error.message, "error");
  }
}

async function loadInvestigations() {
  const params = new URLSearchParams();
  const q = $("#investigation-search").value.trim();
  const environment = $("#investigation-environment").value;
  const status = $("#investigation-status").value;
  if (q) params.set("q", q);
  if (environment) params.set("environment", environment);
  if (status) params.set("status", status);
  params.set("limit", "100");
  $("#investigations-table").innerHTML = '<tr><td colspan="7" class="empty-cell">Carregando histórico...</td></tr>';
  try {
    const data = await api(`/ui/api/investigations?${params}`);
    $("#investigations-table").innerHTML = data.items.length ? data.items.map((item) => investigationRow(item, "full")).join("") : '<tr><td colspan="7" class="empty-cell">Nenhuma investigação encontrada.</td></tr>';
    $("#investigation-total").textContent = `${data.total} registro(s)`;
    bindInvestigationRows($("#investigations-table"));
    state.investigationsLoaded = true;
  } catch (error) { toast(error.message, "error"); }
}

function inventoryCard(host) {
  const mapping = host.mapping;
  return `<article class="inventory-card"><div class="card-top"><div><h4>${escapeHtml(host.hostname || host.vpn_ip)}</h4><p>${escapeHtml(host.vpn_ip)}:${escapeHtml(host.ssh_port)}</p></div>${environmentBadge(host.environment)}</div><div class="card-meta"><div><span>SO</span><strong>${escapeHtml(host.os_name || "Não identificado")}</strong></div><div><span>Tipo</span><strong>${escapeHtml(host.host_type || "desconhecido")}</strong></div><div><span>Última coleta</span><strong>${escapeHtml(formatDate(host.last_seen_at))}</strong></div>${mapping ? `<div><span>Site Checkmk</span><strong>${escapeHtml(mapping.site_name || "—")}</strong></div><div><span>Container</span><strong>${escapeHtml(mapping.container_name || "—")}</strong></div>` : ""}</div><div class="card-actions"><button class="ghost-button" data-investigate-host="${escapeHtml(host.vpn_ip)}" data-investigate-port="${escapeHtml(host.ssh_port)}" data-investigate-env="${escapeHtml(host.environment)}">Investigar alvo</button></div></article>`;
}

async function loadInventory() {
  const params = new URLSearchParams();
  const q = $("#inventory-search").value.trim();
  const environment = $("#inventory-environment").value;
  if (q) params.set("q", q);
  if (environment) params.set("environment", environment);
  $("#inventory-grid").innerHTML = '<div class="empty-state">Carregando inventário...</div>';
  try {
    const data = await api(`/ui/api/hosts?${params}`);
    $("#inventory-grid").innerHTML = data.items.length ? data.items.map(inventoryCard).join("") : '<div class="empty-state">Nenhum alvo aprendido ainda.</div>';
    $$('[data-investigate-host]').forEach((button) => button.addEventListener("click", () => {
      $("#target").value = button.dataset.investigateHost;
      $("#ssh-port").value = button.dataset.investigatePort;
      $("#environment").value = button.dataset.investigateEnv || "unknown";
      updateCorrectionMode();
      showView("analysis");
      $("#objective").focus();
    }));
    state.inventoryLoaded = true;
  } catch (error) { toast(error.message, "error"); }
}

function playbookCard(playbook) {
  const corrections = playbook.allowed_corrections || [];
  return `<article class="playbook-card"><div class="card-top"><div><h4>${escapeHtml(playbook.title)}</h4><p>${escapeHtml(playbook.id)}</p></div><span class="mode-badge">P${escapeHtml(playbook.priority)}</span></div><div class="card-meta"><div><span>Perfis</span><strong>${escapeHtml((playbook.profiles || []).join(", "))}</strong></div><div><span>Etapas de coleta</span><strong>${escapeHtml(playbook.steps_count)}</strong></div><div><span>Pós-validações</span><strong>${escapeHtml(playbook.validation_count)}</strong></div><div><span>Porta SSH</span><strong>${escapeHtml(playbook.ssh_port || "Automática")}</strong></div></div><div class="correction-tags">${corrections.length ? corrections.map((item) => `<span>${escapeHtml(item)}</span>`).join("") : "<span>somente leitura</span>"}</div></article>`;
}

async function loadPlaybooks() {
  $("#playbook-grid").innerHTML = '<div class="empty-state">Carregando playbooks...</div>';
  try {
    if (!state.playbooks.length) await loadPlaybookOptions();
    $("#playbook-grid").innerHTML = state.playbooks.length ? state.playbooks.map(playbookCard).join("") : '<div class="empty-state">Nenhum playbook encontrado no diretório configurado.</div>';
    state.playbooksLoaded = true;
  } catch (error) { toast(error.message, "error"); }
}

function healthCard(title, item, extra = "") {
  const stateName = item?.state || "unknown";
  return `<article class="health-card" data-state="${escapeHtml(stateName)}"><div class="health-card-header"><h4>${escapeHtml(title)}</h4><span>${escapeHtml(providerStateLabel(stateName))}</span></div><p>${escapeHtml(item?.detail || "Sem informação adicional.")}</p>${extra}</article>`;
}

async function loadHealth() {
  $("#health-summary").innerHTML = '<div class="empty-state">Executando diagnóstico seguro...</div>';
  $("#health-grid").innerHTML = "";
  $("#provider-health-list").innerHTML = "";
  try {
    const data = await api("/ui/api/health");
    $("#health-summary").innerHTML = `<div class="health-overall" data-state="${escapeHtml(data.status)}"><div><p class="eyebrow">ESTADO GERAL</p><h3>${escapeHtml(labelStatus(data.status))}</h3></div><div class="health-build"><span>Versão ${escapeHtml(data.version)}</span><strong>${escapeHtml(data.git?.branch)} · ${escapeHtml(data.git?.commit)}</strong></div></div>`;
    const queueExtra = `<div class="health-meta"><span>Modo: ${escapeHtml(data.queue?.execution_mode)}</span><span>Fila: ${escapeHtml(data.queue?.depth ?? "—")}</span></div>`;
    const playbookExtra = `<div class="health-meta"><span>${escapeHtml(data.playbooks?.count)} playbooks</span><span title="${escapeHtml(data.playbooks?.directory)}">diretório configurado</span></div>`;
    $("#health-grid").innerHTML = [
      healthCard("PostgreSQL", data.database),
      healthCard("Redis e fila", data.queue, queueExtra),
      healthCard("Worker", data.worker),
      healthCard("Playbooks", data.playbooks, playbookExtra),
    ].join("");
    $("#provider-health-list").innerHTML = `<div class="panel-subheader"><div><p class="eyebrow">PROVEDORES</p><h3>Validação de IA</h3></div></div><div class="provider-health-grid">${(data.providers || []).map((item) => healthCard(item.label, item, `<div class="health-meta"><span>${escapeHtml(item.model || "sem modelo")}</span><span>${item.latency_ms != null ? `${escapeHtml(item.latency_ms)} ms` : "—"}</span></div>`)).join("")}</div>`;
    state.healthLoaded = true;
  } catch (error) {
    $("#health-summary").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    toast(error.message, "error");
  }
}

function setSubmitting(active) {
  const button = $("#submit-analysis");
  button.disabled = active;
  button.classList.toggle("loading", active);
}

function showExecutionStart(payload) {
  const provider = state.providers.find((item) => item.provider === payload.provider);
  $("#result-drawer").classList.add("open");
  $("#result-drawer").setAttribute("aria-hidden", "false");
  $("#result-title").textContent = "Iniciando investigação";
  $("#result-content").innerHTML = `<div class="execution-timeline"><div class="timeline-item completed"><span></span><div><strong>Provedor validado</strong><p>${escapeHtml(provider?.label || payload.provider)} · ${escapeHtml(payload.model || provider?.model || "modelo padrão")}</p></div></div><div class="timeline-item active"><span></span><div><strong>Preparando o alvo</strong><p>Resolvendo ambiente, SSH e playbook sem executar correções.</p></div></div><div class="timeline-item"><span></span><div><strong>Coleta e análise</strong><p>As evidências aparecerão no resultado persistido.</p></div></div></div>`;
}

async function pollJob(jobId) {
  $("#result-title").textContent = "Análise em processamento";
  while (true) {
    const job = await api(`/ui/api/jobs/${encodeURIComponent(jobId)}`);
    if (job.status === "completed") return job.result;
    if (job.status === "failed") throw new Error(job.error || "A execução na fila falhou.");
    $("#result-content").innerHTML = `<div class="execution-timeline"><div class="timeline-item completed"><span></span><div><strong>Job recebido</strong><p>${escapeHtml(job.job_id)}</p></div></div><div class="timeline-item active"><span></span><div><strong>${job.status === "running" ? "Investigação em execução" : "Aguardando worker"}</strong><p>${escapeHtml(job.worker || "fila operacional")} · ${escapeHtml(job.provider || "provedor padrão")}${job.model ? ` · ${escapeHtml(job.model)}` : ""}</p></div></div><div class="timeline-item"><span></span><div><strong>Conclusão e persistência</strong><p>O resultado será carregado automaticamente.</p></div></div></div>`;
    await new Promise((resolve) => setTimeout(resolve, 2200));
  }
}

async function submitAnalysis(event) {
  event.preventDefault();
  const selectedProvider = state.providers.find((item) => item.provider === $("#provider").value);
  if (!selectedProvider?.selectable) {
    toast("Selecione um provedor disponível antes de iniciar.", "error");
    return;
  }
  if ($("#playbook-mode").value === "manual" && !$("#playbook-id").value) {
    toast("Selecione o playbook manual.", "error");
    return;
  }
  setSubmitting(true);
  const payload = {
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
  showExecutionStart(payload);
  try {
    let result = await api("/ui/api/investigations", { method: "POST", body: payload });
    if (result.job_id) result = await pollJob(result.job_id);
    showResult(result);
    state.dashboardLoaded = false;
    state.investigationsLoaded = false;
    state.inventoryLoaded = false;
    toast("Investigação concluída e registrada no histórico.");
  } catch (error) {
    toast(error.message, "error");
    $("#result-content").innerHTML = `<div class="result-section error-section"><h3>Investigação não iniciada</h3><p>${escapeHtml(error.message)}</p></div>`;
  } finally { setSubmitting(false); }
}

function resultEnvironment(result) {
  const classification = result.environment_classification || {};
  return classification.environment || result.environment || "unknown";
}

function proposedActions(analysis, result) {
  return analysis.proposed_actions || result.corrections || [];
}

function resultProvider(result) {
  if (result.selected_provider) return { provider: result.selected_provider, model: result.selected_model };
  const diagnostics = result.ai_diagnostics || result.analysis?.ai_diagnostics || result.diagnostics || [];
  for (let index = diagnostics.length - 1; index >= 0; index -= 1) {
    const item = diagnostics[index] || {};
    if (item.provider || item.model) return { provider: item.provider, model: item.model };
    const attempts = item.attempts || [];
    const attempt = attempts.findLast ? attempts.findLast((entry) => entry.status === "success") : [...attempts].reverse().find((entry) => entry.status === "success");
    if (attempt) return { provider: attempt.provider, model: attempt.model };
  }
  return {};
}

function showResult(result) {
  const analysis = result.analysis || {};
  const actions = proposedActions(analysis, result);
  const ai = resultProvider(result);
  state.approvalToken = result.approval_token || null;
  state.currentInvestigationId = result.investigation_id || result.id || null;
  const environment = resultEnvironment(result);
  const facts = analysis.facts || [];
  const recommendations = analysis.recommendations || [];
  const review = result.review || analysis.review || {};

  $("#result-title").textContent = result.hostname || result.target || "Resultado da investigação";
  $("#result-content").innerHTML = `<div class="result-summary">${statusBadge(analysis.status || result.status)}${environmentBadge(environment)}<span class="mode-badge">Confiança ${escapeHtml(analysis.confidence ?? result.confidence ?? 0)}%</span>${providerBadge(ai.provider, ai.model)}${result.playbook ? `<span class="mode-badge">${escapeHtml(result.playbook.title || result.playbook.id)}</span>` : ""}</div>
    <section class="result-section"><h3>Resumo</h3><p>${escapeHtml(analysis.summary || "A análise foi concluída sem resumo textual.")}</p></section>
    <section class="result-section"><h3>Causa provável</h3><p>${escapeHtml(analysis.probable_cause || "Não foi possível confirmar uma causa provável.")}</p></section>
    <section class="result-section"><h3>Conclusão</h3><p>${escapeHtml(analysis.conclusion || "Sem conclusão adicional.")}</p></section>
    ${facts.length ? `<section class="result-section"><h3>Fatos confirmados</h3><ul>${facts.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>` : ""}
    ${recommendations.length ? `<section class="result-section"><h3>Recomendações</h3><ul>${recommendations.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>` : ""}
    ${actions.length ? `<section class="result-section"><h3>Ações propostas</h3>${actions.map((item) => `<div class="action-item"><strong>${escapeHtml(item.description || item.tool || "Ação proposta")}</strong><small>${escapeHtml(item.evidence_reason || item.reason || item.status || "Aguardando avaliação")}</small></div>`).join("")}</section>` : ""}
    ${review && Object.keys(review).length ? `<section class="result-section"><h3>Revisão da segunda IA</h3><p>${escapeHtml(review.reason || review.summary || (review.approved ? "Proposta aprovada pela IA revisora." : "A proposta não foi aprovada pela IA revisora."))}</p></section>` : ""}
    <section class="result-section"><h3>Texto para ticket</h3><p id="ticket-report">${escapeHtml(analysis.ticket_report || "Relatório de ticket não gerado.")}</p></section>
    ${state.approvalToken ? `<div class="approval-box"><h3>Aprovação humana disponível</h3><p>Revise as ações antes de autorizar. Ambiente, política, segunda IA e pós-validação continuam obrigatórios.</p><button class="primary-button" id="approve-actions">Aprovar ações seguras</button></div>` : ""}
    <div class="result-actions"><button class="secondary-button" id="copy-ticket">Copiar texto do ticket</button>${state.currentInvestigationId ? '<button class="ghost-button" id="open-full-detail">Ver evidências completas</button>' : ""}</div>
    <details class="raw-details"><summary>Ver retorno técnico</summary><pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre></details>`;
  $("#result-drawer").classList.add("open");
  $("#result-drawer").setAttribute("aria-hidden", "false");
  $("#copy-ticket")?.addEventListener("click", copyTicket);
  $("#approve-actions")?.addEventListener("click", approveActions);
  $("#open-full-detail")?.addEventListener("click", () => openInvestigation(state.currentInvestigationId));
}

async function copyTicket() {
  const text = $("#ticket-report")?.textContent || "";
  try { await navigator.clipboard.writeText(text); toast("Texto do ticket copiado."); }
  catch { toast("Não foi possível copiar automaticamente.", "error"); }
}

async function approveActions() {
  if (!state.approvalToken || !state.currentInvestigationId) return;
  const confirmed = window.confirm("Você revisou as ações propostas e deseja autorizar somente as ações permitidas pelas políticas do Agent IA?");
  if (!confirmed) return;
  const button = $("#approve-actions");
  button.disabled = true;
  button.textContent = "Executando e validando...";
  try {
    const result = await api(`/ui/api/investigations/${encodeURIComponent(state.currentInvestigationId)}/approve`, { method: "POST", body: { token: state.approvalToken } });
    state.approvalToken = null;
    toast("Execução aprovada e pós-validada.");
    button.closest(".approval-box").innerHTML = `<h3>Execução processada</h3><p>${escapeHtml(result.status || "A solicitação foi concluída.")}</p><details class="raw-details"><summary>Ver resultado</summary><pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre></details>`;
  } catch (error) {
    button.disabled = false;
    button.textContent = "Aprovar ações seguras";
    toast(error.message, "error");
  }
}

async function openInvestigation(id) {
  if (!id) return;
  $("#result-drawer").classList.add("open");
  $("#result-drawer").setAttribute("aria-hidden", "false");
  $("#result-title").textContent = "Carregando investigação";
  $("#result-content").innerHTML = '<div class="result-section"><p>Consultando dados persistidos...</p></div>';
  try { showResult(await api(`/ui/api/investigations/${encodeURIComponent(id)}`)); }
  catch (error) { toast(error.message, "error"); }
}

function closeDrawer() {
  $("#result-drawer").classList.remove("open");
  $("#result-drawer").setAttribute("aria-hidden", "true");
}

function resetAnalysisForm() {
  $("#analysis-form").reset();
  const defaultProvider = state.providers.find((item) => item.provider === state.session?.default_provider && item.selectable) || state.providers.find((item) => item.selectable);
  if (defaultProvider) $("#provider").value = defaultProvider.provider;
  updateProviderSelection();
  updatePlaybookMode();
  updateCorrectionMode();
}

function bindEvents() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
  $$('[data-view-link]').forEach((button) => button.addEventListener("click", () => showView(button.dataset.viewLink)));
  $$('[data-open-analysis]').forEach((button) => button.addEventListener("click", () => showView("analysis")));
  $$('[data-close-drawer]').forEach((button) => button.addEventListener("click", closeDrawer));
  $$(".preset").forEach((button) => button.addEventListener("click", () => { $("#objective").value = button.dataset.preset; $("#objective").focus(); }));
  $("#analysis-form").addEventListener("submit", submitAnalysis);
  $("#clear-form").addEventListener("click", resetAnalysisForm);
  $("#provider").addEventListener("change", updateProviderSelection);
  $("#playbook-mode").addEventListener("change", updatePlaybookMode);
  $("#environment").addEventListener("change", updateCorrectionMode);
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#refresh-health")?.addEventListener("click", () => { state.healthLoaded = false; loadHealth(); });
  $("#filter-investigations").addEventListener("click", loadInvestigations);
  $("#filter-inventory").addEventListener("click", loadInventory);
  $("#investigation-search").addEventListener("keydown", (event) => { if (event.key === "Enter") loadInvestigations(); });
  $("#inventory-search").addEventListener("keydown", (event) => { if (event.key === "Enter") loadInventory(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDrawer(); });
}

async function boot() {
  setupTheme();
  bindEvents();
  updateCorrectionMode();
  try {
    await loadSession();
    await loadDashboard();
  } catch (error) {
    toast(error.message, "error");
    $("#operator-name").textContent = "Acesso indisponível";
  }
}

document.addEventListener("DOMContentLoaded", boot);
