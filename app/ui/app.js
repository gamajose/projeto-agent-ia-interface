const state = {
  session: null,
  dashboardLoaded: false,
  investigationsLoaded: false,
  inventoryLoaded: false,
  playbooksLoaded: false,
  approvalToken: null,
  currentInvestigationId: null,
};

const viewMeta = {
  dashboard: ["CENTRAL OPERACIONAL", "Visão geral"],
  analysis: ["INVESTIGAÇÃO GUIADA", "Nova análise"],
  investigations: ["MEMÓRIA OPERACIONAL", "Investigações"],
  inventory: ["ALVOS CONHECIDOS", "Inventário"],
  playbooks: ["AUTOMAÇÃO CONTROLADA", "Playbooks"],
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
  if ((init.method || "GET").toUpperCase() !== "GET") {
    init.headers["X-Agent-UI"] = "1";
  }
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
  toast.timer = setTimeout(() => { element.className = "toast"; }, 3600);
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

function statusBadge(status) {
  const safeStatus = ["healthy", "attention", "critical", "inconclusive"].includes(status) ? status : "inconclusive";
  return `<span class="status-badge ${safeStatus}">${escapeHtml(labelStatus(status))}</span>`;
}

function environmentBadge(environment) {
  return `<span class="environment-badge">${escapeHtml(labelEnvironment(environment))}</span>`;
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
}

async function loadSession() {
  state.session = await api("/ui/api/session");
  $("#operator-name").textContent = state.session.operator;
  $("#operator-avatar").textContent = initials(state.session.operator);
  $("#execution-mode-badge").textContent = `modo ${state.session.execution_mode}`;
  $("#safety-list").innerHTML = state.session.safe_rules.map((rule) => `<li>${escapeHtml(rule)}</li>`).join("");
}

function renderMetrics(metrics) {
  const byStatus = metrics.by_status || {};
  const byMode = metrics.by_mode || {};
  const approval = metrics.approval_executions || {};
  const cards = [
    ["Investigações", metrics.investigations_total || 0, "total persistido no PostgreSQL"],
    ["Duração média", formatDuration(metrics.average_duration_ms), "tempo médio de análise"],
    ["Casos críticos", byStatus.critical || 0, `${byStatus.attention || 0} casos em atenção`],
    ["Aprovações executadas", approval.completed || approval.success || 0, `${byMode.propose || 0} análises no modo propor`],
  ];
  $("#metrics-grid").innerHTML = cards.map(([label, value, detail]) => `
    <article class="metric-card">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(value)}</div>
      <div class="metric-detail">${escapeHtml(detail)}</div>
    </article>`).join("");
}

function investigationRow(item, columns = "recent") {
  const hostname = item.hostname || item.target;
  const targetLine = item.hostname && item.target !== item.hostname ? item.target : "";
  if (columns === "recent") {
    return `<tr class="clickable" data-investigation-id="${escapeHtml(item.id)}">
      <td><strong>${escapeHtml(hostname)}</strong><small>${escapeHtml(targetLine)}</small></td>
      <td>${environmentBadge(item.environment)}</td>
      <td>${statusBadge(item.status)}</td>
      <td>${escapeHtml(item.confidence ?? 0)}%</td>
      <td>${escapeHtml(formatDate(item.created_at))}</td>
    </tr>`;
  }
  return `<tr class="clickable" data-investigation-id="${escapeHtml(item.id)}">
    <td><strong>${escapeHtml(hostname)}</strong><small>${escapeHtml(targetLine)}</small></td>
    <td title="${escapeHtml(item.objective)}">${escapeHtml(String(item.objective || "").slice(0, 82))}${String(item.objective || "").length > 82 ? "…" : ""}</td>
    <td>${escapeHtml(item.playbook?.title || "Automático")}</td>
    <td>${environmentBadge(item.environment)}</td>
    <td>${statusBadge(item.status)}</td>
    <td>${escapeHtml(item.confidence ?? 0)}%</td>
    <td>${escapeHtml(formatDate(item.created_at))}</td>
  </tr>`;
}

function bindInvestigationRows(root = document) {
  $$('[data-investigation-id]', root).forEach((row) => {
    row.addEventListener("click", () => openInvestigation(row.dataset.investigationId));
  });
}

async function loadDashboard() {
  try {
    const data = await api("/ui/api/dashboard");
    renderMetrics(data.metrics || {});
    const items = data.recent?.items || [];
    $("#recent-investigations").innerHTML = items.length
      ? items.map((item) => investigationRow(item, "recent")).join("")
      : '<tr><td colspan="5" class="empty-cell">Nenhuma investigação registrada ainda.</td></tr>';
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
    $("#investigations-table").innerHTML = data.items.length
      ? data.items.map((item) => investigationRow(item, "full")).join("")
      : '<tr><td colspan="7" class="empty-cell">Nenhum registro encontrado.</td></tr>';
    $("#investigation-total").textContent = `${data.total} registro${data.total === 1 ? "" : "s"}`;
    bindInvestigationRows($("#investigations-table"));
    state.investigationsLoaded = true;
  } catch (error) {
    toast(error.message, "error");
  }
}

function hostCard(host) {
  const mapping = host.mapping || {};
  const subtitle = host.os_name || host.host_type || "Sistema não identificado";
  return `<article class="inventory-card">
    <div class="card-top">
      <div><h4>${escapeHtml(host.hostname || host.vpn_ip)}</h4><p>${escapeHtml(subtitle)}</p></div>
      ${environmentBadge(host.environment)}
    </div>
    <div class="card-meta">
      <div><span>IP VPN</span><strong>${escapeHtml(host.vpn_ip)}:${escapeHtml(host.ssh_port)}</strong></div>
      <div><span>Site Checkmk</span><strong>${escapeHtml(mapping.site_name || "—")}</strong></div>
      <div><span>Container</span><strong>${escapeHtml(mapping.container_name || "—")}</strong></div>
      <div><span>Última coleta</span><strong>${escapeHtml(formatDate(host.last_seen_at))}</strong></div>
    </div>
    <div class="card-actions"><button class="ghost-button" data-analyze-target="${escapeHtml(host.vpn_ip)}" data-analyze-environment="${escapeHtml(host.environment)}">Analisar alvo</button></div>
  </article>`;
}

async function loadInventory() {
  const params = new URLSearchParams({ limit: "200" });
  const q = $("#inventory-search").value.trim();
  const environment = $("#inventory-environment").value;
  if (q) params.set("q", q);
  if (environment) params.set("environment", environment);
  $("#inventory-grid").innerHTML = '<div class="empty-state">Carregando inventário...</div>';
  try {
    const data = await api(`/ui/api/hosts?${params}`);
    $("#inventory-grid").innerHTML = data.items.length
      ? data.items.map(hostCard).join("")
      : '<div class="empty-state">Nenhum alvo encontrado no inventário.</div>';
    $$('[data-analyze-target]', $("#inventory-grid")).forEach((button) => {
      button.addEventListener("click", () => {
        $("#target").value = button.dataset.analyzeTarget;
        $("#environment").value = button.dataset.analyzeEnvironment || "unknown";
        showView("analysis");
        $("#objective").focus();
      });
    });
    state.inventoryLoaded = true;
  } catch (error) {
    toast(error.message, "error");
  }
}

function playbookCard(playbook) {
  const corrections = playbook.allowed_corrections || [];
  return `<article class="playbook-card">
    <div class="card-top">
      <div><h4>${escapeHtml(playbook.title)}</h4><p>${escapeHtml(playbook.id)}</p></div>
      <span class="mode-badge">P${escapeHtml(playbook.priority)}</span>
    </div>
    <div class="card-meta">
      <div><span>Perfis</span><strong>${escapeHtml((playbook.profiles || []).join(", "))}</strong></div>
      <div><span>Etapas de coleta</span><strong>${escapeHtml(playbook.steps_count)}</strong></div>
      <div><span>Pós-validações</span><strong>${escapeHtml(playbook.validation_count)}</strong></div>
      <div><span>Porta SSH</span><strong>${escapeHtml(playbook.ssh_port || "Automática")}</strong></div>
    </div>
    <div class="correction-tags">${corrections.length ? corrections.map((item) => `<span>${escapeHtml(item)}</span>`).join("") : "<span>somente leitura</span>"}</div>
  </article>`;
}

async function loadPlaybooks() {
  $("#playbook-grid").innerHTML = '<div class="empty-state">Carregando playbooks...</div>';
  try {
    const data = await api("/ui/api/playbooks");
    $("#playbook-grid").innerHTML = data.items.length
      ? data.items.map(playbookCard).join("")
      : '<div class="empty-state">Nenhum playbook encontrado no diretório configurado.</div>';
    state.playbooksLoaded = true;
  } catch (error) {
    toast(error.message, "error");
  }
}

function setSubmitting(active) {
  const button = $("#submit-analysis");
  button.disabled = active;
  button.classList.toggle("loading", active);
}

async function pollJob(jobId) {
  const drawer = $("#result-drawer");
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  $("#result-title").textContent = "Análise em processamento";
  $("#result-content").innerHTML = '<div class="result-section"><h3>Fila operacional</h3><p>O job foi recebido e será atualizado automaticamente nesta tela.</p></div>';

  while (true) {
    const job = await api(`/ui/api/jobs/${encodeURIComponent(jobId)}`);
    if (job.status === "completed") return job.result;
    if (job.status === "failed") throw new Error(job.error || "A execução na fila falhou.");
    $("#result-content").innerHTML = `<div class="result-section"><h3>Status do job</h3><p>${escapeHtml(job.status || "aguardando")} ${job.worker ? `— ${escapeHtml(job.worker)}` : ""}</p></div>`;
    await new Promise((resolve) => setTimeout(resolve, 2200));
  }
}

async function submitAnalysis(event) {
  event.preventDefault();
  setSubmitting(true);
  const payload = {
    target: $("#target").value.trim(),
    objective: $("#objective").value.trim(),
    environment: $("#environment").value,
    mode: $("#mode").value,
    ssh_port: $("#ssh-port").value ? Number($("#ssh-port").value) : null,
  };
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
  } finally {
    setSubmitting(false);
  }
}

function resultEnvironment(result) {
  const classification = result.environment_classification || {};
  return classification.environment || result.environment || "unknown";
}

function proposedActions(analysis, result) {
  return analysis.proposed_actions || result.corrections || [];
}

function showResult(result) {
  const analysis = result.analysis || {};
  const actions = proposedActions(analysis, result);
  state.approvalToken = result.approval_token || null;
  state.currentInvestigationId = result.investigation_id || result.id || null;
  const environment = resultEnvironment(result);
  const facts = analysis.facts || [];
  const recommendations = analysis.recommendations || [];
  const review = result.review || analysis.review || {};

  $("#result-title").textContent = result.hostname || result.target || "Resultado da investigação";
  $("#result-content").innerHTML = `
    <div class="result-summary">
      ${statusBadge(analysis.status || result.status)}
      ${environmentBadge(environment)}
      <span class="mode-badge">Confiança ${escapeHtml(analysis.confidence ?? result.confidence ?? 0)}%</span>
      ${result.playbook ? `<span class="mode-badge">${escapeHtml(result.playbook.title || result.playbook.id)}</span>` : ""}
    </div>
    <section class="result-section"><h3>Resumo</h3><p>${escapeHtml(analysis.summary || "A análise foi concluída sem resumo textual.")}</p></section>
    <section class="result-section"><h3>Causa provável</h3><p>${escapeHtml(analysis.probable_cause || "Não foi possível confirmar uma causa provável.")}</p></section>
    <section class="result-section"><h3>Conclusão</h3><p>${escapeHtml(analysis.conclusion || "Sem conclusão adicional.")}</p></section>
    ${facts.length ? `<section class="result-section"><h3>Fatos confirmados</h3><ul>${facts.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>` : ""}
    ${recommendations.length ? `<section class="result-section"><h3>Recomendações</h3><ul>${recommendations.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>` : ""}
    ${actions.length ? `<section class="result-section"><h3>Ações propostas</h3>${actions.map((item) => `<div class="action-item"><strong>${escapeHtml(item.description || item.tool || "Ação proposta")}</strong><small>${escapeHtml(item.evidence_reason || item.reason || item.status || "Aguardando avaliação")}</small></div>`).join("")}</section>` : ""}
    ${review && Object.keys(review).length ? `<section class="result-section"><h3>Revisão da segunda IA</h3><p>${escapeHtml(review.reason || review.summary || (review.approved ? "Proposta aprovada pela IA revisora." : "A proposta não foi aprovada pela IA revisora."))}</p></section>` : ""}
    <section class="result-section"><h3>Texto para ticket</h3><p id="ticket-report">${escapeHtml(analysis.ticket_report || "Relatório de ticket não gerado.")}</p></section>
    ${state.approvalToken ? `<div class="approval-box"><h3>Aprovação humana disponível</h3><p>O token temporário está apenas nesta sessão. Revise as ações acima antes de autorizar. As políticas do ambiente e as pós-validações continuam obrigatórias.</p><button class="primary-button" id="approve-actions">Aprovar ações seguras</button></div>` : ""}
    <div class="result-actions">
      <button class="secondary-button" id="copy-ticket">Copiar texto do ticket</button>
      ${state.currentInvestigationId ? `<button class="ghost-button" id="open-full-detail">Ver evidências completas</button>` : ""}
    </div>
    <details class="raw-details"><summary>Ver retorno técnico</summary><pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre></details>
  `;
  $("#result-drawer").classList.add("open");
  $("#result-drawer").setAttribute("aria-hidden", "false");
  $("#copy-ticket")?.addEventListener("click", copyTicket);
  $("#approve-actions")?.addEventListener("click", approveActions);
  $("#open-full-detail")?.addEventListener("click", () => openInvestigation(state.currentInvestigationId));
}

async function copyTicket() {
  const text = $("#ticket-report")?.textContent || "";
  try {
    await navigator.clipboard.writeText(text);
    toast("Texto do ticket copiado.");
  } catch {
    toast("Não foi possível copiar automaticamente.", "error");
  }
}

async function approveActions() {
  if (!state.approvalToken || !state.currentInvestigationId) return;
  const confirmed = window.confirm("Você revisou as ações propostas e deseja autorizar somente as ações permitidas pelas políticas do Agent IA?");
  if (!confirmed) return;
  const button = $("#approve-actions");
  button.disabled = true;
  button.textContent = "Executando e validando...";
  try {
    const result = await api(`/ui/api/investigations/${encodeURIComponent(state.currentInvestigationId)}/approve`, {
      method: "POST",
      body: { token: state.approvalToken },
    });
    state.approvalToken = null;
    toast("Execução aprovada e pós-validada.");
    const box = button.closest(".approval-box");
    box.innerHTML = `<h3>Execução processada</h3><p>${escapeHtml(result.status || "A solicitação foi concluída.")}</p><details class="raw-details"><summary>Ver resultado</summary><pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre></details>`;
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
  try {
    const result = await api(`/ui/api/investigations/${encodeURIComponent(id)}`);
    showResult(result);
  } catch (error) {
    toast(error.message, "error");
  }
}

function closeDrawer() {
  $("#result-drawer").classList.remove("open");
  $("#result-drawer").setAttribute("aria-hidden", "true");
}

function bindEvents() {
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
  $$('[data-view-link]').forEach((button) => button.addEventListener("click", () => showView(button.dataset.viewLink)));
  $$('[data-open-analysis]').forEach((button) => button.addEventListener("click", () => showView("analysis")));
  $$('[data-close-drawer]').forEach((button) => button.addEventListener("click", closeDrawer));
  $$(".preset").forEach((button) => button.addEventListener("click", () => {
    $("#objective").value = button.dataset.preset;
    $("#objective").focus();
  }));
  $("#analysis-form").addEventListener("submit", submitAnalysis);
  $("#clear-form").addEventListener("click", () => $("#analysis-form").reset());
  $("#filter-investigations").addEventListener("click", loadInvestigations);
  $("#filter-inventory").addEventListener("click", loadInventory);
  $("#investigation-search").addEventListener("keydown", (event) => { if (event.key === "Enter") loadInvestigations(); });
  $("#inventory-search").addEventListener("keydown", (event) => { if (event.key === "Enter") loadInventory(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDrawer(); });
}

async function boot() {
  bindEvents();
  try {
    await loadSession();
    await loadDashboard();
  } catch (error) {
    toast(error.message, "error");
    $("#operator-name").textContent = "Acesso indisponível";
  }
}

document.addEventListener("DOMContentLoaded", boot);
