(() => {
  viewMeta.projects = ["VALIDAÇÃO AUTOMÁTICA", "Projetos"];

  const projectState = {
    templates: null,
    plan: null,
    results: new Map(),
    draftKey: "agent-ui-project-validation-draft-v3",
  };

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => [...root.querySelectorAll(selector)];
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
      linux_prod_std: "Informe o IP VPN/TAP. A IA entra no host, descobre o ambiente e executa automaticamente as validações de leitura do playbook de Produção/Standby.",
      linux_monitoring: "Informe o IP VPN/TAP do monitor. A IA descobre IPs internos, acessa os hosts relacionados e executa os testes de monitoramento aplicáveis.",
      management_interface: "Informe o IP VPN/TAP do servidor físico. A IA identifica fabricante, BMC e executa as validações disponíveis sem entregar comandos para copiar.",
      firewall: "Informe o IP VPN/TAP. A IA identifica o equipamento e executa as coletas e testes seguros aplicáveis.",
      windows: "Informe o IP VPN/TAP. A IA conduz o fluxo disponível e registra claramente o que depende de acesso Windows/RDP.",
      dns_vpn: "Informe o IP VPN/TAP. A IA investiga DNS e VPN; qualquer alteração fica como proposta sujeita às políticas de aprovação.",
    };
    q("#project-scenario-help").textContent = descriptions[scenario] || "Informe o IP VPN/TAP e execute. Os comandos ficam dentro da IA.";
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

  function managementLabel(facts) {
    const labels = { idrac: "iDRAC", ilo: "iLO", ilom: "ILOM", xclarity: "xClarity", unknown: "Não identificada", none: "Não identificada" };
    const type = labels[facts?.management_type] || facts?.management_type || "Não identificada";
    return facts?.management_ip ? `${type} · ${facts.management_ip}` : type;
  }

  function discoveryCard(title, facts, role = "") {
    if (!facts) return "";
    const reachable = facts.reachable === true ? "Acessado" : facts.reachable === false ? "Falha no acesso" : "Pendente";
    const tone = facts.reachable === true ? "good" : facts.reachable === false ? "bad" : "neutral";
    return `<article class="project-discovery-card" data-tone="${tone}">
      <div class="project-discovery-head"><div><strong>${escapeHtml(title)}</strong>${role ? `<span>${escapeHtml(role)}</span>` : ""}</div><span>${escapeHtml(reachable)}</span></div>
      <dl>
        <div><dt>IP VPN</dt><dd>${escapeHtml(facts.vpn_ip || "—")}</dd></div>
        <div><dt>Sistema operacional</dt><dd>${escapeHtml(facts.os_name || "A descobrir")}</dd></div>
        <div><dt>IP interno</dt><dd>${escapeHtml(facts.internal_ip || "A descobrir")}</dd></div>
        <div><dt>Máquina</dt><dd>${escapeHtml(facts.machine_type || "A descobrir")}${facts.virtualization && facts.virtualization !== "unknown" ? ` · ${escapeHtml(facts.virtualization)}` : ""}</dd></div>
        <div><dt>Hardware</dt><dd>${escapeHtml([facts.manufacturer, facts.model].filter(Boolean).join(" ") || "A descobrir")}</dd></div>
        <div><dt>Gerenciamento</dt><dd>${escapeHtml(managementLabel(facts))}</dd></div>
      </dl>
      ${facts.error ? `<p class="project-discovery-error">${escapeHtml(facts.error)}</p>` : ""}
    </article>`;
  }

  function discoveryMarkup(plan) {
    const discovery = plan?.discovery || {};
    const target = discoveryCard(plan?.target?.name || "Alvo", discovery.target || {});
    const monitor = discovery.monitoring_server ? discoveryCard("Servidor de monitoramento", discovery.monitoring_server) : "";
    const related = (discovery.related_hosts || []).map((item, index) => discoveryCard(`Host relacionado ${index + 1}`, item, item.role || "")).join("");
    if (!target && !monitor && !related) return "";
    return `<section class="project-discovery"><div class="project-discovery-title"><p class="eyebrow">DESCOBERTA AUTOMÁTICA</p><h3>O que a IA encontrou antes da análise profunda</h3><p>Esses dados foram coletados pela própria aplicação a partir do IP informado.</p></div><div class="project-discovery-grid">${target}${monitor}${related}</div></section>`;
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

  function analysisMarkup(result) {
    const analysis = result?.analysis || {};
    const recommendations = analysis.recommendations || [];
    const cause = analysis.probable_cause || "A IA ainda não confirmou uma causa provável.";
    const conclusion = analysis.conclusion || analysis.summary || "Análise concluída.";
    const approval = result?.approval_token
      ? '<button type="button" class="primary-button project-review-result">Revisar proposta e aprovar</button>'
      : "";
    return `<div class="project-job-result">
      <div class="project-result-line"><strong>Status</strong><span>${escapeHtml(labelStatus(analysis.status || result?.status || "inconclusive"))} · ${escapeHtml(analysis.confidence ?? result?.confidence ?? 0)}%</span></div>
      <div class="project-result-block"><strong>Causa provável</strong><p>${escapeHtml(cause)}</p></div>
      <div class="project-result-block"><strong>Conclusão</strong><p>${escapeHtml(conclusion)}</p></div>
      ${recommendations.length ? `<div class="project-result-block"><strong>Próximas ações</strong><ul>${recommendations.slice(0, 6).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
      <div class="project-result-actions">${approval}${result?.investigation_id ? '<button type="button" class="secondary-button project-open-investigation">Ver evidências</button>' : ""}</div>
    </div>`;
  }

  function jobCard(meta) {
    return `<article class="project-job-card" data-project-job="${escapeHtml(meta.job_id || meta.investigation_id || meta.reference)}" data-state="${escapeHtml(meta.status || "queued")}">
      <div class="project-job-card-head"><div><span class="pulse-dot"></span><strong>${escapeHtml(meta.label || meta.reference)}</strong></div><span class="project-job-status">${escapeHtml(statusLabel(meta.status))}</span></div>
      <p>${escapeHtml(meta.reference)} · ${escapeHtml(meta.environment || "unknown")}</p>
      <div class="project-job-progress"><span style="width:${meta.status === "completed" ? 100 : 4}%"></span></div>
      <small class="project-job-phase">${meta.status === "completed" ? "Validação concluída." : "A IA vai executar os testes do playbook e analisar as saídas."}</small>
      <div class="project-job-output"></div>
    </article>`;
  }

  function findJobCard(key) {
    return qa("[data-project-job]", q("#project-plan")).find((element) => element.dataset.projectJob === String(key));
  }

  function bindResultActions(card, result) {
    q(".project-review-result", card)?.addEventListener("click", () => {
      if (typeof showResult === "function") showResult(result);
    });
    q(".project-open-investigation", card)?.addEventListener("click", () => {
      if (result?.investigation_id && typeof openInvestigation === "function") openInvestigation(result.investigation_id);
    });
  }

  function updateJobCard(key, job) {
    const card = findJobCard(key);
    if (!card) return;
    const status = job.status || "running";
    card.dataset.state = status;
    q(".project-job-status", card).textContent = statusLabel(status);
    const percent = Math.max(0, Math.min(100, Number(job.percent ?? (status === "completed" ? 100 : 8))));
    q(".project-job-progress span", card).style.width = `${percent}%`;
    const phase = job.current_phase || {};
    q(".project-job-phase", card).textContent = phase.detail || (status === "completed" ? "Coleta, análise e persistência concluídas." : statusLabel(status));
    const output = q(".project-job-output", card);

    if (status === "completed" && job.result) {
      const result = job.result;
      projectState.results.set(String(key), result);
      output.innerHTML = analysisMarkup(result);
      bindResultActions(card, result);
    } else if (status === "failed") {
      output.innerHTML = `<p class="project-job-error">${escapeHtml(job.error || "A execução falhou sem detalhe adicional.")}</p>`;
    } else if (status === "cancelled") {
      output.innerHTML = '<p class="project-job-error">Execução cancelada.</p>';
    }
  }

  function renderExecution(response) {
    projectState.plan = response.plan || null;
    projectState.results.clear();
    const root = q("#project-plan");
    const plan = response.plan || {};
    const warnings = (plan.warnings || []).length
      ? `<div class="project-warnings">${plan.warnings.map((warning) => `<p>⚠ ${escapeHtml(warning)}</p>`).join("")}</div>`
      : "";
    const metas = [
      ...(response.jobs || []),
      ...(response.executions || []).map((item) => ({ ...item, job_id: item.investigation_id || item.reference })),
    ];
    const errors = (response.errors || []).map((item) => `<p class="project-job-error"><strong>${escapeHtml(item.label || item.reference)}</strong>: ${escapeHtml(item.error)}</p>`).join("");

    root.innerHTML = `<div class="project-plan-summary">
      <div><p class="eyebrow">IA EM EXECUÇÃO</p><h2>${escapeHtml(plan.scenario_label || "Validação automática")}</h2><p>Alvo VPN/TAP: ${escapeHtml(plan.target?.vpn_ip || value("project-target-vpn"))}</p></div>
      <div class="project-summary-metrics"><span><b>${escapeHtml(metas.length)}</b> investigação(ões)</span><span><b>${escapeHtml(plan.summary?.automatic_read_only_steps ?? "—")}</b> coletas previstas</span><span><b>0</b> comandos para copiar</span></div>
    </div>
    ${discoveryMarkup(plan)}
    ${warnings}
    <div class="project-safety-note"><strong>Execução:</strong> a IA executa coletas e testes de leitura automaticamente. <strong>Alterações:</strong> permanecem como proposta e só avançam quando as políticas permitirem e houver revisão/aprovação.</div>
    <section class="project-jobs"><div class="project-jobs-head"><div><p class="eyebrow">EXECUÇÃO OPERACIONAL</p><h3>A IA está fazendo o trabalho</h3><p>${escapeHtml(response.message || "Validação iniciada.")}</p></div></div><div class="project-job-grid">${metas.map(jobCard).join("")}</div>${errors}</section>
    ${plan.ticket_macro ? `<section class="project-macro-card"><div><p class="eyebrow">TEXTO DO TICKET</p><h3>Macro de apoio</h3></div><pre id="project-ticket-macro">${escapeHtml(plan.ticket_macro)}</pre></section>` : ""}`;
    root.hidden = false;
    root.scrollIntoView({ behavior: "smooth", block: "start" });

    (response.executions || []).forEach((item) => {
      const key = item.investigation_id || item.reference;
      updateJobCard(key, { status: "completed", percent: 100, result: item.result, current_phase: { detail: "Validação executada diretamente pela IA." } });
    });
  }

  async function pollJob(meta) {
    const key = meta.job_id;
    for (;;) {
      let job;
      try {
        job = await api(`/ui/api/jobs/${encodeURIComponent(meta.job_id)}`);
      } catch (error) {
        updateJobCard(key, { status: "failed", error: error.message });
        return "failed";
      }
      updateJobCard(key, job);
      if (["completed", "failed", "cancelled"].includes(job.status)) return job.status;
      await wait(1400);
    }
  }

  async function pollProjectJobs(response) {
    const jobs = response.jobs || [];
    if (!jobs.length) return [];
    const statuses = await Promise.all(jobs.map(pollJob));
    const completed = statuses.filter((status) => status === "completed").length;
    const failed = statuses.filter((status) => status === "failed").length;
    q("#project-form-status").textContent = `Execução finalizada: ${completed} concluída(s)${failed ? `, ${failed} com falha` : ""}.`;
    return statuses;
  }

  function setPlanning(active) {
    const button = q("#project-generate");
    if (!button) return;
    button.disabled = active;
    button.textContent = active ? "IA acessando e executando..." : "Executar validação com IA";
  }

  async function executeProject(event) {
    event?.preventDefault();
    setPlanning(true);
    q("#project-form-status").textContent = "A IA está acessando o IP, descobrindo o ambiente e executando as validações. Você não precisa copiar nenhum comando.";
    try {
      const response = await api("/ui/api/projects/start", { method: "POST", body: payload() });
      renderExecution(response);
      saveDraft();
      if (response.execution_mode === "queue") {
        toast(`${response.jobs.length} investigação(ões) iniciada(s). A IA vai executar e analisar automaticamente.`);
        await pollProjectJobs(response);
      } else {
        q("#project-form-status").textContent = `${response.executions.length} validação(ões) executada(s) diretamente pela IA.`;
        toast("Validação executada pela IA. Revise a causa e a proposta no resultado.");
      }
    } catch (error) {
      q("#project-form-status").textContent = error.message;
      toast(error.message, "error");
    } finally {
      setPlanning(false);
    }
  }

  async function loadTemplates() {
    projectState.templates = await api("/ui/api/projects/templates");
    const defaults = projectState.templates.defaults || {};
    const scenario = q("#project-scenario");
    scenario.innerHTML = (projectState.templates.scenarios || []).map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
    q("#project-vpn-dns-name").value ||= defaults.vpn_dns_name || "vpn.oracledba.com.br";
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
      q("#project-form").reset();
      localStorage.removeItem(projectState.draftKey);
      projectState.results.clear();
      q("#project-plan").hidden = true;
      q("#project-plan").innerHTML = "";
      q("#project-form-status").textContent = "";
      refreshScenario();
      toast("Dados da validação limpos.");
    });
  }

  async function bootProjects() {
    bind();
    try {
      await loadTemplates();
    } catch (error) {
      q("#project-form-status").textContent = `Não foi possível carregar os modelos: ${error.message}`;
      toast(error.message, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", bootProjects);
})();
