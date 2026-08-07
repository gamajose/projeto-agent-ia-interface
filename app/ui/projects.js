(() => {
  viewMeta.projects = ["VALIDAÇÃO ASSISTIDA", "Projetos"];

  const projectState = {
    templates: null,
    plan: null,
    draftKey: "agent-ui-project-validation-draft-v1",
    completedKey: "agent-ui-project-validation-completed-v1",
  };

  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => [...root.querySelectorAll(selector)];

  function value(id) {
    return String(q(`#${id}`)?.value || "").trim();
  }

  function checked(id) {
    return Boolean(q(`#${id}`)?.checked);
  }

  function parseRelatedHosts(raw) {
    return String(raw || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => {
        const parts = line.split("|").map((item) => item.trim());
        if (parts.length < 3) {
          throw new Error(`Linha ${index + 1} dos hosts relacionados deve usar: Nome | papel | IP interno | IP VPN opcional`);
        }
        return {
          name: parts[0],
          role: parts[1] || "server",
          internal_ip: parts[2],
          vpn_ip: parts[3] || null,
        };
      });
  }

  function payload() {
    return {
      project_name: value("project-name") || "Validação de projeto",
      ticket_number: value("project-ticket") || null,
      scenario: value("project-scenario"),
      role: value("project-role") || "production",
      target_name: value("project-target-name") || "Servidor do projeto",
      target_vpn_ip: value("project-target-vpn"),
      target_internal_ip: value("project-target-internal") || null,
      os_family: value("project-os-family") || "unknown",
      install_agent: checked("project-install-agent"),
      has_monitoring_server: checked("project-has-monitor"),
      monitoring_name: value("project-monitor-name") || null,
      monitoring_vpn_ip: value("project-monitor-vpn") || null,
      monitoring_internal_ip: value("project-monitor-internal") || null,
      related_hosts: parseRelatedHosts(value("project-related-hosts")),
      management_interface_type: value("project-management-type") || "auto",
      management_interface_ip: value("project-management-ip") || null,
      firewall_type: value("project-firewall-type") || "unknown",
      gateway_dns: value("project-gateway-dns") || null,
      vpn_dns_name: value("project-vpn-dns-name") || "vpn.oracledba.com.br",
      monitor1_ip: value("project-monitor1-ip") || "10.17.181.1",
      monitor1_user: value("project-monitor1-user") || "jose.moraes",
      cmk05_ip: value("project-cmk05-ip") || "10.17.181.44",
      whatsapp_host: value("project-whatsapp-host") || "ws.2comconsulting.com.br",
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
    const isLinux = ["linux_prod_std", "linux_monitoring", "management_interface", "dns_vpn"].includes(scenario);
    const isMonitoring = scenario === "linux_monitoring";
    const isManagement = scenario === "management_interface" || ["linux_prod_std", "linux_monitoring"].includes(scenario);
    const isWindows = scenario === "windows";
    const isFirewall = scenario === "firewall";
    const isDns = scenario === "dns_vpn";

    setHidden("#project-role-field", scenario !== "linux_prod_std");
    setHidden("#project-os-field", !(isLinux || isWindows));
    setHidden("#project-agent-field", !(scenario === "linux_prod_std" || isMonitoring));
    setHidden("#project-monitor-toggle-field", isMonitoring || !(scenario === "linux_prod_std" || scenario === "management_interface" || isWindows));
    setHidden("#project-monitor-fields", isMonitoring || !checked("project-has-monitor"));
    setHidden("#project-related-fields", !isMonitoring);
    setHidden("#project-management-fields", !isManagement);
    setHidden("#project-firewall-fields", !isFirewall);
    setHidden("#project-dns-fields", !isDns);

    if (isMonitoring) {
      q("#project-role").value = "monitoring";
      q("#project-has-monitor").checked = false;
    }
    if (isWindows) q("#project-os-family").value = "windows";
    if (isFirewall) q("#project-os-family").value = value("project-firewall-type") === "pfsense" ? "pfsense" : "fortigate";

    const descriptions = {
      linux_prod_std: "Produção/standby: hardware, SO, acesso root, agente Checkmk, 6556 nos dois sentidos e interface de gerenciamento.",
      linux_monitoring: "Monitoramento: inclui IPs internos dos demais hosts, Livestatus, Monitor 1, Monitor 5/6557 e API do WhatsApp.",
      management_interface: "Interface: hardware, ipmitool e SNMP pelo próprio host ou pelo servidor de monitoramento compartilhado.",
      firewall: "Firewall: painel, shell, versão, agente e porta 6556 nos dois sentidos.",
      windows: "Windows: Socat/RDP, systeminfo, IP, agente e comunicação 6556 pela rede interna.",
      dns_vpn: "DNS da VPN: compara resolvers, lê logs e prepara ajuste específico para OL7/OL8 sem aplicar automaticamente.",
    };
    q("#project-scenario-help").textContent = descriptions[scenario] || "Selecione um cenário.";
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
      <div><p class="eyebrow">PLANO GERADO</p><h2>${escapeHtml(plan.project_name)}</h2><p>${escapeHtml(plan.scenario_label)} · ${escapeHtml(plan.target.name)} (${escapeHtml(plan.target.vpn_ip)})</p></div>
      <div class="project-summary-metrics"><span><b>${escapeHtml(plan.summary.total_steps)}</b> etapas</span><span><b>${escapeHtml(plan.summary.automatic_read_only_steps)}</b> coletas IA</span><span><b>${escapeHtml(plan.summary.change_steps)}</b> alterações manuais</span></div>
    </div>
    ${warnings}
    <div class="project-safety-note"><strong>Escopo automático:</strong> ${escapeHtml(plan.safety.automatic_scope)} <strong>Fora do automático:</strong> ${escapeHtml(plan.safety.manual_scope)}</div>
    <div class="project-plan-actions"><button type="button" class="primary-button" id="project-start-ai">Iniciar validações de leitura com a IA</button><button type="button" class="secondary-button" id="project-copy-all">Copiar comandos</button><button type="button" class="secondary-button" id="project-copy-macro">Copiar macro</button></div>
    <div class="project-context-list">${groups}</div>
    <section class="project-macro-card"><div><p class="eyebrow">TEXTO DO TICKET</p><h3>Macro preparada</h3></div><pre id="project-ticket-macro">${escapeHtml(plan.ticket_macro)}</pre></section>
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
    button.textContent = active ? "Montando cenário..." : "Gerar plano do projeto";
  }

  async function generatePlan(event) {
    event?.preventDefault();
    setPlanning(true);
    try {
      const plan = await api("/ui/api/projects/plan", { method: "POST", body: payload() });
      renderPlan(plan);
      toast("Plano de projeto montado conforme o cenário informado.");
      saveDraft();
    } catch (error) {
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
      button.textContent = "Iniciar validações de leitura com a IA";
    }
  }

  async function loadTemplates() {
    projectState.templates = await api("/ui/api/projects/templates");
    const defaults = projectState.templates.defaults || {};
    const scenario = q("#project-scenario");
    scenario.innerHTML = (projectState.templates.scenarios || []).map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
    const os = q("#project-os-family");
    os.innerHTML = (projectState.templates.os_families || []).map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
    const management = q("#project-management-type");
    management.innerHTML = (projectState.templates.management_interfaces || []).map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
    q("#project-monitor1-ip").value ||= defaults.monitor1_ip || "10.17.181.1";
    q("#project-monitor1-user").value ||= defaults.monitor1_user || "jose.moraes";
    q("#project-cmk05-ip").value ||= defaults.cmk05_ip || "10.17.181.44";
    q("#project-whatsapp-host").value ||= defaults.whatsapp_host || "ws.2comconsulting.com.br";
    q("#project-vpn-dns-name").value ||= defaults.vpn_dns_name || "vpn.oracledba.com.br";
    restoreDraft();
    refreshScenario();
  }

  function bind() {
    q("#project-form")?.addEventListener("submit", generatePlan);
    q("#project-scenario")?.addEventListener("change", refreshScenario);
    q("#project-has-monitor")?.addEventListener("change", refreshScenario);
    q("#project-firewall-type")?.addEventListener("change", refreshScenario);
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
      refreshScenario();
      toast("Rascunho do projeto limpo.");
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
