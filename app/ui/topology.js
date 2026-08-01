(() => {
  const MAX_RELATED_HOSTS = 3;
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
  let topologyLoadedFor = "";

  function topologyMarkup() {
    return `<section class="multi-host-scope" id="multi-host-scope">
      <div class="multi-host-switch-row">
        <label class="multi-host-switch"><input id="multi-host-enabled" type="checkbox"><span></span><div><strong>Investigar vários hosts da mesma empresa</strong><small>Reutiliza Monitor 1 → servidor de entrada → SSH interno.</small></div></label>
      </div>
      <div class="multi-host-config" id="multi-host-config" hidden>
        <div class="multi-host-head">
          <div><p class="eyebrow">TOPOLOGIA DO CLIENTE</p><h3>Hosts relacionados</h3><p>O alvo principal será o servidor de entrada. Os hosts abaixo serão acessados por SSH a partir dele, usando a mesma credencial operacional.</p></div>
          <button type="button" class="secondary-button" id="multi-host-add">Adicionar host</button>
        </div>
        <div class="multi-host-options">
          <label><span>Empresa / cliente</span><input id="multi-host-customer" maxlength="255" placeholder="Ex.: Empresa José"></label>
          <label class="multi-host-auto"><input id="multi-host-auto" type="checkbox" checked><span>Permitir que a IA consulte outros hosts já mapeados da mesma empresa quando as evidências indicarem dependência.</span></label>
        </div>
        <div class="multi-host-route-preview"><b>Caminho desta execução</b><span>Monitor 1</span><i>→</i><span id="multi-host-entry-label">servidor de entrada</span><i>→</i><span>hosts internos selecionados</span></div>
        <div class="multi-host-target-list" id="multi-host-target-list"></div>
        <div class="multi-host-note">Até três hosts relacionados serão visitados nesta execução. Produção e standby permanecem somente leitura. O Agent não acessa bancos de dados de clientes.</div>
      </div>
    </section>`;
  }

  function roleOptions(selected = "other") {
    return Object.entries(roleLabels).map(([value, label]) => `<option value="${value}"${value === selected ? " selected" : ""}>${label}</option>`).join("");
  }

  function environmentOptions(selected = "unknown") {
    return Object.entries(environmentLabels).map(([value, label]) => `<option value="${value}"${value === selected ? " selected" : ""}>${label}</option>`).join("");
  }

  function addRelatedTarget(initial = {}) {
    const list = $("#multi-host-target-list");
    if (!list || list.children.length >= MAX_RELATED_HOSTS) {
      toast(`Esta execução aceita até ${MAX_RELATED_HOSTS} hosts relacionados.`, "error");
      return;
    }
    const row = document.createElement("article");
    row.className = "multi-host-target";
    row.innerHTML = `<div class="multi-host-target-title"><strong>Host relacionado ${list.children.length + 1}</strong><button type="button" class="icon-button" data-remove-related aria-label="Remover host">×</button></div>
      <div class="multi-host-target-grid">
        <label class="wide"><span>IP ou hostname interno</span><input data-related-reference maxlength="255" value="${escapeHtml(initial.address || initial.reference || "")}" placeholder="10.45.1.24" required></label>
        <label><span>Porta SSH</span><input data-related-port type="number" min="1" max="65535" value="${escapeHtml(initial.ssh_port || 22)}"></label>
        <label><span>Função</span><select data-related-role>${roleOptions(initial.role || "other")}</select></label>
        <label><span>Ambiente</span><select data-related-environment>${environmentOptions(initial.environment || "unknown")}</select></label>
        <label class="wide"><span>Nome exibido</span><input data-related-label maxlength="255" value="${escapeHtml(initial.label || initial.hostname || "")}" placeholder="Produção José"></label>
        <label class="wide"><span>Acesso</span><input value="SSH pelo servidor de entrada" disabled><small>A sessão do Monitor 1 e do servidor de entrada será reutilizada.</small></label>
      </div>`;
    row.querySelector("[data-remove-related]").addEventListener("click", () => {
      row.remove();
      renumberTargets();
    });
    list.appendChild(row);
  }

  function renumberTargets() {
    $$(".multi-host-target", $("#multi-host-target-list")).forEach((row, index) => {
      row.querySelector(".multi-host-target-title strong").textContent = `Host relacionado ${index + 1}`;
    });
  }

  function relatedTargetsPayload() {
    return $$(".multi-host-target", $("#multi-host-target-list")).map((row) => ({
      reference: row.querySelector("[data-related-reference]").value.trim(),
      ssh_port: Number(row.querySelector("[data-related-port]").value || 22),
      role: row.querySelector("[data-related-role]").value,
      environment: row.querySelector("[data-related-environment]").value,
      label: row.querySelector("[data-related-label]").value.trim() || null,
      via: null,
      route_type: "ssh",
      credential_ref: "SSH_DEFAULT_PASSWORD",
    })).filter((item) => item.reference);
  }

  async function loadMappedTopology() {
    const reference = $("#target")?.value.trim();
    if (!reference || reference === topologyLoadedFor || reference.includes(";") || reference.includes("\n")) return;
    topologyLoadedFor = reference;
    try {
      const data = await api(`/ui/api/topology/resolve?reference=${encodeURIComponent(reference)}`);
      if (!data?.customer) return;
      $("#multi-host-customer").value = data.customer.name || "";
      const source = (data.nodes || []).find((node) => [node.address, node.hostname, node.label].filter(Boolean).some((value) => String(value).toLowerCase() === reference.toLowerCase()));
      if (!source) return;
      const destinations = new Map((data.nodes || []).map((node) => [node.id, node]));
      const mapped = (data.routes || [])
        .filter((route) => route.source_node_id === source.id && route.route_type === "ssh")
        .map((route) => destinations.get(route.destination_node_id))
        .filter(Boolean)
        .slice(0, MAX_RELATED_HOSTS);
      if (!mapped.length) return;
      $("#multi-host-enabled").checked = true;
      $("#multi-host-config").hidden = false;
      $("#multi-host-entry-label").textContent = source.label || source.hostname || source.address;
      $("#multi-host-target-list").innerHTML = "";
      mapped.forEach(addRelatedTarget);
      toast(`Topologia de ${data.customer.name} carregada com ${mapped.length} host(s) relacionado(s).`);
    } catch {
      // Um alvo ainda não mapeado continua disponível para cadastro manual.
    }
  }

  function injectTopologyForm() {
    const form = $("#analysis-form");
    if (!form || $("#multi-host-scope")) return;
    const anchor = form.querySelector(".compact-settings-grid") || form.querySelector("#objective")?.closest("label");
    if (!anchor) return;
    anchor.insertAdjacentHTML("beforebegin", topologyMarkup());
    $("#multi-host-enabled").addEventListener("change", (event) => {
      $("#multi-host-config").hidden = !event.target.checked;
      if (event.target.checked && !$("#multi-host-target-list").children.length) addRelatedTarget();
      $("#multi-host-entry-label").textContent = $("#target").value.trim() || "servidor de entrada";
    });
    $("#multi-host-add").addEventListener("click", () => addRelatedTarget());
    $("#target")?.addEventListener("change", () => {
      $("#multi-host-entry-label").textContent = $("#target").value.trim() || "servidor de entrada";
      void loadMappedTopology();
    });
    $("#target")?.addEventListener("blur", () => void loadMappedTopology());
  }

  function wrapApiPayload() {
    const baseApi = api;
    api = async function topologyAwareApi(path, options = {}) {
      const method = String(options.method || "GET").toUpperCase();
      if (path === "/ui/api/executions" && method === "POST" && options.body && typeof options.body === "object") {
        const enabled = Boolean($("#multi-host-enabled")?.checked);
        options = {
          ...options,
          body: {
            ...options.body,
            multi_host: enabled,
            customer_name: enabled ? ($("#multi-host-customer")?.value.trim() || null) : null,
            auto_expand_scope: enabled ? Boolean($("#multi-host-auto")?.checked) : false,
            related_targets: enabled ? relatedTargetsPayload() : [],
          },
        };
      }
      return baseApi(path, options);
    };
  }

  function multiHostResultMarkup(result) {
    const scope = result?.multi_host || result?.analysis?.multi_host;
    if (!scope?.enabled) return "";
    const customer = scope.customer?.name || "cliente não identificado";
    const hosts = scope.hosts || [];
    const handoffs = scope.handoffs || [];
    return `<section class="result-section multi-host-result">
      <div class="multi-host-result-head"><div><p class="eyebrow">INVESTIGAÇÃO MULTI-HOST</p><h3>${escapeHtml(customer)}</h3></div><span class="mode-badge">${hosts.length} host(s)</span></div>
      <div class="multi-host-result-route"><span>Monitor 1</span><i>→</i><span>${escapeHtml(scope.entry_host?.label || scope.entry_host?.hostname || scope.entry_host?.address || "entrada")}</span>${hosts.slice(1).map((host) => `<i>→</i><span>${escapeHtml(host.label || host.hostname || host.address)}</span>`).join("")}</div>
      <div class="multi-host-result-grid">${hosts.map((host) => `<article><div><strong>${escapeHtml(host.label || host.hostname || host.address)}</strong><small>${escapeHtml(roleLabels[host.role] || host.role || "Outro")} · ${escapeHtml(environmentLabels[host.environment] || host.environment || "Não informado")}</small></div><span class="status-badge ${escapeHtml(host.status || "inconclusive")}">${escapeHtml(labelStatus(host.status))}</span><p>${escapeHtml(host.probable_cause || host.summary || "Sem causa específica confirmada neste host.")}</p><b>Confiança ${escapeHtml(host.confidence || 0)}%</b></article>`).join("")}</div>
      ${handoffs.length ? `<div class="multi-host-handoffs"><h4>Trocas de host decididas pela IA</h4>${handoffs.map((item) => `<div data-status="${escapeHtml(item.status || "pending")}"><strong>${escapeHtml(item.from)} → ${escapeHtml(item.to)}</strong><p>${escapeHtml(item.reason || "Host incluído no escopo.")}</p></div>`).join("")}</div>` : ""}
      <p class="multi-host-safety">Toda a coleta multi-host foi somente leitura. Nenhuma sessão de banco de dados foi aberta e nenhuma correção foi autorizada automaticamente.</p>
    </section>`;
  }

  function wrapResult() {
    const baseShowResult = showResult;
    showResult = function showResultWithTopology(result) {
      const output = baseShowResult(result);
      const content = $("#result-content");
      if (!content || content.querySelector(".multi-host-result")) return output;
      const markup = multiHostResultMarkup(result);
      if (!markup) return output;
      const holder = document.createElement("div");
      holder.innerHTML = markup;
      const section = holder.firstElementChild;
      const raw = content.querySelector(".raw-details");
      content.insertBefore(section, raw || null);
      return output;
    };
  }

  function setup() {
    injectTopologyForm();
    wrapApiPayload();
    wrapResult();
  }

  document.addEventListener("DOMContentLoaded", setup);
})();
