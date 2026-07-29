(() => {
  let inventoryBackfilled = false;
  let suggestionTimer = null;
  let suggestionRows = [];
  let activeSuggestion = -1;

  async function ensureInventoryBackfill() {
    if (inventoryBackfilled) return;
    try {
      await api("/ui/api/inventory/backfill", { method: "POST" });
      inventoryBackfilled = true;
      state.inventoryLoaded = false;
      state.dashboardLoaded = false;
    } catch {
      // A investigação continua disponível mesmo quando a reconciliação retroativa falha.
    }
  }

  if (typeof loadInventory === "function") {
    const baseLoadInventory = loadInventory;
    loadInventory = async function loadInventoryWithLearning() {
      await ensureInventoryBackfill();
      return baseLoadInventory();
    };
  }

  function hasMultipleTargets(value) {
    return /[;,\n]/.test(String(value || ""));
  }

  function autocompleteMarkup() {
    return `<div class="target-autocomplete" id="target-autocomplete" role="listbox" aria-label="Alvos aprendidos" hidden></div>`;
  }

  function closeSuggestions() {
    const element = $("#target-autocomplete");
    if (!element) return;
    element.hidden = true;
    element.innerHTML = "";
    suggestionRows = [];
    activeSuggestion = -1;
  }

  function environmentLabel(value) {
    return ({
      production: "Produção",
      standby: "Standby",
      monitoring: "Monitoramento",
      training: "Treinamento",
      unknown: "Desconhecido",
    })[value] || value || "Desconhecido";
  }

  function renderSuggestions(rows) {
    const element = $("#target-autocomplete");
    if (!element) return;
    suggestionRows = rows || [];
    activeSuggestion = -1;
    if (!suggestionRows.length) {
      closeSuggestions();
      return;
    }
    element.innerHTML = suggestionRows.map((item, index) => `<button type="button" role="option" data-target-suggestion="${index}"><span><strong>${escapeHtml(item.hostname || item.vpn_ip)}</strong><small>${escapeHtml(item.vpn_ip)}${item.ssh_port ? `:${escapeHtml(item.ssh_port)}` : ""}</small></span><span><small>${escapeHtml(environmentLabel(item.environment))}</small><small>${escapeHtml(item.os_name || "SO não identificado")}</small></span></button>`).join("");
    element.hidden = false;
    $$('[data-target-suggestion]', element).forEach((button) => {
      button.addEventListener("mousedown", (event) => event.preventDefault());
      button.addEventListener("click", () => chooseSuggestion(Number(button.dataset.targetSuggestion)));
    });
  }

  function highlightSuggestion(index) {
    const element = $("#target-autocomplete");
    if (!element || element.hidden || !suggestionRows.length) return;
    activeSuggestion = (index + suggestionRows.length) % suggestionRows.length;
    $$('[data-target-suggestion]', element).forEach((button, position) => {
      button.classList.toggle("active", position === activeSuggestion);
      button.setAttribute("aria-selected", position === activeSuggestion ? "true" : "false");
    });
    element.querySelector(`[data-target-suggestion="${activeSuggestion}"]`)?.scrollIntoView({ block: "nearest" });
  }

  function chooseSuggestion(index) {
    const item = suggestionRows[index];
    if (!item) return;
    $("#target").value = item.vpn_ip || item.value || "";
    if (item.ssh_port) $("#ssh-port").value = item.ssh_port;
    if ($("#environment").value === "unknown" && item.environment) $("#environment").value = item.environment;
    if (typeof updateCorrectionMode === "function") updateCorrectionMode();
    closeSuggestions();
    $("#objective")?.focus();
    toast(`Alvo ${item.hostname || item.vpn_ip} carregado do inventário.`);
  }

  async function loadSuggestions(value) {
    const query = String(value || "").trim();
    if (hasMultipleTargets(query)) {
      closeSuggestions();
      return;
    }
    try {
      const data = await api(`/ui/api/targets/suggestions?q=${encodeURIComponent(query)}&limit=12`);
      if ($("#target")?.value.trim() !== query) return;
      renderSuggestions(data.items || []);
    } catch {
      closeSuggestions();
    }
  }

  function scheduleSuggestions() {
    clearTimeout(suggestionTimer);
    const value = $("#target")?.value || "";
    if (hasMultipleTargets(value)) return closeSuggestions();
    suggestionTimer = setTimeout(() => void loadSuggestions(value), 180);
  }

  function setupTargetAutocomplete() {
    const target = $("#target");
    const field = target?.closest(".target-field");
    if (!target || !field) return;
    field.classList.add("target-field-autocomplete");
    if (!$("#target-autocomplete")) field.insertAdjacentHTML("beforeend", autocompleteMarkup());
    target.setAttribute("autocomplete", "off");
    target.addEventListener("input", scheduleSuggestions);
    target.addEventListener("focus", scheduleSuggestions);
    target.addEventListener("keydown", (event) => {
      const element = $("#target-autocomplete");
      if (!element || element.hidden) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        highlightSuggestion(activeSuggestion + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        highlightSuggestion(activeSuggestion - 1);
      } else if (event.key === "Enter" && activeSuggestion >= 0) {
        event.preventDefault();
        chooseSuggestion(activeSuggestion);
      } else if (event.key === "Escape") {
        closeSuggestions();
      }
    });
    document.addEventListener("click", (event) => {
      if (!field.contains(event.target)) closeSuggestions();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    setupTargetAutocomplete();
    void ensureInventoryBackfill();
  });
})();
