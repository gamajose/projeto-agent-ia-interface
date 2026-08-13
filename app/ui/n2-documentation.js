(() => {
  const state = {
    loaded: false,
    sites: [],
    context: null,
    siteId: "",
    playbooks: [],
    providers: [],
    plan: null,
    review: null,
    documentId: "",
    documents: [],
    executionIds: [],
    running: false,
    saveTimer: null,
    saving: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const delay = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  async function request(path, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const headers = { ...(options.headers || {}) };
    if (method !== "GET") headers["X-Agent-UI"] = "1";
    let body = options.body;
    if (body && typeof body === "object" && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, method, headers, body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  async function requestBlob(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Agent-UI": "1" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `Falha HTTP ${response.status}`);
    }
    return { blob: await response.blob(), disposition: response.headers.get("Content-Disposition") || "" };
  }

  function pageMarkup() {
    return `
      <div class="n2-page-head">
        <div>
          <p class="eyebrow">DOCUMENTAÇÃO OPERACIONAL</p>
          <h2>Documentação N2 com IA</h2>
          <p>Selecione cliente, responsáveis e exatamente quais hosts entram no documento. A IA coleta somente as evidências necessárias, você revisa e edita, e o resultado fica salvo no PostgreSQL para reabrir ou exportar depois.</p>
        </div>
        <div class="n2-safety-chip"><strong>Somente leitura</strong><span>Nunca reinicia servidor · não coleta credenciais</span></div>
      </div>

      <article class="panel n2-saved-card">
        <div class="n2-saved-head">
          <div><p class="eyebrow">DOCUMENTOS SALVOS</p><h3>Histórico N2</h3><p>Documentações ficam persistidas no banco do Agent IA.</p></div>
          <div class="n2-saved-actions"><input id="n2-document-search" placeholder="Buscar cliente ou documento"><button type="button" class="secondary-button" id="n2-new-document">Nova documentação</button></div>
        </div>
        <div class="n2-document-list" id="n2-document-list"><div class="empty-state">Carregando documentos salvos...</div></div>
      </article>

      <div class="n2-workflow-grid">
        <article class="panel n2-setup-card">
          <div class="n2-step-title"><span>1</span><div><p class="eyebrow">ESCOPO</p><h3>Cliente e responsáveis</h3></div></div>
          <label class="n2-field n2-client-field">
            <span>Cliente / site</span>
            <div class="n2-combobox" id="n2-client-combobox">
              <input id="n2-client-search" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="n2-client-options" autocomplete="off" placeholder="Digite o nome ou o site do cliente">
              <button type="button" class="n2-combo-toggle" id="n2-client-toggle" aria-label="Abrir lista de clientes">⌄</button>
              <div class="n2-client-options" id="n2-client-options" role="listbox" hidden></div>
            </div>
            <small>Digite para pesquisar. A lista usa fundo claro e texto legível.</small>
          </label>
          <div class="n2-responsibles">
            <label class="n2-field"><span>Responsável Infra</span><input id="n2-resp-infra" placeholder="Nome"></label>
            <label class="n2-field"><span>Responsável DBA</span><input id="n2-resp-dba" placeholder="Nome"></label>
            <label class="n2-field"><span>Responsável NOC</span><input id="n2-resp-noc" placeholder="Nome"></label>
            <label class="n2-field"><span>Revisão</span><input id="n2-resp-review" placeholder="Nome"></label>
            <label class="n2-field"><span>Revisão NOC</span><input id="n2-resp-review-noc" placeholder="Nome"></label>
          </div>
          <label class="n2-field">
            <span>Playbook para auxiliar a IA <em>opcional</em></span>
            <select id="n2-playbook"><option value="">Automático — a IA decide o roteiro de validação</option></select>
            <small>Com playbook, ele orienta a coleta. Sem playbook, a IA monta a validação do zero conforme o padrão documental.</small>
          </label>
        </article>

        <article class="panel n2-host-card">
          <div class="n2-step-title"><span>2</span><div><p class="eyebrow">HOSTS</p><h3>Quais servidores entram na documentação?</h3></div></div>
          <div class="n2-host-toolbar"><div id="n2-host-summary">Selecione um cliente para carregar os hosts.</div><div><button type="button" class="ghost-button" id="n2-select-all-hosts">Selecionar todos</button><button type="button" class="ghost-button" id="n2-clear-hosts">Limpar</button></div></div>
          <div class="n2-host-list" id="n2-host-list"><div class="empty-state">Nenhum cliente selecionado.</div></div>
        </article>
      </div>

      <article class="panel n2-run-card">
        <div class="n2-step-title"><span>3</span><div><p class="eyebrow">AUTOMAÇÃO</p><h3>Executar validação documental</h3></div></div>
        <div class="n2-run-copy"><p>A IA acessará somente os hosts marcados e procurará inventário, hardware/SO, Oracle/TOTVS, TNSNAMES, Winthor, backup, retenção, redundância/standby e monitoramento quando observáveis com segurança.</p><p>Sem evidência, o campo permanece em branco para você completar na revisão. Nenhuma senha é coletada ou exportada.</p></div>
        <div class="n2-run-actions"><button type="button" class="primary-button" id="n2-run-validation">Executar validação N2 com IA</button><span id="n2-run-status"></span></div>
        <div class="n2-progress" id="n2-progress" hidden></div>
      </article>

      <article class="panel n2-review-card" id="n2-review-card" hidden>
        <div class="n2-review-head">
          <div class="n2-step-title"><span>4</span><div><p class="eyebrow">REVISÃO</p><h3>Revisar e editar a documentação</h3></div></div>
          <div class="n2-review-state" id="n2-review-state">Pronto para revisão</div>
        </div>
        <div class="n2-review-note">Todos os campos não sensíveis são editáveis. As alterações são salvas automaticamente no PostgreSQL.</div>
        <div id="n2-review-content"></div>
        <div class="n2-export-bar"><div><strong>Documento revisado</strong><span>Exporte no padrão do template N2 enviado, com campos sensíveis em branco.</span></div><div><button type="button" class="secondary-button" id="n2-save-document">Salvar agora</button><button type="button" class="secondary-button" id="n2-export-word">Exportar Word</button><button type="button" class="primary-button" id="n2-export-pdf">Exportar PDF</button></div></div>
      </article>`;
  }

  function ensureShell() {
    const nav = $(".nav");
    const main = $("main.main");
    if (!nav || !main) return false;

    let button = $('.nav-item[data-view="n2"]');
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "nav-item";
      button.dataset.view = "n2";
      button.innerHTML = '<span class="nav-icon">▤</span><span>N2</span>';
      const projects = $('.nav-item[data-view="projects"]');
      if (projects) projects.insertAdjacentElement("afterend", button);
      else nav.appendChild(button);
    }
    button.title = "Documentação N2 com IA";
    button.setAttribute("aria-label", "N2 - Documentação operacional");

    let view = $("#view-n2");
    if (!view) {
      view = document.createElement("section");
      view.id = "view-n2";
      const opencode = $("#view-opencode");
      if (opencode) opencode.insertAdjacentElement("beforebegin", view);
      else main.appendChild(view);
    }
    view.classList.add("view", "n2-page");
    if (view.dataset.n2Ready !== "1") {
      view.innerHTML = pageMarkup();
      view.dataset.n2Ready = "1";
      bindPageEvents(view);
    }

    if (button.dataset.n2Bound !== "1") {
      button.dataset.n2Bound = "1";
      button.addEventListener("click", activateN2);
    }
    return true;
  }

  function activateN2() {
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === "n2"));
    $$(".view").forEach((item) => item.classList.toggle("active", item.id === "view-n2"));
    if ($("#page-eyebrow")) $("#page-eyebrow").textContent = "OPERAÇÃO N2";
    if ($("#page-title")) $("#page-title").textContent = "Documentação N2";
    void loadBaseData();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function bindPageEvents(view) {
    const search = $("#n2-client-search", view);
    search?.addEventListener("focus", openClientOptions);
    search?.addEventListener("input", () => {
      state.siteId = "";
      state.context = null;
      renderClientOptions(search.value);
    });
    search?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeClientOptions();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        $("#n2-client-options [role=option]", view)?.focus();
      }
    });
    $("#n2-client-toggle", view)?.addEventListener("click", () => {
      const options = $("#n2-client-options", view);
      if (options?.hidden) openClientOptions(); else closeClientOptions();
    });
    $("#n2-select-all-hosts", view)?.addEventListener("click", () => setAllHosts(true));
    $("#n2-clear-hosts", view)?.addEventListener("click", () => setAllHosts(false));
    $("#n2-run-validation", view)?.addEventListener("click", () => void runValidation());
    $("#n2-save-document", view)?.addEventListener("click", () => void saveReview("reviewed", true));
    $("#n2-export-word", view)?.addEventListener("click", () => void exportReview("docx"));
    $("#n2-export-pdf", view)?.addEventListener("click", () => void exportReview("pdf"));
    $("#n2-new-document", view)?.addEventListener("click", newDocument);
    $("#n2-document-search", view)?.addEventListener("input", renderDocuments);
    $$("#n2-resp-infra,#n2-resp-dba,#n2-resp-noc,#n2-resp-review,#n2-resp-review-noc", view).forEach((input) => input.addEventListener("input", () => {
      if (state.review) reviewChanged();
    }));
    document.addEventListener("click", (event) => {
      const combo = $("#n2-client-combobox");
      if (combo && !combo.contains(event.target)) closeClientOptions();
    });
  }

  async function loadBaseData() {
    if (state.loaded) {
      await loadDocuments(state.siteId || "");
      return;
    }
    state.loaded = true;
    try {
      const [siteData, playbookData, providerData, documentData] = await Promise.all([
        request("/ui/api/n2/sites?limit=1000"),
        request("/ui/api/playbooks").catch(() => ({ items: [] })),
        request("/ui/api/ai/providers").catch(() => ({ items: [] })),
        request("/ui/api/n2/documents?limit=100").catch(() => ({ items: [] })),
      ]);
      state.sites = siteData.items || [];
      state.playbooks = playbookData.items || playbookData.playbooks || [];
      state.providers = normalizeProviders(providerData);
      state.documents = documentData.items || [];
      renderPlaybooks();
      renderClientOptions("");
      closeClientOptions();
      renderDocuments();
    } catch (error) {
      state.loaded = false;
      setRunStatus(error.message, true);
    }
  }

  function normalizeProviders(data) {
    const rows = data.items || data.providers || data.options || [];
    if (Array.isArray(rows)) return rows;
    if (rows && typeof rows === "object") return Object.entries(rows).map(([id, value]) => ({ id, ...(typeof value === "object" ? value : { label: value }) }));
    return [];
  }

  function renderPlaybooks() {
    const select = $("#n2-playbook");
    if (!select) return;
    const items = state.playbooks.filter((item) => item && (item.id || item.playbook_id || item.name));
    select.innerHTML = '<option value="">Automático — a IA decide o roteiro de validação</option>' + items.map((item) => {
      const id = item.id || item.playbook_id || item.name;
      const label = item.title || item.label || item.name || id;
      return `<option value="${esc(id)}">${esc(label)}</option>`;
    }).join("");
  }

  function renderClientOptions(query = "") {
    const root = $("#n2-client-options");
    const search = $("#n2-client-search");
    if (!root || !search) return;
    const normalized = String(query || "").trim().toLocaleLowerCase("pt-BR");
    const items = state.sites
      .filter((site) => !normalized || `${site.alias || ""} ${site.site_id || ""}`.toLocaleLowerCase("pt-BR").includes(normalized))
      .slice(0, 150);
    root.innerHTML = items.length ? items.map((site) => `
      <button type="button" role="option" class="n2-client-option" data-site-id="${esc(site.site_id)}" aria-selected="${site.site_id === state.siteId ? "true" : "false"}">
        <strong>${esc(site.alias || site.site_id)}</strong>
        <span>${esc(site.site_id)} · ${esc(site.hosts ?? 0)} host(s) · ${esc(site.problems ?? 0)} problema(s)</span>
      </button>`).join("") : '<div class="n2-no-options">Nenhum cliente encontrado.</div>';
    $$("[data-site-id]", root).forEach((button) => button.addEventListener("click", () => void selectSite(button.dataset.siteId)));
    root.hidden = false;
    search.setAttribute("aria-expanded", "true");
  }

  function openClientOptions() { renderClientOptions($("#n2-client-search")?.value || ""); }
  function closeClientOptions() {
    const root = $("#n2-client-options");
    const search = $("#n2-client-search");
    if (root) root.hidden = true;
    search?.setAttribute("aria-expanded", "false");
  }

  async function selectSite(siteId, options = {}) {
    const site = state.sites.find((item) => item.site_id === siteId);
    if (!site) throw new Error(`Cliente/site ${siteId} não encontrado na base N2.`);
    state.siteId = siteId;
    if (!options.keepDocument) {
      state.documentId = "";
      state.review = null;
      hideReview();
    }
    const search = $("#n2-client-search");
    if (search) search.value = `${site.alias || site.site_id} · ${site.site_id}`;
    closeClientOptions();
    const hostList = $("#n2-host-list");
    if (hostList) hostList.innerHTML = '<div class="empty-state">Carregando hosts do cliente...</div>';
    try {
      state.context = await request(`/ui/api/n2/sites/${encodeURIComponent(siteId)}`);
      state.plan = null;
      renderHosts();
      await loadDocuments(siteId);
    } catch (error) {
      state.context = null;
      if (hostList) hostList.innerHTML = `<div class="empty-state error-state">${esc(error.message)}</div>`;
      throw error;
    }
  }

  function renderHosts() {
    const root = $("#n2-host-list");
    const summary = $("#n2-host-summary");
    const hosts = state.context?.hosts || [];
    if (!root || !summary) return;
    summary.textContent = `${hosts.length} host(s) encontrados no site ${state.context?.site?.site_id || ""}. Marque somente os servidores que devem entrar no documento.`;
    if (!hosts.length) {
      root.innerHTML = '<div class="empty-state">Nenhum host disponível neste cliente.</div>';
      return;
    }
    root.innerHTML = hosts.map((host) => {
      const problems = (state.context?.problems || []).filter((item) => item.host === host.host).length;
      return `<label class="n2-host-row"><input type="checkbox" data-n2-host-select value="${esc(host.host)}"><span class="n2-host-check" aria-hidden="true"></span><span class="n2-host-name"><strong>${esc(host.host)}</strong><small>${esc(host.ip || "sem IP")}</small></span><span class="n2-host-role">${esc(host.kind || host.environment || "server")}</span><span class="n2-host-problems">${esc(problems)} problema(s)</span></label>`;
    }).join("");
  }

  function setAllHosts(value) { $$("[data-n2-host-select]").forEach((input) => { input.checked = value; }); }
  function setSelectedHosts(hosts) {
    const wanted = new Set((hosts || []).map(String));
    $$("[data-n2-host-select]").forEach((input) => { input.checked = wanted.has(input.value); });
  }
  function selectedHostNames() { return $$("[data-n2-host-select]:checked").map((input) => input.value); }
  function responsibles() {
    return {
      infra: $("#n2-resp-infra")?.value || "",
      dba: $("#n2-resp-dba")?.value || "",
      noc: $("#n2-resp-noc")?.value || "",
      review: $("#n2-resp-review")?.value || "",
      review_noc: $("#n2-resp-review-noc")?.value || "",
    };
  }

  function setResponsibleInputs(resp = {}) {
    const fields = [["#n2-resp-infra", "infra"], ["#n2-resp-dba", "dba"], ["#n2-resp-noc", "noc"], ["#n2-resp-review", "review"], ["#n2-resp-review-noc", "review_noc"]];
    fields.forEach(([selector, key]) => { const input = $(selector); if (input) input.value = resp[key] || ""; });
  }

  function concreteProvider() {
    const preferred = state.providers.find((item) => {
      const id = item.id || item.provider || item.name;
      return id && String(id).toLowerCase() !== "auto" && item.selected === true && item.configured !== false && item.available !== false && item.selectable !== false;
    });
    const fallback = state.providers.find((item) => {
      const id = item.id || item.provider || item.name;
      return id && String(id).toLowerCase() !== "auto" && item.configured !== false && item.available !== false && item.enabled !== false && item.selectable !== false;
    });
    return String((preferred || fallback || {}).id || (preferred || fallback || {}).provider || (preferred || fallback || {}).name || "gemini");
  }

  async function runValidation() {
    if (state.running) return;
    if (!state.siteId || !state.context) {
      setRunStatus("Selecione o cliente antes de executar.", true);
      return;
    }
    const hosts = selectedHostNames();
    if (!hosts.length) {
      setRunStatus("Selecione pelo menos um host para a documentação.", true);
      return;
    }

    state.running = true;
    state.executionIds = [];
    state.review = null;
    state.documentId = "";
    hideReview();
    toggleRunButton(true);
    setRunStatus("Montando o plano de validação...", false);
    try {
      state.plan = await request("/ui/api/n2/plan", { method: "POST", body: { site_id: state.siteId, host_names: hosts } });
      const batches = state.plan.batches || [];
      renderProgress(batches, 0, "Preparando automação...");
      const playbookId = $("#n2-playbook")?.value || "";
      const provider = playbookId ? concreteProvider() : "auto";

      for (let index = 0; index < batches.length; index += 1) {
        const batch = batches[index];
        renderProgress(batches, index, `Iniciando lote ${index + 1} de ${batches.length}...`);
        const payload = {
          target: batch.target,
          objective: batch.objective,
          environment: "monitoring",
          mode: "propose",
          ssh_port: Number(batch.ssh_port || 22),
          provider,
          playbook_mode: playbookId ? "manual" : "auto",
          playbook_id: playbookId || null,
          multi_host: (batch.related_targets || []).length > 0,
          customer_name: state.context?.site?.alias || state.siteId,
          auto_expand_scope: false,
          related_targets: batch.related_targets || [],
        };
        const record = await request("/ui/api/executions", { method: "POST", body: payload });
        const executionId = record.execution_id || record.id;
        if (!executionId) throw new Error("A API não retornou o identificador da execução N2.");
        state.executionIds.push(executionId);
        await waitExecution(executionId, index, batches);
      }

      setRunStatus("Coleta concluída. Montando a revisão e salvando no banco...", false);
      state.review = await request("/ui/api/n2/review", {
        method: "POST",
        body: { site_id: state.siteId, host_names: hosts, responsibles: responsibles(), execution_ids: state.executionIds },
      });
      state.documentId = state.review.document_id || "";
      renderReview();
      await loadDocuments(state.siteId);
      setRunStatus("Validação concluída e salva. Revise os dados antes de exportar.", false);
      renderProgress(batches, batches.length, "Coleta, consolidação e persistência concluídas.", true);
    } catch (error) {
      setRunStatus(error.message, true);
      renderProgress(state.plan?.batches || [], 0, error.message, false, true);
    } finally {
      state.running = false;
      toggleRunButton(false);
    }
  }

  async function waitExecution(executionId, batchIndex, batches) {
    const deadline = Date.now() + 30 * 60 * 1000;
    while (Date.now() < deadline) {
      const record = await request(`/ui/api/executions/${encodeURIComponent(executionId)}`);
      const status = String(record.status || "").toLowerCase();
      const progress = Number(record.progress?.percent ?? record.percent ?? 0);
      const detail = record.progress?.detail || record.detail || `Lote ${batchIndex + 1}: ${status || "em execução"}`;
      renderProgress(batches, batchIndex, detail, false, false, progress);
      if (status === "completed") return record;
      if (["failed", "cancelled"].includes(status)) throw new Error(record.error || `A execução ${executionId} terminou como ${status}.`);
      await delay(1200);
    }
    throw new Error(`Tempo limite excedido aguardando a execução ${executionId}.`);
  }

  function renderProgress(batches, currentIndex, detail, completed = false, failed = false, innerPercent = 0) {
    const root = $("#n2-progress");
    if (!root) return;
    root.hidden = false;
    const total = Math.max(1, batches.length);
    const overall = completed ? 100 : Math.min(99, Math.round(((currentIndex + (Math.max(0, Math.min(100, innerPercent)) / 100)) / total) * 100));
    root.innerHTML = `<div class="n2-progress-head"><div><strong>${failed ? "Falha na validação" : completed ? "Validação concluída" : "IA coletando evidências"}</strong><span>${esc(detail || "")}</span></div><b>${overall}%</b></div><div class="n2-progress-track"><i style="width:${overall}%"></i></div><div class="n2-batch-pills">${batches.map((batch, index) => `<span class="${completed || index < currentIndex ? "done" : index === currentIndex ? failed ? "failed" : "running" : "pending"}">Lote ${index + 1}: ${esc((batch.host_names || []).join(", ") || "entrada")}</span>`).join("")}</div>`;
  }

  function toggleRunButton(disabled) {
    const button = $("#n2-run-validation");
    if (!button) return;
    button.disabled = disabled;
    button.textContent = disabled ? "IA validando ambiente..." : "Executar validação N2 com IA";
  }
  function setRunStatus(message, error) {
    const root = $("#n2-run-status");
    if (!root) return;
    root.textContent = message || "";
    root.classList.toggle("error", Boolean(error));
  }
  function hideReview() { const card = $("#n2-review-card"); if (card) card.hidden = true; }

  function renderReview() {
    const card = $("#n2-review-card");
    const root = $("#n2-review-content");
    if (!card || !root || !state.review) return;
    card.hidden = false;
    setResponsibleInputs(state.review.responsibles || {});
    const collection = state.review.collection || {};
    const hostCards = (state.review.selected_hosts || []).map((host, hostIndex) => {
      const fields = host.fields || {};
      const defs = [["server", "Servidor"], ["address_ipv4", "Address IPv4"], ["address_vpn", "Address VPN"], ["hostname", "Nome do host"], ["processor", "Processador"], ["memory", "Memória"], ["storage", "Armazenamento"], ["os", "Sistema operacional"]];
      return `<article class="n2-review-host"><header><div><span>${esc(host.role || host.kind || "host")}</span><h4>${esc(host.host)}</h4><small>${esc(host.ip || "sem IP")}</small></div><b>editável</b></header><div class="n2-edit-grid">${defs.map(([key, label]) => `<label><span>${esc(label)}</span><input data-review-host-field data-host-index="${hostIndex}" data-field-key="${esc(key)}" value="${esc(fields[key] || "")}"></label>`).join("")}</div><label class="n2-edit-wide"><span>Evidências / observações deste host</span><textarea rows="5" data-review-host-notes data-host-index="${hostIndex}">${esc(host.collection_notes || "")}</textarea></label></article>`;
    }).join("");
    const sections = (state.review.sections || []).map((section, sectionIndex) => `<details class="n2-review-section" ${sectionIndex < 2 ? "open" : ""}><summary><span>${esc(section.title)}</span><small>${(section.fields || []).filter((field) => field.value).length}/${(section.fields || []).length} preenchidos</small></summary><div class="n2-edit-grid">${(section.fields || []).map((field, fieldIndex) => `<label class="${field.control === "textarea" ? "n2-edit-wide" : ""}"><span>${esc(field.label)}</span>${field.control === "textarea" ? `<textarea rows="5" data-review-field data-section-index="${sectionIndex}" data-field-index="${fieldIndex}">${esc(field.value || "")}</textarea>` : `<input data-review-field data-section-index="${sectionIndex}" data-field-index="${fieldIndex}" value="${esc(field.value || "")}">`}</label>`).join("")}</div></details>`).join("");
    root.innerHTML = `<div class="n2-review-summary"><div><strong>${esc(state.review.client)}</strong><span>${esc(state.review.site_id)} · ${(state.review.selected_hosts || []).length} host(s) selecionado(s)</span></div><div><strong>${esc(collection.evidence_count || 0)}</strong><span>evidências estruturadas</span></div><div><strong>${esc((collection.completed_execution_ids || []).length)}</strong><span>execuções concluídas</span></div></div>${(collection.errors || []).length ? `<div class="n2-collection-errors"><strong>Pontos para revisão manual</strong>${collection.errors.map((item) => `<span>${esc(item)}</span>`).join("")}</div>` : ""}<section class="n2-review-block"><div class="n2-review-block-head"><p class="eyebrow">INFRAESTRUTURA</p><h3>Hosts selecionados</h3></div><div class="n2-review-hosts">${hostCards}</div></section><section class="n2-review-block"><div class="n2-review-block-head"><p class="eyebrow">DOCUMENTAÇÃO</p><h3>Campos do template</h3></div><div class="n2-review-sections">${sections}</div></section>`;

    $$('[data-review-host-field]', root).forEach((input) => input.addEventListener("input", () => {
      const host = state.review.selected_hosts[Number(input.dataset.hostIndex)];
      if (host) host.fields[input.dataset.fieldKey] = input.value;
      reviewChanged();
    }));
    $$('[data-review-host-notes]', root).forEach((input) => input.addEventListener("input", () => {
      const host = state.review.selected_hosts[Number(input.dataset.hostIndex)];
      if (host) host.collection_notes = input.value;
      reviewChanged();
    }));
    $$('[data-review-field]', root).forEach((input) => input.addEventListener("input", () => {
      const section = state.review.sections[Number(input.dataset.sectionIndex)];
      const field = section?.fields?.[Number(input.dataset.fieldIndex)];
      if (field) field.value = input.value;
      reviewChanged();
    }));
    updateReviewState(state.documentId ? "Salvo no PostgreSQL" : "Pronto para salvar");
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function updateReviewState(message, error = false) {
    const label = $("#n2-review-state");
    if (!label) return;
    label.textContent = message;
    label.classList.toggle("error", error);
  }

  function reviewChanged() {
    if (!state.review) return;
    state.review.responsibles = responsibles();
    updateReviewState("Alterações pendentes...");
    if (state.saveTimer) window.clearTimeout(state.saveTimer);
    state.saveTimer = window.setTimeout(() => void saveReview("reviewed", false), 900);
  }

  async function saveReview(status = "reviewed", showStatus = false, exportFormat = null) {
    if (!state.review || state.saving) return null;
    state.saving = true;
    state.review.responsibles = responsibles();
    if (showStatus) updateReviewState("Salvando no PostgreSQL...");
    try {
      const saved = await request("/ui/api/n2/documents", {
        method: "POST",
        body: { document_id: state.documentId || state.review.document_id || null, review: state.review, status, export_format: exportFormat },
      });
      state.documentId = saved.id;
      state.review.document_id = saved.id;
      updateReviewState("Salvo no PostgreSQL");
      await loadDocuments(state.siteId || "");
      return saved;
    } catch (error) {
      updateReviewState(`Falha ao salvar: ${error.message}`, true);
      if (showStatus) setRunStatus(error.message, true);
      return null;
    } finally {
      state.saving = false;
    }
  }

  async function loadDocuments(siteId = "") {
    try {
      const suffix = siteId ? `&site_id=${encodeURIComponent(siteId)}` : "";
      const data = await request(`/ui/api/n2/documents?limit=100${suffix}`);
      state.documents = data.items || [];
      renderDocuments();
    } catch (error) {
      const root = $("#n2-document-list");
      if (root) root.innerHTML = `<div class="empty-state error-state">${esc(error.message)}</div>`;
    }
  }

  function renderDocuments() {
    const root = $("#n2-document-list");
    if (!root) return;
    const query = String($("#n2-document-search")?.value || "").trim().toLocaleLowerCase("pt-BR");
    const rows = state.documents.filter((item) => !query || `${item.client || ""} ${item.site_id || ""} ${item.title || ""} ${(item.selected_hosts || []).join(" ")}`.toLocaleLowerCase("pt-BR").includes(query));
    if (!rows.length) {
      root.innerHTML = '<div class="empty-state">Nenhuma documentação N2 salva para este filtro.</div>';
      return;
    }
    root.innerHTML = rows.map((item) => {
      const date = item.updated_at ? new Date(item.updated_at).toLocaleString("pt-BR") : "sem data";
      return `<article class="n2-document-row"><div><strong>${esc(item.client || item.site_id)}</strong><span>${esc(item.site_id)} · ${esc((item.selected_hosts || []).join(", ") || "sem hosts")}</span><small>${esc(date)} · ${esc(item.status || "salvo")}${item.last_export_format ? ` · último export: ${esc(item.last_export_format.toUpperCase())}` : ""}</small></div><button type="button" class="secondary-button" data-n2-open-document="${esc(item.id)}">Abrir</button></article>`;
    }).join("");
    $$('[data-n2-open-document]', root).forEach((button) => button.addEventListener("click", () => void openDocument(button.dataset.n2OpenDocument)));
  }

  async function openDocument(documentId) {
    setRunStatus("Abrindo documentação salva...", false);
    try {
      const data = await request(`/ui/api/n2/documents/${encodeURIComponent(documentId)}`);
      const review = data.review;
      if (!review) throw new Error("Documento salvo não contém revisão N2.");
      state.documentId = data.id;
      state.review = review;
      state.review.document_id = data.id;
      await selectSite(data.site_id, { keepDocument: true });
      setResponsibleInputs(review.responsibles || data.responsibles || {});
      setSelectedHosts(data.selected_hosts || (review.selected_hosts || []).map((item) => item.host));
      renderReview();
      setRunStatus("Documentação carregada do PostgreSQL. Você pode editar ou exportar novamente.", false);
    } catch (error) {
      setRunStatus(error.message, true);
    }
  }

  function newDocument() {
    state.documentId = "";
    state.review = null;
    state.executionIds = [];
    state.plan = null;
    setResponsibleInputs({});
    setAllHosts(false);
    hideReview();
    const progress = $("#n2-progress");
    if (progress) progress.hidden = true;
    setRunStatus("Nova documentação pronta. Selecione cliente, hosts e execute a validação.", false);
  }

  async function exportReview(format) {
    if (!state.review) {
      setRunStatus("Execute a validação ou abra um documento salvo antes de exportar.", true);
      return;
    }
    const button = format === "docx" ? $("#n2-export-word") : $("#n2-export-pdf");
    const original = button?.textContent || "Exportar";
    if (button) { button.disabled = true; button.textContent = "Gerando..."; }
    try {
      await saveReview("reviewed", false);
      const result = await requestBlob(`/ui/api/n2/export/${format}`, { review: state.review, document_id: state.documentId || null });
      const match = /filename="?([^";]+)"?/i.exec(result.disposition);
      const filename = match?.[1] || `Documentacao-N2.${format}`;
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1500);
      await saveReview("exported", false, format);
      updateReviewState(`${format === "docx" ? "Word" : "PDF"} exportado e salvo`);
    } catch (error) {
      setRunStatus(error.message, true);
    } finally {
      if (button) { button.disabled = false; button.textContent = original; }
    }
  }

  function boot() {
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (ensureShell()) {
        window.clearInterval(timer);
      } else if (attempts > 120) {
        window.clearInterval(timer);
      }
    }, 100);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
