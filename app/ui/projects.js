(() => {
  viewMeta.projects = ["VALIDAÇÃO DE PROJETO", "Projetos"];

  const projectState = {
    templates: null,
    plan: null,
    results: new Map(),
    draftKey: "agent-ui-project-validation-draft-v4",
  };

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => [...root.querySelectorAll(selector)];
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const asArray = (value) => (Array.isArray(value) ? value : []);

  function normalizeExecutionResponse(value) {
    const response = value && typeof value === "object" ? value : {};
    return {
      ...response,
      jobs: asArray(response.jobs),
      executions: asArray(response.executions),
      errors: asArray(response.errors),
    };
  }

  function value(id) {
    return String(q(`#${id}`)?.value || "").trim();
  }

  function checked(id) {
    return Boolean(q(`#${id}`)?.checked);
  }

  function normalizeRole(raw) {
    const normalized = String(raw || "").trim().toLowerCase();
    if (["production", "producao", "produção", "prod"].includes(normalized)) return "production";
    if (["standby", "std"].includes(normalized)) return "standby";
    return "server";
  }

  function parseRelatedHosts(raw) {
    return String(raw || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => {
        const parts = line.split("|").map((item) => item.trim()).filter(Boolean);
        if (parts.length === 1) return { role: "server", vpn_ip: parts[0] };
        if (parts.length === 2) return { role: normalizeRole(parts[0]), vpn_ip: parts[1] };
        if (parts.length >= 3) return { role: normalizeRole(parts[1] || parts[0]), vpn_ip: parts[parts.length - 1] };
        throw new Error(`Linha ${index + 1} dos hosts relacionados é inválida.`);
      });
  }

  function payload() {
    return {
      scenario: value("project-scenario"),
      role: value("project-role") || "production",
      target_vpn_ip: value("project-target-vpn"),
      install_agent: checked("project-install-agent"),
      has_monitoring_server: checked("project-has-monitor"),
      monitoring_vpn_ip: value("project-monitor-vpn") || null,
      related_hosts: parseRelatedHosts(value("project-related-hosts")),
      gateway_dns: value("project-gateway-dns") || null,
      vpn_dns_name: value("project-vpn-dns-name") || "vpn.oracledba.com.br",
      provider: "auto",
      model: null,
    };
  }

  function setHidden(selector, hidden) {
    const element = q(selector);
    if (element) element.hidden = hidden;
  }

  function refreshScenario() {
    const scenario = value("project-scenario");
    const isMonitoring = scenario === "linux_monitoring";
    const isDns = scenario === "dns_vpn";
    const canUseSharedMonitor = ["linux_prod_std", "management_interface", "windows"].includes(scenario);

    setHidden("#project-role-field", scenario !== "linux_prod_std");
    setHidden("#project-agent-field", !["linux_prod_std", "linux_monitoring"].includes(scenario));
    setHidden("#project-monitor-toggle-field", !canUseSharedMonitor);
    setHidden("#project-monitor-fields", !canUseSharedMonitor || !checked("project-has-monitor"));
    setHidden("#project-related-fields", !isMonitoring);
    setHidden("#project-dns-fields", !isDns);

    if (isMonitoring) {
      q("#project-role").value = "monitoring";
      q("#project-has-monitor").checked = false;
    } else if (!canUseSharedMonitor) {
      q("#project-has-monitor").checked = false;
    }

    const descriptions = {
      linux_prod_std: "Informe o IP VPN/TAP. A automação executa somente as validações da macro de Produção/Standby e devolve as evidências para o ticket.",
      linux_monitoring: "Informe o IP VPN/TAP do monitor. A automação executa somente a macro de servidor de monitoramento, incluindo os testes de comunicação aplicáveis.",
      management_interface: "Informe o IP VPN/TAP do servidor físico. A automação coleta hardware, SO e interface de gerenciamento conforme a macro.",
      firewall: "Informe o IP VPN/TAP. A automação executa somente as validações previstas na macro de firewall.",
      windows: "Informe o IP VPN/TAP. A tela registra as validações possíveis e deixa como manual apenas o que realmente depende de RDP/Socat.",
      dns_vpn: "Informe o IP VPN/TAP. A automação executa somente as validações do playbook de DNS/VPN; não inicia troubleshooting geral.",
    };
    q("#project-scenario-help").textContent = descriptions[scenario] || "Informe o IP VPN/TAP e execute a macro do projeto.";
    saveDraft();
  }

  function saveDraft() {
    try {
      const fields = {};
      qa("#project-form input, #project-form select, #project-form textarea").forEach((element) => {
        if (!element.id) return;
        fields[element.id] = element.type === "checkbox" ? element.checked : element.value;
      });
      localStorage.setItem(projectState.draftKey, JSON.stringify(fields));
    } catch (_) {
      // O funcionamento principal não depende do armazenamento local.
    }
  }

  function restoreDraft() {
    try {
      const fields = JSON.parse(localStorage.getItem(projectState.draftKey) || "{}");
      Object.entries(fields).forEach(([id, stored]) => {
        const element = q(`#${id}`);
        if (!element) return;
        if (element.type === "checkbox") element.checked = Boolean(stored);
        else element.value = stored == null ? "" : String(stored);
      });
    } catch (_) {
      // Ignora rascunho incompatível.
    }
  }

  function statusLabel(status) {
    const labels = {
      queued: "Na fila",
      running: "Executando",
      cancelling: "Cancelando",
      completed: "Concluído",
      failed: "Falhou",
      cancelled: "Cancelado",
    };
    return labels[status] || status || "Aguardando";
  }

  function checklistIcon(status) {
    if (status === "completed") return "✅";
    if (status === "failed") return "❌";
    if (status === "manual") return "⭕";
    return "▶️";
  }

  function checklistStatus(status) {
    if (status === "completed") return "Concluído";
    if (status === "failed") return "Falhou";
    if (status === "manual") return "Manual";
    return "Pendente";
  }

  function factsMarkup(facts) {
    if (!facts || typeof facts !== "object") return "";
    const rows = [
      ["IP VPN", facts.vpn_ip],
      ["Sistema operacional", facts.os_name],
      ["IP interno", facts.internal_ip],
      ["Máquina", facts.machine_type && facts.virtualization && facts.virtualization !== "unknown" ? `${facts.machine_type} · ${facts.virtualization}` : facts.machine_type],
      ["Hardware", [facts.manufacturer, facts.model].filter(Boolean).join(" ")],
      ["Gerenciamento", facts.management_ip ? `${facts.management_label || facts.management_type} · ${facts.management_ip}` : facts.management_label || facts.management_type],
    ].filter(([, answer]) => answer && !String(answer).includes("unknown") && String(answer) !== "desconhecida");
    if (!rows.length) return "";
    return `<section class="project-discovery"><div class="project-discovery-title"><p class="eyebrow">DADOS IDENTIFICADOS</p><h3>Informações coletadas durante a macro</h3><p>Esses valores vieram das próprias validações executadas no servidor.</p></div><article class="project-discovery-card" data-tone="good"><dl>${rows.map(([label, answer]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(answer)}</dd></div>`).join("")}</dl></article></section>`;
  }

  function evidenceMarkup(item) {
    const evidence = item?.evidence || null;
    const output = evidence ? String(evidence.stdout || evidence.stderr || "").trim() : "";
    const notes = asArray(item?.notes);
    const hint = String(item?.evidence_hint || "").trim();
    if (!output && !hint && !notes.length) return "";
    return `<details class="project-evidence-details"><summary>Ver evidência</summary>${output ? `<pre>${escapeHtml(output)}</pre>` : ""}${hint ? `<p><strong>Evidência esperada:</strong> ${escapeHtml(hint)}</p>` : ""}${notes.length ? `<ul>${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>` : ""}</details>`;
  }

  function macroResultMarkup(result) {
    const checklist = asArray(result?.checklist);
    const summary = result?.summary || {};
    const cards = checklist.map((item) => `<article class="project-check-result" data-state="${escapeHtml(item.status || "pending")}">
      <div class="project-check-result-head"><span class="project-check-icon">${checklistIcon(item.status)}</span><div><strong>${escapeHtml(item.title || "Validação")}</strong><span>${escapeHtml(item.context || "Projeto")}</span></div><b>${escapeHtml(checklistStatus(item.status))}</b></div>
      <p>${escapeHtml(item.summary || "")}</p>
      ${evidenceMarkup(item)}
    </article>`).join("");
    return `<div class="project-macro-result">
      ${factsMarkup(result?.facts)}
      <div class="project-macro-result-summary"><span><b>${escapeHtml(summary.completed ?? 0)}</b> concluídas</span><span><b>${escapeHtml(summary.failed ?? 0)}</b> falharam</span><span><b>${escapeHtml(summary.manual ?? 0)}</b> manuais</span></div>
      <section class="project-check-results"><div class="project-jobs-head"><div><p class="eyebrow">RESULTADO DA MACRO</p><h3>Validações do projeto</h3><p>As saídas abaixo são as evidências do que foi executado. Abra o item desejado para tirar o print.</p></div></div><div class="project-check-result-grid">${cards || '<div class="empty-state">Nenhum item retornado pela macro.</div>'}</div></section>
    </div>`;
  }

  function jobCard(meta) {
    const safeMeta = meta && typeof meta === "object" ? meta : {};
    return `<article class="project-job-card" data-project-job="${escapeHtml(safeMeta.job_id || safeMeta.reference || "unknown")}" data-state="${escapeHtml(safeMeta.status || "queued")}">
      <div class="project-job-card-head"><div><span class="pulse-dot"></span><strong>${escapeHtml(safeMeta.label || safeMeta.reference || "Validação")}</strong></div><span class="project-job-status">${escapeHtml(statusLabel(safeMeta.status))}</span></div>
      <p>${escapeHtml(safeMeta.reference || "—")} · ${escapeHtml(safeMeta.environment || "unknown")}</p>
      <div class="project-job-progress"><span style="width:${safeMeta.status === "completed" ? 100 : 4}%"></span></div>
      <small class="project-job-phase">${safeMeta.status === "completed" ? "Macro concluída." : "A automação vai executar somente as validações previstas para este projeto."}</small>
      <div class="project-job-output"></div>
    </article>`;
  }

  function findJobCard(key) {
    const root = q("#project-plan");
    if (!root) return null;
    return qa("[data-project-job]", root).find((element) => element.dataset.projectJob === String(key));
  }

  function updateJobCard(key, job) {
    const card = findJobCard(key);
    if (!card || !job || typeof job !== "object") return;
    const status = job.status || "running";
    card.dataset.state = status;
    const statusElement = q(".project-job-status", card);
    if (statusElement) statusElement.textContent = statusLabel(status);
    const percent = Math.max(0, Math.min(100, Number(job.percent ?? (status === "completed" ? 100 : 8))));
    const progress = q(".project-job-progress span", card);
    if (progress) progress.style.width = `${percent}%`;
    const phase = job.current_phase || {};
    const phaseElement = q(".project-job-phase", card);
    if (phaseElement) phaseElement.textContent = phase.detail || (status === "completed" ? "Macro concluída e evidências organizadas." : statusLabel(status));
    const output = q(".project-job-output", card);
    if (!output) return;

    if (status === "completed" && job.result) {
      projectState.results.set(String(key), job.result);
      output.innerHTML = job.result.kind === "project_validation" ? macroResultMarkup(job.result) : '<p class="project-job-error">O job retornou um resultado que não pertence à validação de projeto.</p>';
    } else if (status === "failed") {
      output.innerHTML = `<p class="project-job-error">${escapeHtml(job.error || "A execução da macro falhou sem detalhe adicional.")}</p>`;
    } else if (status === "cancelled") {
      output.innerHTML = '<p class="project-job-error">Execução cancelada.</p>';
    }
  }

  function renderExecution(rawResponse) {
    const response = normalizeExecutionResponse(rawResponse);
    projectState.plan = response.plan || null;
    projectState.results.clear();
    const root = q("#project-plan");
    if (!root) return response;
    const plan = response.plan || {};
    const checklist = asArray(plan.checklist);
    const automatic = checklist.filter((item) => item.automated).length;
    const manual = checklist.filter((item) => !item.automated).length;
    const warnings = asArray(plan.warnings).length
      ? `<div class="project-warnings">${asArray(plan.warnings).map((warning) => `<p>⚠ ${escapeHtml(warning)}</p>`).join("")}</div>`
      : "";
    const metas = [
      ...response.jobs,
      ...response.executions.map((item) => ({ ...item, job_id: item.reference })),
    ];
    const errors = response.errors.map((item) => `<p class="project-job-error"><strong>${escapeHtml(item.label || item.reference)}</strong>: ${escapeHtml(item.error)}</p>`).join("");

    root.innerHTML = `<div class="project-plan-summary">
      <div><p class="eyebrow">VALIDAÇÃO DE PROJETO</p><h2>${escapeHtml(plan.scenario_label || "Macro do projeto")}</h2><p>Alvo VPN/TAP: ${escapeHtml(plan.target?.vpn_ip || value("project-target-vpn"))}</p></div>
      <div class="project-summary-metrics"><span><b>${escapeHtml(metas.length)}</b> validação(ões)</span><span><b>${escapeHtml(automatic)}</b> automáticas</span><span><b>${escapeHtml(manual)}</b> manuais</span></div>
    </div>
    ${warnings}
    <div class="project-safety-note"><strong>Escopo:</strong> esta área executa somente a macro/checklist do projeto. Não inicia investigação de causa raiz e não gera proposta corretiva.</div>
    <section class="project-jobs"><div class="project-jobs-head"><div><p class="eyebrow">EXECUÇÃO DA MACRO</p><h3>Validações em andamento</h3><p>${escapeHtml(response.message || "Macro iniciada.")}</p></div></div><div class="project-job-grid">${metas.map(jobCard).join("")}</div>${errors}</section>
    ${plan.ticket_macro ? `<section class="project-macro-card"><div><p class="eyebrow">TEXTO DO TICKET</p><h3>Macro de apoio</h3></div><pre id="project-ticket-macro">${escapeHtml(plan.ticket_macro)}</pre></section>` : ""}`;
    root.hidden = false;
    root.scrollIntoView({ behavior: "smooth", block: "start" });

    response.executions.forEach((item) => {
      updateJobCard(item.reference, { status: "completed", percent: 100, result: item.result, current_phase: { detail: "Macro executada diretamente." } });
    });
    return response;
  }

  async function pollJob(meta) {
    const key = meta?.job_id;
    if (!key) return { status: "failed" };
    for (;;) {
      let job;
      try {
        job = await api(`/ui/api/jobs/${encodeURIComponent(key)}`);
        if (!job || typeof job !== "object") throw new Error("Resposta inválida ao consultar a validação.");
      } catch (error) {
        updateJobCard(key, { status: "failed", error: error.message });
        return { status: "failed" };
      }
      updateJobCard(key, job);
      if (["completed", "failed", "cancelled"].includes(job.status)) return job;
      await wait(1400);
    }
  }

  async function pollProjectJobs(rawResponse) {
    const response = normalizeExecutionResponse(rawResponse);
    if (!response.jobs.length) return [];
    const finalJobs = await Promise.all(response.jobs.map(pollJob));
    let completedSteps = 0;
    let failedSteps = 0;
    let manualSteps = 0;
    finalJobs.forEach((job) => {
      if (job?.result?.kind !== "project_validation") return;
      completedSteps += Number(job.result.summary?.completed || 0);
      failedSteps += Number(job.result.summary?.failed || 0);
      manualSteps += Number(job.result.summary?.manual || 0);
    });
    const failedJobs = finalJobs.filter((job) => job?.status === "failed").length;
    const statusElement = q("#project-form-status");
    if (statusElement) {
      statusElement.textContent = failedJobs
        ? `A macro terminou com ${failedJobs} execução(ões) interrompida(s).`
        : `Macro finalizada: ${completedSteps} validação(ões) concluída(s), ${failedSteps} com falha e ${manualSteps} manual(is).`;
    }
    return finalJobs;
  }

  function setPlanning(active) {
    const button = q("#project-generate");
    if (!button) return;
    button.disabled = active;
    button.textContent = active ? "Executando macro..." : "Executar validação com IA";
  }

  async function executeProject(event) {
    event?.preventDefault();
    setPlanning(true);
    const formStatus = q("#project-form-status");
    if (formStatus) formStatus.textContent = "Executando somente a macro do projeto. As saídas serão organizadas como evidências para o ticket.";
    try {
      const rawResponse = await api("/ui/api/projects/start", { method: "POST", body: payload() });
      const response = renderExecution(rawResponse);
      saveDraft();
      if (response.execution_mode === "queue") {
        const count = response.jobs.length;
        toast(`${count} validação(ões) de projeto enviada(s) para execução.`);
        if (!count) throw new Error("A API informou execução em fila, mas não retornou a validação criada.");
        await pollProjectJobs(response);
      } else {
        const count = response.executions.length;
        if (formStatus) formStatus.textContent = `${count} macro(s) executada(s). Abra as evidências para tirar os prints.`;
        toast("Macro do projeto executada. As evidências estão disponíveis abaixo.");
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || "Falha inesperada na validação do projeto.");
      if (formStatus) formStatus.textContent = message;
      toast(message, "error");
    } finally {
      setPlanning(false);
    }
  }

  async function loadTemplates() {
    projectState.templates = await api("/ui/api/projects/templates");
    const defaults = projectState.templates?.defaults || {};
    const scenario = q("#project-scenario");
    if (!scenario) return;
    scenario.innerHTML = asArray(projectState.templates?.scenarios).map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
    const dnsName = q("#project-vpn-dns-name");
    if (dnsName) dnsName.value ||= defaults.vpn_dns_name || "vpn.oracledba.com.br";
    restoreDraft();
    refreshScenario();
  }

  function bind() {
    q("#project-form")?.addEventListener("submit", executeProject);
    q("#project-scenario")?.addEventListener("change", refreshScenario);
    q("#project-has-monitor")?.addEventListener("change", refreshScenario);
    qa("#project-form input, #project-form select, #project-form textarea").forEach((element) => {
      element.addEventListener("change", saveDraft);
      if (element.tagName === "TEXTAREA" || element.type === "text") element.addEventListener("input", saveDraft);
    });
    q("#project-clear")?.addEventListener("click", () => {
      q("#project-form")?.reset();
      localStorage.removeItem(projectState.draftKey);
      projectState.results.clear();
      const root = q("#project-plan");
      if (root) {
        root.hidden = true;
        root.innerHTML = "";
      }
      const statusElement = q("#project-form-status");
      if (statusElement) statusElement.textContent = "";
      refreshScenario();
      toast("Dados da validação limpos.");
    });
  }

  async function bootProjects() {
    bind();
    try {
      await loadTemplates();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || "Falha ao carregar modelos.");
      const statusElement = q("#project-form-status");
      if (statusElement) statusElement.textContent = `Não foi possível carregar os modelos: ${message}`;
      toast(message, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", bootProjects);
})();
