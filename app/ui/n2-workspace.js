(() => {
  let sites = [];
  let context = null;
  let draft = null;
  let loaded = false;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function request(path, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const headers = { ...(options.headers || {}) };
    if (method !== "GET") headers["X-Agent-UI"] = "1";
    let body = options.body;
    if (body && typeof body === "object") {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, method, headers, body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  function modal() {
    return document.querySelector("#n2-workspace-modal");
  }

  function openModal() {
    const root = modal();
    if (!root) return;
    root.classList.add("open");
    root.setAttribute("aria-hidden", "false");
    document.body.classList.add("n2-modal-open");
    void loadSites();
  }

  function closeModal() {
    const root = modal();
    if (!root) return;
    root.classList.remove("open");
    root.setAttribute("aria-hidden", "true");
    document.body.classList.remove("n2-modal-open");
  }

  function ensureEntryButton() {
    const projectHead = document.querySelector("#view-projects .project-builder-head");
    if (!projectHead || document.querySelector("#open-n2-workspace")) return Boolean(projectHead);
    let actions = projectHead.querySelector(".n2-project-actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.className = "n2-project-actions";
      projectHead.appendChild(actions);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.id = "open-n2-workspace";
    button.className = "secondary-button n2-entry-button";
    button.innerHTML = '<span aria-hidden="true">▤</span><span>Área N2</span>';
    button.title = "Abrir ferramenta de documentação e validação para o analista N2";
    actions.appendChild(button);
    button.addEventListener("click", openModal);
    return true;
  }

  function ensureModal() {
    if (modal()) return true;
    if (!document.body) return false;
    const root = document.createElement("aside");
    root.className = "n2-workspace-modal";
    root.id = "n2-workspace-modal";
    root.setAttribute("aria-hidden", "true");
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "n2-workspace-title");
    root.innerHTML = `
      <div class="n2-modal-backdrop" data-close-n2></div>
      <div class="n2-modal-panel">
        <header class="n2-modal-header">
          <div><p class="eyebrow">FERRAMENTA PARA O ANALISTA N2</p><h2 id="n2-workspace-title">Documentação e validação com IA</h2><p>Área adicional. A operação normal do Agent IA continua exatamente como está.</p></div>
          <button type="button" class="icon-button" data-close-n2 aria-label="Fechar Área N2">×</button>
        </header>
        <div class="n2-modal-body">
          <div class="n2-shell">
            <article class="panel n2-sidebar-panel">
              <div class="n2-title"><p class="eyebrow">BASE DO CMK05</p><h3>Selecionar ambiente</h3><p>A IA aproveita o que já está comprovado e marca o restante como pendência de coleta.</p></div>
              <label class="n2-field"><span>Cliente / site</span><select id="n2-site"><option value="">Carregando...</option></select></label>
              <div class="n2-responsibles">
                <label class="n2-field"><span>Responsável Infra</span><input id="n2-resp-infra" placeholder="Nome"></label>
                <label class="n2-field"><span>Responsável DBA</span><input id="n2-resp-dba" placeholder="Nome"></label>
                <label class="n2-field"><span>Responsável NOC</span><input id="n2-resp-noc" placeholder="Nome"></label>
                <label class="n2-field"><span>Revisão</span><input id="n2-resp-review" placeholder="Nome"></label>
              </div>
              <button type="button" class="primary-button" id="n2-build-draft">Montar rascunho N2</button>
              <div class="n2-safe-note"><strong>Sem credenciais</strong><span>Senhas, communities e secrets não entram no rascunho nem no prompt.</span></div>
            </article>
            <div class="n2-main">
              <article class="panel" id="n2-context-panel"><div class="empty-state">Selecione um cliente para abrir o ambiente.</div></article>
              <article class="panel" id="n2-draft-panel" hidden></article>
            </div>
          </div>
        </div>
      </div>`;
    document.body.appendChild(root);

    root.querySelectorAll("[data-close-n2]").forEach((item) => item.addEventListener("click", closeModal));
    root.querySelector("#n2-site")?.addEventListener("change", (event) => void loadContext(event.currentTarget.value));
    root.querySelector("#n2-build-draft")?.addEventListener("click", () => void buildDraft());
    return true;
  }

  async function loadSites() {
    const select = modal()?.querySelector("#n2-site");
    if (!select || (loaded && sites.length)) return;
    select.innerHTML = '<option value="">Carregando clientes...</option>';
    try {
      const data = await request("/ui/api/n2/sites?limit=1000");
      sites = data.items || [];
      loaded = true;
      select.innerHTML = '<option value="">Selecione o cliente</option>' + sites.map((site) => `<option value="${esc(site.site_id)}">${esc(site.alias)} · ${esc(site.site_id)}</option>`).join("");
    } catch (error) {
      select.innerHTML = `<option value="">${esc(error.message)}</option>`;
    }
  }

  function problemCountFor(hostName) {
    return (context?.problems || []).filter((item) => item.host === hostName).length;
  }

  function openInvestigation(host, problem = null) {
    const site = context?.site || {};
    const entry = site.endpoint || "";
    const target = host?.ip && host.ip !== "0.0.0.0" ? host.ip : entry;
    const problemText = problem
      ? `${problem.service} ${problem.state}: ${problem.output}`
      : `Validar o host ${host?.host || "selecionado"} (${host?.ip || "sem IP"}) dentro do cliente ${site.alias || site.site_id}.`;
    const objective = `Cliente/site: ${site.alias || site.site_id} (${site.site_id}). Host interno: ${host?.host || "-"} / ${host?.ip || "-"}. ${problemText} Investigue com segurança, preserve o isolamento deste cliente e nunca reinicie o servidor.`;

    closeModal();
    const openButton = document.querySelector("#topbar-start-investigation") || document.querySelector("[data-open-analysis]");
    openButton?.click();
    window.setTimeout(() => {
      const targetField = document.querySelector("#target");
      const objectiveField = document.querySelector("#objective");
      if (targetField) targetField.value = entry || target;
      if (objectiveField) objectiveField.value = objective;
      targetField?.dispatchEvent(new Event("input", { bubbles: true }));
      objectiveField?.dispatchEvent(new Event("input", { bubbles: true }));
    }, 120);
  }

  function renderContext() {
    const root = modal()?.querySelector("#n2-context-panel");
    if (!root || !context) return;
    const site = context.site || {};
    const hosts = context.hosts || [];
    const problems = context.problems || [];
    root.innerHTML = `
      <div class="n2-context-head"><div><p class="eyebrow">AMBIENTE</p><h3>${esc(site.alias || site.site_id)}</h3><span>${esc(site.site_id)} · ${esc(site.endpoint || "—")}:${esc(site.port || "—")}</span></div><div class="n2-context-metrics"><span><strong>${esc(hosts.length)}</strong> hosts</span><span><strong>${esc(problems.length)}</strong> problemas</span></div></div>
      <div class="n2-context-grid">
        <section><h4>Hosts</h4><div class="n2-table-wrap"><table class="n2-table"><thead><tr><th>Host</th><th>IP</th><th>Papel</th><th>Problemas</th><th></th></tr></thead><tbody>${hosts.length ? hosts.map((host) => `<tr><td><strong>${esc(host.host)}</strong></td><td>${esc(host.ip || "—")}</td><td>${esc(host.kind || host.environment || "—")}</td><td>${esc(problemCountFor(host.host))}</td><td><button class="ghost-button" type="button" data-n2-investigate-host="${esc(host.host)}">IA</button></td></tr>`).join("") : '<tr><td colspan="5" class="empty-cell">Nenhum host conhecido.</td></tr>'}</tbody></table></div></section>
        <section><h4>Alertas atuais</h4><div class="n2-problems">${problems.length ? problems.map((problem, index) => `<article><div><span class="n2-state ${esc(String(problem.state).toLowerCase())}">${esc(problem.state)}</span><strong>${esc(problem.host)}</strong><small>${esc(problem.ip || "—")}</small></div><div><strong>${esc(problem.service)}</strong><p>${esc(problem.output || "")}</p><button type="button" class="ghost-button" data-n2-investigate-problem="${index}">Investigar com IA</button></div></article>`).join("") : '<div class="empty-state">Nenhum problema ativo.</div>'}</div></section>
      </div>`;
    root.querySelectorAll("[data-n2-investigate-host]").forEach((button) => button.addEventListener("click", () => {
      const host = hosts.find((item) => item.host === button.dataset.n2InvestigateHost);
      openInvestigation(host);
    }));
    root.querySelectorAll("[data-n2-investigate-problem]").forEach((button) => button.addEventListener("click", () => {
      const problem = problems[Number(button.dataset.n2InvestigateProblem || 0)];
      const host = hosts.find((item) => item.host === problem?.host) || { host: problem?.host, ip: problem?.ip };
      openInvestigation(host, problem);
    }));
  }

  async function loadContext(siteId) {
    const root = modal()?.querySelector("#n2-context-panel");
    const draftRoot = modal()?.querySelector("#n2-draft-panel");
    if (!siteId) {
      context = null;
      if (root) root.innerHTML = '<div class="empty-state">Selecione um cliente para abrir o ambiente.</div>';
      if (draftRoot) draftRoot.hidden = true;
      return;
    }
    if (root) root.innerHTML = '<div class="empty-state">Carregando ambiente...</div>';
    try {
      context = await request(`/ui/api/n2/sites/${encodeURIComponent(siteId)}`);
      draft = null;
      if (draftRoot) draftRoot.hidden = true;
      renderContext();
    } catch (error) {
      if (root) root.innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
    }
  }

  function renderDraft() {
    const root = modal()?.querySelector("#n2-draft-panel");
    if (!root || !draft) return;
    root.hidden = false;
    const sections = draft.sections || [];
    root.innerHTML = `
      <div class="n2-draft-head"><div><p class="eyebrow">RASCUNHO N2</p><h3>${esc(draft.client)}</h3><span>Base: ${esc(draft.generated_from)}</span></div><span class="n2-security-badge">sem segredos</span></div>
      <div class="n2-template-grid">${sections.map((section) => `<article class="n2-template-card ${esc(section.status)}"><header><span>${esc(section.status === "ready" ? "Pronto" : section.status === "partial" ? "Parcial" : "Pendente")}</span><h4>${esc(section.title)}</h4></header>${section.known?.length ? `<div class="n2-known"><strong>Já conhecido</strong>${section.known.map((item) => `<p>✓ ${esc(item)}</p>`).join("")}</div>` : ""}${section.missing?.length ? `<div class="n2-missing"><strong>Falta validar</strong>${section.missing.map((item) => `<p>• ${esc(item)}</p>`).join("")}</div>` : ""}</article>`).join("")}</div>
      <div class="n2-guidance"><strong>Como usar a IA</strong>${(draft.ai_guidance || []).map((item) => `<span>✓ ${esc(item)}</span>`).join("")}</div>`;
    root.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function buildDraft() {
    const root = modal();
    const siteId = root?.querySelector("#n2-site")?.value || "";
    if (!siteId) return;
    const button = root?.querySelector("#n2-build-draft");
    const original = button?.textContent || "Montar rascunho N2";
    if (button) { button.disabled = true; button.textContent = "Montando..."; }
    try {
      draft = await request("/ui/api/n2/draft", {
        method: "POST",
        body: {
          site_id: siteId,
          responsibles: {
            infra: root?.querySelector("#n2-resp-infra")?.value || "",
            dba: root?.querySelector("#n2-resp-dba")?.value || "",
            noc: root?.querySelector("#n2-resp-noc")?.value || "",
            review: root?.querySelector("#n2-resp-review")?.value || "",
          },
        },
      });
      renderDraft();
    } catch (error) {
      window.alert(error.message);
    } finally {
      if (button) { button.disabled = false; button.textContent = original; }
    }
  }

  function boot() {
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      const ready = ensureModal() && ensureEntryButton();
      if (ready) window.clearInterval(timer);
      else if (attempts > 120) window.clearInterval(timer);
    }, 300);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal()?.classList.contains("open")) closeModal();
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
