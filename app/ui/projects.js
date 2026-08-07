(() => {
  viewMeta.projects = ["VALIDAÇÃO ASSISTIDA", "Projetos"];

  const projectState = {
    templates: null,
    plan: null,
    draftKey: "agent-ui-project-validation-draft-v2",
    completedKey: "agent-ui-project-validation-completed-v2",
  };

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => [...root.querySelectorAll(selector)];

  function value(id) {
    return String(q(`#${id}`)?.value || "").trim();
  }

  function checked(id) {
    return Boolean(q(`#${id}`)?.checked);
  }

  function normalizeRole(raw) {
    const value = String(raw || "").trim().toLowerCase();
    if (["production", "producao", "produção", "prod"].includes(value)) return "production";
    if (["standby", "std"].includes(value)) return "standby";
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
      linux_prod_std: "Informe somente se é Produção ou Standby e o IP VPN/TAP. A ferramenta acessa o host e descobre SO, IP interno, hardware e interface de gerenciamento.",
      linux_monitoring: "Informe o IP VPN/TAP do monitor e, se houver, os IPs VPN dos outros hosts. A ferramenta descobre os IPs internos antes dos testes 6556.",
      management_interface: "Informe o IP VPN/TAP do servidor físico. A ferramenta descobre fabricante, modelo, iDRAC/iLO/ILOM/xClarity e o IP da controladora com dmidecode/ipmitool.",
      firewall: "Informe apenas o IP VPN/TAP. A ferramenta identifica o fabricante/versão e valida agente e comunicação.",
      windows: "O fluxo Windows permanece com Socat/RDP. Se houver monitor do cliente, informe somente o IP VPN dele.",
      dns_vpn: "Informe o IP VPN/TAP do servidor. O SO é descoberto automaticamente antes de preparar qualquer ajuste de DNS.",
    };
    q("#project-scenario-help").textContent = descriptions[scenario] || "Escolha o tipo de validação e informe o IP VPN/TAP.";
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

  function completedItems() {
    try {
      return new Set(JSON.parse(localStorage.getItem(projectState.completedKey) || "[]"));
    } catch (_) {
      return new Set();
    }
  }

  function persistCompleted(root) {
    const ids = qa("[data-project-check]:checked", root).map((input) => input.dataset.projectCheck);
    localStorage.setItem(projectState.completedKey, JSON.stringify(ids));
  }

  function kindLabel(item) {
    if (item.kind === "change") return "alteração manual";
    if (item.kind === "listener") return "terminal aberto";
    if (item.kind === "manual") return "manual";
    if (item.automated) return "coleta pela IA";
    return "comando assistido";
  }

  function itemMarkup(item, completed) {
    const notes = (item.notes || []).length
      ? `<ul class="project-item-notes">${item.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>`
      : "";
    const command = item.command
      ? `<div class="project-command"><pre>${escapeHtml(item.command)}</pre><button type="button" class="ghost-button project-copy-command" data-command="${escapeHtml(item.command)}">Copiar</button></div>`
      : "";
    const approval = item.approval_required
      ? '<p class="project-approval-warning">Não será executado automaticamente. Exige revisão e autorização.</p>'
      : "";
    return `<article class="project-step ${item.kind}" data-project-item="${escapeHtml(item.id)}">
      <label class="project-step-check"><input type="checkbox" data-project-check="${escapeHtml(item.id)}"${completed.has(item.id) ? " checked" : ""}><span></span></label>
      <div class="project-step-body">
        <div class="project-step-head"><div><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.purpose)}</p></div><span class="project-kind ${escapeHtml(item.kind)}">${escapeHtml(kindLabel(item))}</span></div>
        ${command}
        ${approval}
        ${notes}
        <div class="project-evidence"><strong>Evidência:</strong> ${escapeHtml(item.evidence)}</div>
      </div>
    </article>`;
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
    const discovery = plan.discovery || {};
    const target = discoveryCard(plan.target?.name || "Alvo", discovery.target || {});
    const monitor = discovery.monitoring_server ? discoveryCard("Servidor de monitoramento", discovery.monitoring_server) : "";
    const related = (discovery.related_hosts || []).map((item, index) => discoveryCard(`Host relacionado ${index + 1}`, item, item.role || "")).join("");
    if (!target && !monitor && !related) return "";
    return `<section class="project-discovery"><div class="project-discovery-title"><p class="eyebrow">DESCOBERTA AUTOMÁTICA</p><h3>Dados obtidos pela própria ferramenta</h3><p>Esses dados não são pedidos no formulário. A ferramenta acessa os IPs VPN e coleta o ambiente.</p></div><div class="project-discovery-grid">${target}${monitor}${related}</div></section>`;
  }

  function renderPlan(plan) {
    projectState.plan = plan;
    const root = q("#project-plan");
    const completed = completedItems();
    const warnings = (plan.warnings || []).length
      ? `<div class="project-warnings">${plan.warnings.map((warning) => `<p>⚠ ${escapeHtml(warning)}</p>`).join("")}</div>`
      : "";
    const groups = (plan.groups || []).map((group) => `<section class="project-context-card">
      <header><div><p class="eyebrow">ONDE EXECUTAR</p><h3>${escapeHtml(group.label)}</h3>${group.target ? `<span>${escapeHtml(group.target)}</span>` : ""}</div><span class="project-context-kind">${escapeHtml(group.kind === "manual" ? "manual" : "terminal")}</span></header>
      <div class="project-steps">${group.items.map((item) => itemMarkup(item, completed)).join("")}</div>
    </section>`).join("");

    root.innerHTML = `<div class="project-plan-summary">
      <div><p class="eyebrow">PLANO GERADO</p><h2>${escapeHtml(plan.scenario_label)}</h2><p>Alvo VPN/TAP: ${escapeHtml(plan.target.vpn_ip)}</p></div>
      <div class="project-summary-metrics"><span><b>${escapeHtml(plan.summary.total_steps)}</b> etapas</span><span><b>${escapeHtml(plan.summary.automatic_read_only_steps)}</b> coletas IA</span><span><b>${escapeHtml(plan.summary.change_steps)}</b> alterações manuais</span></div>
    </div>
    ${discoveryMarkup(plan)}
    ${warnings}
    <div class="project-safety-note"><strong>Escopo automático:</strong> ${escapeHtml(plan.safety.automatic_scope)} <strong>Fora do automático:</strong> ${escapeHtml(plan.safety.manual_scope)}<br><strong>Infraestrutura:</strong> ${escapeHtml(plan.safety.credentials)}</div>
    <div class="project-plan-actions"><button type="button" class="primary-button" id="project-start-ai">Continuar validação com a IA</button><button type="button" class="secondary-button" id="project-copy-all">Copiar comandos</button><button type="button" class="secondary-button" id="project-copy-macro">Copiar macro</button></div>
    <div class="project-context-list">${groups}</div>
    <section class="project-macro-card"><div><p class="eyebrow">TEXTO DO TICKET</p><h3>Macro preparada com o que foi descoberto</h3></div><pre id="project-ticket-macro">${escapeHtml(plan.ticket_macro)}</pre></section>
    <div id="project-jobs" class="project-jobs" hidden></div>`;
    root.hidden = false;

    qa("[data-project-check]", root).forEach((input) => input.addEventListener("change", () => persistCompleted(root)));
    qa(".project-copy-command", root).forEach((button) => button.addEventListener("click", () => copyText(button.dataset.command, "Comando copiado.")));
    q("#project-copy-all")?.addEventListener("click", copyAllCommands);
    q("#project-copy-macro")?.addEventListener("click", () => copyText(plan.ticket_macro, "Macro copiada."));
    q("#project-start-ai")?.addEventListener("click", startProject);
    root.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function copyText(text, message) {
    try {
      await navigator.clipboard.writeText(String(text || ""));
      toast(message);
    } catch (_) {
      toast("Não foi possível copiar automaticamente.", "error");
    }
  }

  function copyAllCommands() {
    if (!projectState.plan) return;
    const sections = [];
    (projectState.plan.groups || []).forEach((group) => {
      const commands = group.items.filter((item) => item.command).map((item) => `# ${item.title}\n${item.command}`);
      if (commands.length) sections.push(`## ${group.label}${group.target ? ` — ${group.target}` : ""}\n${commands.join("\n\n")}`);
    });
    copyText(sections.join("\n\n"), "Comandos do plano copiados.");
  }

  function setPlanning(active) {
    const button = q("#project-generate");
    if (!button) return;
    button.disabled = active;
    button.textContent = active ? "Acessando IP VPN e descobrindo ambiente..." : "Acessar e montar validação";
  }

  async function generatePlan(event) {
    event?.preventDefault();
    setPlanning(true);
    q("#project-form-status").textContent = "Conectando pelo fluxo VPN já configurado e coletando SO, interfaces, hardware e gerenciamento...";
    try {
      const plan = await api("/ui/api/projects/plan", { method: "POST", body: payload() });
      renderPlan(plan);
      q("#project-form-status").textContent = "Pré-descoberta concluída. Revise as evidências e continue com a IA quando desejar.";
      toast("Ambiente acessado e plano montado com os dados descobertos.");
      saveDraft();
    } catch (error) {
      q("#project-form-status").textContent = error.message;
      toast(error.message, "error");
    } finally {
      setPlanning(false);
    }
  }

  function renderJobs(response) {
    const root = q("#project-jobs");
    const errors = (response.errors || []).map((item) => `<p class="project-job-error"><strong>${escapeHtml(item.label || item.reference)}</strong>: ${escapeHtml(item.error)}</p>`).join("");
    root.innerHTML = `<div class="project-jobs-head"><div><p class="eyebrow">FILA OPERACIONAL</p><h3>Validações iniciadas</h3><p>${escapeHtml(response.message)}</p></div></div>
      <div class="project-job-grid">${(response.jobs || []).map((job) => `<article><span class="pulse-dot"></span><div><strong>${escapeHtml(job.label || job.reference)}</strong><p>${escapeHtml(job.reference)} · ${escapeHtml(job.environment)}</p><code>${escapeHtml(job.job_id)}</code></div></article>`).join("")}</div>${errors}`;
    root.hidden = false;
    root.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function startProject() {
    const button = q("#project-start-ai");
    if (!button) return;
    button.disabled = true;
    button.textContent = "Enfileirando validações...";
    try {
      const response = await api("/ui/api/projects/start", { method: "POST", body: payload() });
      renderJobs(response);
      toast(`${response.jobs.length} validação(ões) enviada(s) ao worker.`);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Continuar validação com a IA";
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
    q("#project-form")?.addEventListener("submit", generatePlan);
    q("#project-scenario")?.addEventListener("change", refreshScenario);
    q("#project-has-monitor")?.addEventListener("change", refreshScenario);
    qa("#project-form input, #project-form select, #project-form textarea").forEach((element) => {
      element.addEventListener("change", saveDraft);
      if (element.tagName === "TEXTAREA" || element.type === "text") element.addEventListener("input", saveDraft);
    });
    q("#project-clear")?.addEventListener("click", () => {
      q("#project-form").reset();
      localStorage.removeItem(projectState.draftKey);
      localStorage.removeItem(projectState.completedKey);
      q("#project-plan").hidden = true;
      q("#project-plan").innerHTML = "";
      q("#project-form-status").textContent = "";
      refreshScenario();
      toast("Rascunho da validação limpo.");
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
