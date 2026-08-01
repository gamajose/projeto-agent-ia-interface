(() => {
  const FAVORITES_KEY = "agent-ui-favorite-scopes";
  const RECENTS_KEY = "agent-ui-recent-scopes";
  const MAX_RECENTS = 8;
  const roleLabels = {
    monitoring: "Monitoramento",
    production: "Produção",
    standby: "Standby",
    database: "Servidor de banco",
    application: "Aplicação",
    firewall: "Firewall",
    other: "Outro",
  };
  const environmentLabels = {
    monitoring: "Monitoramento",
    production: "Produção",
    standby: "Standby",
    training: "Treinamento",
    unknown: "Não informado",
  };
  let replayStream = null;
  let currentWizardStep = 0;
  let wizardPanels = [];
  let customerCache = [];

  function safeArray(value) { return Array.isArray(value) ? value : []; }
  function text(value, fallback = "—") { return String(value ?? "").trim() || fallback; }
  function storage(key, fallback = []) { return window.AgentUI?.storage(key, fallback) ?? fallback; }
  function persist(key, value) { return window.AgentUI?.persist(key, value) ?? value; }

  function customViewMarkup() {
    return `<section class="view" id="view-customers">
      <article class="panel customer-overview-panel">
        <div class="panel-header stacked-mobile"><div><p class="eyebrow">AMBIENTES MAPEADOS</p><h3>Clientes e topologias</h3><p class="operator-subtitle">Visualize entrada VPN, hosts internos e rotas SSH sem expor credenciais.</p></div><div class="filters"><input id="customer-search" placeholder="Buscar empresa, host ou IP"><button class="secondary-button" id="customer-filter">Buscar</button></div></div>
        <div class="favorite-scopes" id="favorite-scopes"></div>
        <div class="customer-grid" id="customer-grid"><div class="empty-state">Carregando clientes...</div></div>
      </article>
    </section>
    <section class="view" id="view-replay">
      <article class="panel replay-panel">
        <div class="panel-header stacked-mobile"><div><p class="eyebrow">LABORATÓRIO LOCAL</p><h3>Demonstração e replay no WSL</h3><p class="operator-subtitle">Cenários sanitizados percorrem a interface completa sem abrir SSH, VPN ou banco real.</p></div><span class="replay-safe-badge">100% local e sanitizado</span></div>
        <div class="replay-grid" id="replay-grid"><div class="empty-state">Carregando cenários...</div></div>
        <div class="replay-console" id="replay-console" hidden></div>
      </article>
    </section>`;
  }

  function injectViews() {
    if ($("#view-customers")) return;
    const main = $(".main");
    if (!main) return;
    main.insertAdjacentHTML("beforeend", customViewMarkup());
    const nav = $(".nav");
    const inventory = nav?.querySelector('[data-view="inventory"]');
    if (nav && !nav.querySelector('[data-view="customers"]')) {
      inventory?.insertAdjacentHTML("afterend", '<button class="nav-item" data-view="customers"><span class="nav-icon">⌘</span><span>Clientes</span></button>');
      nav.insertAdjacentHTML("beforeend", '<button class="nav-item" data-view="replay"><span class="nav-icon">▷</span><span>Replay WSL</span></button>');
    }
    nav?.querySelector('[data-view="customers"]')?.addEventListener("click", () => activateCustomView("customers"));
    nav?.querySelector('[data-view="replay"]')?.addEventListener("click", () => activateCustomView("replay"));
    $("#customer-filter")?.addEventListener("click", () => loadCustomers($("#customer-search")?.value.trim() || ""));
    $("#customer-search")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); void loadCustomers(event.currentTarget.value.trim()); }
    });
  }

  function activateCustomView(name) {
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
    $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
    $("#page-eyebrow").textContent = name === "customers" ? "AMBIENTES MAPEADOS" : "LABORATÓRIO LOCAL";
    $("#page-title").textContent = name === "customers" ? "Clientes e topologias" : "Replay no WSL";
    if (name === "customers") void loadCustomers();
    if (name === "replay") void loadReplayScenarios();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function wizardMarkup() {
    return `<div class="investigation-wizard" id="investigation-wizard">
      <div class="wizard-progress"><i></i></div>
      <div class="wizard-nav" role="tablist">
        <button type="button" data-wizard-step="0"><b>1</b><span>Cliente e alerta</span></button>
        <button type="button" data-wizard-step="1"><b>2</b><span>Escopo e rota</span></button>
        <button type="button" data-wizard-step="2"><b>3</b><span>Estratégia</span></button>
        <button type="button" data-wizard-step="3"><b>4</b><span>Revisão</span></button>
      </div>
      <div class="wizard-panels"></div>
      <div class="wizard-footer"><button type="button" class="ghost-button" id="wizard-back">Voltar</button><span id="wizard-validation"></span><button type="button" class="primary-button" id="wizard-next">Continuar</button></div>
    </div>`;
  }

  function classifyFormChild(element) {
    if (element.id === "multi-host-scope") return 1;
    if (element.querySelector?.("#target") || element.querySelector?.("#objective") || element.id?.startsWith("batch-")) return 0;
    if (element.querySelector?.('button[type="submit"]')) return 3;
    return 2;
  }

  function setupWizard() {
    const form = $("#analysis-form");
    if (!form || $("#investigation-wizard")) return;
    const originalChildren = [...form.children];
    form.insertAdjacentHTML("afterbegin", wizardMarkup());
    const shell = $("#investigation-wizard");
    const container = shell.querySelector(".wizard-panels");
    wizardPanels = [0, 1, 2, 3].map((index) => {
      const panel = document.createElement("section");
      panel.className = "wizard-panel";
      panel.dataset.step = String(index);
      container.appendChild(panel);
      return panel;
    });
    originalChildren.forEach((child) => wizardPanels[classifyFormChild(child)].appendChild(child));

    const objective = $("#objective")?.closest("label");
    if (objective && objective.parentElement !== wizardPanels[0]) wizardPanels[0].appendChild(objective);
    const multiScope = $("#multi-host-scope");
    if (multiScope && multiScope.parentElement !== wizardPanels[1]) wizardPanels[1].appendChild(multiScope);

    wizardPanels[0].insertAdjacentHTML("afterbegin", '<div class="wizard-step-heading"><p class="eyebrow">ETAPA 1</p><h3>Qual cliente ou alerta será analisado?</h3><p>Informe o alvo inicial e descreva o sintoma sem tentar antecipar a causa.</p></div>');
    wizardPanels[1].insertAdjacentHTML("afterbegin", '<div class="wizard-step-heading"><p class="eyebrow">ETAPA 2</p><h3>Defina o caminho e os hosts relacionados</h3><p>O alvo principal será o servidor de entrada. A IA só consultará hosts da mesma empresa.</p></div>');
    wizardPanels[2].insertAdjacentHTML("afterbegin", '<div class="wizard-step-heading"><p class="eyebrow">ETAPA 3</p><h3>Escolha a estratégia</h3><p>As opções recomendadas já vêm selecionadas. Abra detalhes apenas quando precisar.</p></div><div class="wizard-recommended"><span>Recomendado</span><strong>IA automática · playbook automático · somente leitura</strong></div>');
    wizardPanels[3].insertAdjacentHTML("afterbegin", '<div class="wizard-step-heading"><p class="eyebrow">ETAPA 4</p><h3>Revise antes de iniciar</h3><p>Confira o host alertado, a rota e as proteções que serão aplicadas.</p></div><div class="wizard-review" id="wizard-review"></div>');

    shell.querySelectorAll("[data-wizard-step]").forEach((button) => button.addEventListener("click", () => showWizardStep(Number(button.dataset.wizardStep))));
    $("#wizard-back")?.addEventListener("click", () => showWizardStep(currentWizardStep - 1));
    $("#wizard-next")?.addEventListener("click", () => {
      if (!validateWizardStep(currentWizardStep)) return;
      showWizardStep(currentWizardStep + 1);
    });
    form.addEventListener("input", updateWizardReview);
    form.addEventListener("change", updateWizardReview);
    $$('[data-open-analysis]').forEach((button) => button.addEventListener("click", () => showWizardStep(0)));
    showWizardStep(0);
  }

  function validateWizardStep(step) {
    const message = $("#wizard-validation");
    message.textContent = "";
    if (step === 0) {
      if (!$("#target")?.value.trim()) { message.textContent = "Informe o alvo inicial."; return false; }
      if (($("#objective")?.value.trim().length || 0) < 3) { message.textContent = "Descreva o alerta ou objetivo."; return false; }
    }
    if (step === 1 && $("#multi-host-enabled")?.checked) {
      const customer = $("#multi-host-customer")?.value.trim();
      const hosts = $$("[data-related-reference]").filter((item) => item.value.trim());
      if (!customer) { message.textContent = "Informe a empresa para isolar os IPs internos."; return false; }
      if (!hosts.length) { message.textContent = "Adicione ao menos um host relacionado."; return false; }
    }
    return true;
  }

  function showWizardStep(step) {
    if (!wizardPanels.length) return;
    currentWizardStep = Math.max(0, Math.min(3, Number(step) || 0));
    wizardPanels.forEach((panel, index) => panel.classList.toggle("active", index === currentWizardStep));
    $$("[data-wizard-step]", $("#investigation-wizard")).forEach((button, index) => {
      button.classList.toggle("active", index === currentWizardStep);
      button.classList.toggle("completed", index < currentWizardStep);
    });
    $("#investigation-wizard .wizard-progress i").style.width = `${(currentWizardStep / 3) * 100}%`;
    $("#wizard-back").hidden = currentWizardStep === 0;
    $("#wizard-next").hidden = currentWizardStep === 3;
    $("#wizard-validation").textContent = "";
    if (currentWizardStep === 3) updateWizardReview();
  }

  function relatedTargets() {
    return $$(".multi-host-target").map((row) => ({
      address: row.querySelector("[data-related-reference]")?.value.trim(),
      label: row.querySelector("[data-related-label]")?.value.trim(),
      role: row.querySelector("[data-related-role]")?.value,
      environment: row.querySelector("[data-related-environment]")?.value,
      port: row.querySelector("[data-related-port]")?.value || "22",
    })).filter((item) => item.address);
  }

  function updateWizardReview() {
    const review = $("#wizard-review");
    if (!review) return;
    const target = $("#target")?.value.trim() || "Não informado";
    const objective = $("#objective")?.value.trim() || "Não informado";
    const environment = $("#environment")?.value || "unknown";
    const provider = $("#provider")?.selectedOptions?.[0]?.textContent || "IA automática";
    const playbookMode = $("#playbook-mode")?.value || "auto";
    const multi = Boolean($("#multi-host-enabled")?.checked);
    const customer = $("#multi-host-customer")?.value.trim() || "Cliente não identificado";
    const hosts = relatedTargets();
    const route = ["Monitor 1", target, ...hosts.map((item) => item.label || item.address)];
    review.innerHTML = `<div class="wizard-review-grid">
      <article><span>Cliente</span><strong>${escapeHtml(multi ? customer : "Resolvido pelo inventário")}</strong><small>${escapeHtml(objective)}</small></article>
      <article><span>Host alertado</span><strong>${escapeHtml(target)}</strong><small>${escapeHtml(environmentLabels[environment] || environment)}</small></article>
      <article><span>Estratégia</span><strong>${escapeHtml(provider)}</strong><small>Playbook ${escapeHtml(playbookMode === "manual" ? "manual" : playbookMode === "none" ? "desativado" : "automático")}</small></article>
    </div>
    <div class="wizard-route-review"><b>Caminho previsto</b>${route.map((item, index) => `${index ? "<i>→</i>" : ""}<span>${escapeHtml(item)}</span>`).join("")}</div>
    <div class="wizard-safety-review"><span>✓ Produção e standby somente leitura</span><span>✓ Banco de cliente bloqueado</span><span>✓ Correção exige revisão humana</span><span>✓ Máximo de hosts e comandos controlado</span></div>`;
  }

  function favoriteScopes() { return storage(FAVORITES_KEY, []); }
  function recentScopes() { return storage(RECENTS_KEY, []); }

  function saveRecentScope(scope) {
    const rows = recentScopes().filter((item) => item.key !== scope.key);
    rows.unshift(scope);
    persist(RECENTS_KEY, rows.slice(0, MAX_RECENTS));
  }

  function toggleFavorite(scope) {
    const rows = favoriteScopes();
    const existing = rows.findIndex((item) => item.key === scope.key);
    if (existing >= 0) rows.splice(existing, 1);
    else rows.unshift(scope);
    persist(FAVORITES_KEY, rows.slice(0, 20));
    renderFavorites();
    toast(existing >= 0 ? "Escopo removido dos favoritos." : "Escopo salvo nos favoritos.");
  }

  function renderFavorites() {
    const holder = $("#favorite-scopes");
    if (!holder) return;
    const favorites = favoriteScopes();
    const recents = recentScopes();
    if (!favorites.length && !recents.length) { holder.innerHTML = ""; return; }
    holder.innerHTML = `<div class="scope-shortcuts"><div><h4>Favoritos</h4>${favorites.length ? favorites.slice(0, 5).map((item) => `<button type="button" data-scope='${escapeHtml(JSON.stringify(item))}'>★ ${escapeHtml(item.label || item.target)}</button>`).join("") : "<span>Nenhum favorito</span>"}</div><div><h4>Recentes</h4>${recents.length ? recents.slice(0, 5).map((item) => `<button type="button" data-scope='${escapeHtml(JSON.stringify(item))}'>↻ ${escapeHtml(item.label || item.target)}</button>`).join("") : "<span>Nenhum escopo recente</span>"}</div></div>`;
    holder.querySelectorAll("[data-scope]").forEach((button) => button.addEventListener("click", () => {
      try { applyScope(JSON.parse(button.dataset.scope)); } catch { toast("Não foi possível restaurar o escopo.", "error"); }
    }));
  }

  function currentScope(result = null) {
    const target = result?.target || $("#target")?.value.trim() || "";
    const objective = result?.analysis?.summary || $("#objective")?.value.trim() || "";
    const environment = result?.environment || $("#environment")?.value || "unknown";
    const customer = result?.multi_host?.customer?.name || $("#multi-host-customer")?.value.trim() || "";
    const related = result?.multi_host?.hosts?.slice(1).map((item) => ({ address: item.address, label: item.label, role: item.role, environment: item.environment, ssh_port: 22 })) || relatedTargets().map((item) => ({ ...item, ssh_port: Number(item.port || 22) }));
    return {
      key: `${customer}|${target}|${environment}`.toLowerCase(),
      label: customer || result?.display_target || target,
      target,
      objective,
      environment,
      customer,
      related,
      saved_at: new Date().toISOString(),
    };
  }

  function applyScope(scope) {
    $("#topbar-start-investigation")?.click();
    setTimeout(() => {
      $("#target").value = scope.target || "";
      $("#objective").value = scope.objective || "";
      $("#environment").value = scope.environment || "unknown";
      if (scope.customer || safeArray(scope.related).length) {
        $("#multi-host-enabled").checked = true;
        $("#multi-host-enabled").dispatchEvent(new Event("change"));
        $("#multi-host-customer").value = scope.customer || "";
        const list = $("#multi-host-target-list");
        if (list) list.innerHTML = "";
        safeArray(scope.related).slice(0, 3).forEach((item) => {
          $("#multi-host-add")?.click();
          const row = list?.lastElementChild;
          if (!row) return;
          row.querySelector("[data-related-reference]").value = item.address || item.reference || "";
          row.querySelector("[data-related-label]").value = item.label || "";
          row.querySelector("[data-related-role]").value = item.role || "other";
          row.querySelector("[data-related-environment]").value = item.environment || "unknown";
          row.querySelector("[data-related-port]").value = item.ssh_port || 22;
        });
      }
      showWizardStep(0);
      updateWizardReview();
    }, 50);
  }

  async function loadCustomers(query = "") {
    const grid = $("#customer-grid");
    if (!grid) return;
    grid.innerHTML = '<div class="empty-state">Carregando topologias...</div>';
    try {
      const data = await api(`/ui/api/customers?limit=100${query ? `&query=${encodeURIComponent(query)}` : ""}`);
      customerCache = data.items || [];
      renderCustomers(customerCache);
      renderFavorites();
    } catch (error) {
      grid.innerHTML = `<div class="empty-state error-state">${escapeHtml(error.message)}</div>`;
    }
  }

  function routeState(route) {
    if (!route.enabled) return ["disabled", "Desabilitada"];
    if (route.last_verified_at) return ["verified", `Validada ${formatDate(route.last_verified_at)}`];
    return ["unverified", "Ainda não validada"];
  }

  function renderCustomers(customers) {
    const grid = $("#customer-grid");
    if (!grid) return;
    if (!customers.length) { grid.innerHTML = '<div class="empty-state">Nenhum cliente mapeado. Uma topologia será criada ao executar uma investigação multi-host.</div>'; return; }
    grid.innerHTML = customers.map((customer) => {
      const nodes = safeArray(customer.nodes);
      const routes = safeArray(customer.routes);
      const nodeById = new Map(nodes.map((node) => [node.id, node]));
      const favorite = favoriteScopes().some((item) => item.customer === customer.name);
      const entry = customer.entry_node || nodes.find((node) => node.direct_vpn);
      const graph = nodes.map((node) => {
        const inbound = routes.find((route) => route.destination_node_id === node.id);
        const source = inbound ? nodeById.get(inbound.source_node_id) : null;
        const [routeClass, routeLabel] = inbound ? routeState(inbound) : [node.direct_vpn ? "direct" : "unmapped", node.direct_vpn ? "VPN direta" : "Sem rota"];
        return `<article class="customer-node" data-role="${escapeHtml(node.role)}"><div class="node-role">${escapeHtml(roleLabels[node.role] || node.role)}</div><strong>${escapeHtml(node.label || node.hostname || node.address)}</strong><code>${escapeHtml(node.address)}:${escapeHtml(node.ssh_port)}</code><small>${node.direct_vpn ? "Monitor 1 → host" : `${escapeHtml(source?.label || source?.hostname || source?.address || "entrada")} → SSH interno`}</small><span class="route-state ${routeClass}">${escapeHtml(routeLabel)}</span></article>`;
      }).join("");
      const scope = {
        key: `${customer.name}|${entry?.address || ""}|monitoring`.toLowerCase(),
        label: customer.name,
        target: entry?.address || "",
        objective: `Investigar o ambiente da empresa ${customer.name}.`,
        environment: entry?.environment || "monitoring",
        customer: customer.name,
        related: nodes.filter((node) => node.id !== entry?.id).slice(0, 3),
      };
      return `<section class="customer-card" data-customer-id="${escapeHtml(customer.id)}"><header><div><p class="eyebrow">${escapeHtml(customer.nodes_count)} HOST(S) · ${escapeHtml(customer.routes_count)} ROTA(S)</p><h3>${escapeHtml(customer.name)}</h3><span>${escapeHtml(customer.verified_routes_count)} rota(s) validada(s)</span></div><div class="customer-actions"><button type="button" class="icon-button ${favorite ? "favorite-active" : ""}" data-favorite-customer title="Favoritar">★</button><button type="button" class="secondary-button" data-investigate-customer>Investigar</button></div></header><div class="customer-route-origin"><span>Monitor 1</span><i>→</i><span>${escapeHtml(entry?.label || entry?.hostname || entry?.address || "entrada não definida")}</span></div><div class="customer-node-grid">${graph}</div><footer><span>Produção e standby: somente leitura</span><span>IPs internos isolados por empresa</span></footer><script type="application/json" data-customer-scope>${JSON.stringify(scope).replaceAll("<", "\\u003c")}</script></section>`;
    }).join("");
    grid.querySelectorAll(".customer-card").forEach((card) => {
      const scope = JSON.parse(card.querySelector("[data-customer-scope]").textContent);
      card.querySelector("[data-investigate-customer]").addEventListener("click", () => { saveRecentScope(scope); applyScope(scope); });
      card.querySelector("[data-favorite-customer]").addEventListener("click", () => toggleFavorite(scope));
    });
  }

  async function loadReplayScenarios() {
    const grid = $("#replay-grid");
    if (!grid || grid.dataset.loaded === "1") return;
    try {
      const data = await api("/ui/api/replay/scenarios");
      grid.dataset.loaded = "1";
      grid.innerHTML = safeArray(data.items).map((item) => `<article class="replay-card"><div class="replay-card-icon">▷</div><div><p class="eyebrow">${escapeHtml(item.category)}</p><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.description)}</p><dl><div><dt>Cliente</dt><dd>${escapeHtml(item.customer)}</dd></div><div><dt>Ambiente</dt><dd>${escapeHtml(environmentLabels[item.environment] || item.environment)}</dd></div><div><dt>Duração simulada</dt><dd>${escapeHtml(item.duration_seconds)}s</dd></div></dl></div><button type="button" class="primary-button" data-run-replay="${escapeHtml(item.id)}">Executar demonstração</button></article>`).join("");
      grid.querySelectorAll("[data-run-replay]").forEach((button) => button.addEventListener("click", () => startReplay(button.dataset.runReplay)));
    } catch (error) {
      grid.innerHTML = `<div class="empty-state error-state">${escapeHtml(error.message)}</div>`;
    }
  }

  async function startReplay(scenarioId) {
    const consolePanel = $("#replay-console");
    consolePanel.hidden = false;
    consolePanel.innerHTML = '<div class="replay-running"><span class="execution-tray-spinner"></span><div><strong>Preparando replay...</strong><p>Nenhuma conexão real será aberta.</p></div></div>';
    replayStream?.close();
    try {
      const record = await api(`/ui/api/replay/${encodeURIComponent(scenarioId)}`, { method: "POST", body: { speed: 1.4 } });
      renderReplayRecord(record);
      replayStream = new EventSource(`/ui/api/executions/${encodeURIComponent(record.execution_id)}/events`);
      replayStream.addEventListener("progress", (message) => {
        try { renderReplayEvent(JSON.parse(message.data)); } catch { /* evento inválido */ }
      });
      replayStream.addEventListener("snapshot", (message) => {
        try {
          const snapshot = JSON.parse(message.data);
          renderReplayRecord(snapshot);
          if (["completed", "failed", "cancelled"].includes(snapshot.status)) replayStream.close();
        } catch { /* snapshot inválido */ }
      });
      replayStream.onerror = () => replayStream?.close();
    } catch (error) {
      consolePanel.innerHTML = `<div class="empty-state error-state">${escapeHtml(error.message)}</div>`;
    }
  }

  function renderReplayEvent(event) {
    const panel = $("#replay-console");
    if (!panel) return;
    let log = panel.querySelector(".replay-event-log");
    if (!log) {
      panel.innerHTML = `<div class="replay-progress-head"><strong>Replay em andamento</strong><span>0%</span></div><div class="replay-progress-bar"><i></i></div><div class="replay-event-log"></div>`;
      log = panel.querySelector(".replay-event-log");
    }
    panel.querySelector(".replay-progress-head span").textContent = `${Math.round(Number(event.percent || 0))}%`;
    panel.querySelector(".replay-progress-bar i").style.width = `${Math.round(Number(event.percent || 0))}%`;
    const row = document.createElement("div");
    row.innerHTML = `<span>${escapeHtml(event.host || event.to_host || "Agent")}</span><strong>${escapeHtml(event.detail || event.stage)}</strong>${event.stdout_tail ? `<pre>${escapeHtml(event.stdout_tail)}</pre>` : ""}`;
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }

  function renderReplayRecord(record) {
    const panel = $("#replay-console");
    if (!panel) return;
    if (record.status === "completed" && record.result) {
      replayStream?.close();
      panel.innerHTML = `<div class="replay-complete"><div><span>✓</span><strong>Demonstração concluída</strong><p>O resultado usa somente dados sanitizados.</p></div><button class="primary-button" type="button" id="open-replay-result">Abrir resultado completo</button></div>`;
      $("#open-replay-result")?.addEventListener("click", () => showResult(record.result));
      saveRecentScope(currentScope(record.result));
    } else if (record.status === "failed") {
      panel.innerHTML = `<div class="empty-state error-state">${escapeHtml(record.error || "Replay falhou.")}</div>`;
    }
  }

  function evidenceMarkup(result) {
    const evidence = safeArray(result.evidence);
    if (!evidence.length) return '<div class="empty-state">Nenhuma evidência estruturada disponível.</div>';
    const grouped = new Map();
    evidence.forEach((item) => {
      const host = item.source_hostname || item.source_host || item.host || result.hostname || result.target || "Host não identificado";
      if (!grouped.has(host)) grouped.set(host, []);
      grouped.get(host).push(item);
    });
    return [...grouped.entries()].map(([host, rows]) => `<section class="evidence-host-group"><header><h4>${escapeHtml(host)}</h4><span>${rows.length} evidência(s)</span></header>${rows.map((item) => `<article><div><strong>${escapeHtml(item.tool || item.command || "Coleta")}</strong><span class="status-badge ${item.status === "executed" || item.exit_code === 0 ? "healthy" : "attention"}">${escapeHtml(item.status || `código ${item.exit_code}`)}</span></div>${item.purpose ? `<p>${escapeHtml(item.purpose)}</p>` : ""}${item.stdout ? `<pre>${escapeHtml(String(item.stdout).slice(-5000))}</pre>` : item.normalized ? `<pre>${escapeHtml(JSON.stringify(item.normalized, null, 2).slice(-5000))}</pre>` : ""}</article>`).join("")}</section>`).join("");
  }

  function investigationPathMarkup(result) {
    const multi = result.multi_host || result.analysis?.multi_host || {};
    const hosts = safeArray(multi.hosts);
    const handoffs = safeArray(multi.handoffs);
    const alertHost = result.hostname || result.target;
    const causeHost = multi.root_host || result.analysis?.root_host || alertHost;
    const correctionHost = result.analysis?.corrective_target || (result.environment === "monitoring" ? causeHost : "Exige revisão e seleção manual");
    const triad = `<div class="host-triad"><article><span>Host do alerta</span><strong>${escapeHtml(alertHost)}</strong><small>Origem do sintoma</small></article><article><span>Host da causa provável</span><strong>${escapeHtml(causeHost)}</strong><small>Maior sustentação de evidências</small></article><article><span>Host de eventual correção</span><strong>${escapeHtml(correctionHost)}</strong><small>Nunca definido automaticamente em multi-host</small></article></div>`;
    const route = hosts.length ? `<div class="result-route-map"><span>Monitor 1</span><i>→</i><span>${escapeHtml(multi.entry_host?.label || multi.entry_host?.hostname || multi.entry_host?.address || "entrada")}</span>${hosts.filter((host) => host.address !== multi.entry_host?.address).map((host) => `<i>→</i><span>${escapeHtml(host.label || host.hostname || host.address)}</span>`).join("")}</div>` : "";
    const hostTimeline = hosts.map((host) => `<article class="path-host-card"><header><div><span>${escapeHtml(roleLabels[host.role] || host.role || "Host")}</span><strong>${escapeHtml(host.label || host.hostname || host.address)}</strong></div><b>${escapeHtml(host.confidence || 0)}%</b></header><p>${escapeHtml(host.probable_cause || host.summary || "Sem causa específica confirmada.")}</p><small>${escapeHtml(host.status || "inconclusive")}</small></article>`).join("");
    const changes = handoffs.map((item) => `<article class="handoff-card ${escapeHtml(item.status || "pending")}"><div><strong>${escapeHtml(item.from)} → ${escapeHtml(item.to)}</strong><span>${escapeHtml(item.status || "pending")}</span></div><p>${escapeHtml(item.reason || "Host relacionado ao objetivo.")}</p>${item.error ? `<div class="handoff-recovery"><p>${escapeHtml(item.error)}</p><button type="button" data-retry-host="${escapeHtml(item.to)}">Tentar novamente em nova análise</button><button type="button" data-edit-host="${escapeHtml(item.to)}">Editar IP/porta</button><button type="button" data-skip-host>Ignorar neste resultado</button></div>` : ""}</article>`).join("");
    return `${triad}${route}<div class="path-host-grid">${hostTimeline}</div>${changes ? `<section class="handoff-list"><h4>Decisões de troca de host</h4>${changes}</section>` : '<div class="empty-state">A investigação permaneceu no host inicial.</div>'}`;
  }

  function communicationMarkup(result) {
    const analysis = result.analysis || {};
    const technical = result.ticket_report || result.operator_report || `${analysis.summary || ""}\n\nCausa provável: ${analysis.probable_cause || "não confirmada"}\nPróximo passo: ${analysis.next_safe_step || "revisar evidências"}`;
    const infra = `Descrição do Problema:\n${analysis.summary || "Análise concluída sem resumo."}\n\nAções já realizadas:\n${safeArray(analysis.facts).map((item) => `- ${item}`).join("\n") || "- Coleta técnica realizada."}\n\nMotivo da Transferência:\n${analysis.next_safe_step || "Necessária validação especializada antes de qualquer ação."}`;
    const client = `Identificamos ${analysis.status === "critical" ? "uma indisponibilidade" : "uma condição de atenção"} no ambiente monitorado. ${analysis.summary || "A análise técnica está em andamento."} ${analysis.next_safe_step ? `O próximo passo será ${String(analysis.next_safe_step).charAt(0).toLowerCase()}${String(analysis.next_safe_step).slice(1)}` : "Seguimos acompanhando o caso."}`;
    return `<div class="communication-grid"><article><header><div><span>Ticket</span><strong>Atualização técnica</strong></div><button type="button" data-copy-report="technical">Copiar</button></header><pre data-report="technical">${escapeHtml(technical)}</pre></article><article><header><div><span>Escalonamento</span><strong>Transferência para Infra</strong></div><button type="button" data-copy-report="infra">Copiar</button></header><pre data-report="infra">${escapeHtml(infra)}</pre></article><article><header><div><span>Cliente</span><strong>Mensagem simplificada</strong></div><button type="button" data-copy-report="client">Copiar</button></header><pre data-report="client">${escapeHtml(client)}</pre></article></div>`;
  }

  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const area = document.createElement("textarea");
      area.value = value;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    toast("Texto copiado.");
  }

  function enhanceResult(result) {
    const content = $("#result-content");
    if (!content || content.querySelector(".operator-result-tabs")) return;
    const original = [...content.children];
    const analysis = result.analysis || {};
    const scope = currentScope(result);
    saveRecentScope(scope);
    const wrapper = document.createElement("div");
    wrapper.className = "operator-result-tabs";
    wrapper.innerHTML = `<div class="result-quick-actions"><button type="button" class="secondary-button" data-save-result-scope>★ Salvar escopo</button><button type="button" class="secondary-button" data-repeat-result>↻ Repetir investigação</button>${result.replay?.enabled ? '<span class="replay-result-notice">Replay sanitizado · sem conexão real</span>' : ""}</div><nav><button type="button" class="active" data-result-tab="summary">Resumo</button><button type="button" data-result-tab="evidence">Evidências</button><button type="button" data-result-tab="path">Caminho da investigação</button><button type="button" data-result-tab="communication">Comunicação</button></nav><section class="result-tab active" data-result-panel="summary"></section><section class="result-tab" data-result-panel="evidence">${evidenceMarkup(result)}</section><section class="result-tab" data-result-panel="path">${investigationPathMarkup(result)}</section><section class="result-tab" data-result-panel="communication">${communicationMarkup(result)}</section>`;
    content.innerHTML = "";
    content.appendChild(wrapper);
    const summary = wrapper.querySelector('[data-result-panel="summary"]');
    original.forEach((node) => summary.appendChild(node));
    wrapper.querySelectorAll("[data-result-tab]").forEach((button) => button.addEventListener("click", () => {
      wrapper.querySelectorAll("[data-result-tab]").forEach((item) => item.classList.toggle("active", item === button));
      wrapper.querySelectorAll("[data-result-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.resultPanel === button.dataset.resultTab));
    }));
    wrapper.querySelector("[data-save-result-scope]").addEventListener("click", () => toggleFavorite(scope));
    wrapper.querySelector("[data-repeat-result]").addEventListener("click", () => applyScope(scope));
    wrapper.querySelectorAll("[data-copy-report]").forEach((button) => button.addEventListener("click", () => copyText(wrapper.querySelector(`[data-report="${button.dataset.copyReport}"]`).textContent)));
    wrapper.querySelectorAll("[data-retry-host]").forEach((button) => button.addEventListener("click", () => applyScope({ ...scope, target: button.dataset.retryHost, related: [] })));
    wrapper.querySelectorAll("[data-edit-host]").forEach((button) => button.addEventListener("click", () => applyScope({ ...scope, related: [{ address: button.dataset.editHost, role: "other", environment: "unknown", ssh_port: 22 }] })));
    wrapper.querySelectorAll("[data-skip-host]").forEach((button) => button.addEventListener("click", () => button.closest(".handoff-card")?.remove()));
  }

  function wrapResult() {
    const baseShowResult = showResult;
    showResult = function showOperatorResult(result) {
      const output = baseShowResult(result);
      enhanceResult(result || {});
      return output;
    };
  }

  async function loadObservabilityCard() {
    const health = $("#view-health .panel");
    if (!health || health.querySelector(".observability-card")) return;
    try {
      const data = await api("/ui/api/observability");
      health.insertAdjacentHTML("beforeend", `<section class="observability-card"><div><p class="eyebrow">PERFORMANCE DO AGENT</p><h3>Execução e limites</h3></div><div class="observability-grid"><article><span>Eventos</span><strong>${escapeHtml(data.sse_enabled ? "SSE em tempo real" : "Polling")}</strong><small>Store: ${escapeHtml(data.execution_store)}</small></article><article><span>Comandos</span><strong>${escapeHtml(data.budgets.commands)} por investigação</strong><small>Limite global</small></article><article><span>IA</span><strong>${escapeHtml(data.budgets.ai_calls)} chamadas</strong><small>Inclui crítico e síntese</small></article><article><span>Multi-host</span><strong>${escapeHtml(data.budgets.deep_dive_hosts)} deep dives</strong><small>Triagem nos demais</small></article></div></section>`);
    } catch { /* observabilidade pode estar protegida/desabilitada */ }
  }

  function bindHealthObservation() {
    document.querySelector('[data-view="health"]')?.addEventListener("click", () => setTimeout(loadObservabilityCard, 100));
  }

  function setup() {
    injectViews();
    setupWizard();
    wrapResult();
    renderFavorites();
    bindHealthObservation();
    window.AgentUI?.emit("operator-experience:ready", { version: "1.28.0" });
  }

  document.addEventListener("DOMContentLoaded", setup);
})();
