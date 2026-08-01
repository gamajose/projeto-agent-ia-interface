(() => {
  let inventoryBackfilled = false;
  let suggestionTimer = null;
  let suggestionRows = [];
  let activeSuggestion = -1;

  function values(value) {
    return Array.isArray(value) ? value.filter((item) => String(item || "").trim()) : [];
  }

  function listMarkup(items, emptyText = "Nenhum item registrado.") {
    const rows = values(items);
    return rows.length
      ? `<ul>${rows.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : `<p class="analysis-empty">${escapeHtml(emptyText)}</p>`;
  }

  function accessJourneyMarkup(items) {
    const rows = values(items);
    if (!rows.length) return "";
    return `<section class="result-section explainable-section"><div class="explainable-heading"><div><p class="eyebrow">CAMINHO DE ACESSO</p><h3>Onde a execução passou</h3></div></div><div class="access-journey">${rows.map((item) => {
      const status = ["completed", "failed", "running", "skipped"].includes(item.status) ? item.status : "pending";
      return `<article class="access-step" data-status="${escapeHtml(status)}"><span class="access-step-dot"></span><div><strong>${escapeHtml(item.label || item.step || "Etapa")}</strong><p>${escapeHtml(item.detail || "Sem detalhe adicional.")}</p></div></article>`;
    }).join("")}</div></section>`;
  }

  function qualityMarkup(quality) {
    if (!quality || typeof quality !== "object" || !Object.keys(quality).length) return "";
    const definitions = [
      ["identification", "Identificação do alvo"],
      ["connectivity", "Conectividade"],
      ["evidence_coverage", "Cobertura das evidências"],
      ["diagnostic", "Diagnóstico"],
      ["final_validation", "Validação final"],
    ];
    const overall = Math.max(0, Math.min(100, Number(quality.overall || 0)));
    return `<section class="result-section explainable-section"><div class="explainable-heading"><div><p class="eyebrow">QUALIDADE</p><h3>Qualidade da investigação</h3></div><strong class="quality-overall">${escapeHtml(Math.round(overall))}%</strong></div><div class="quality-grid">${definitions.map(([key, label]) => {
      const score = Math.max(0, Math.min(100, Number(quality[key] || 0)));
      return `<div class="quality-row"><span>${escapeHtml(label)}</span><b>${escapeHtml(Math.round(score))}%</b><i><em style="width:${score}%"></em></i></div>`;
    }).join("")}</div></section>`;
  }

  function playbookMarkup(playbook) {
    if (!playbook || typeof playbook !== "object") return "";
    if (!playbook.selected) {
      return `<section class="result-section explainable-section"><div class="explainable-heading"><div><p class="eyebrow">PLAYBOOK</p><h3>Análise adaptativa sem playbook inicial</h3></div></div>${listMarkup(playbook.reasons, "Nenhum playbook foi selecionado.")}</section>`;
    }
    const score = Math.max(0, Math.min(100, Number(playbook.score || 0)));
    return `<section class="result-section explainable-section"><div class="explainable-heading"><div><p class="eyebrow">PLAYBOOK SELECIONADO</p><h3>${escapeHtml(playbook.title || playbook.id)}</h3></div><strong class="quality-overall">${escapeHtml(Math.round(score))}%</strong></div><div class="compatibility-bar"><i style="width:${score}%"></i></div>${listMarkup(playbook.reasons, "Selecionado pela política atual.")}</section>`;
  }

  function recurrenceMarkup(recurrence) {
    if (!recurrence || typeof recurrence !== "object") return "";
    const total = Number(recurrence.total || 0);
    return `<section class="result-section explainable-section"><div class="explainable-heading"><div><p class="eyebrow">MEMÓRIA OPERACIONAL</p><h3>${total ? "Recorrência e casos relacionados" : "Sem recorrência localizada"}</h3></div>${total ? `<strong class="recurrence-count">${escapeHtml(total)} caso(s)</strong>` : ""}</div><p>${escapeHtml(recurrence.summary || "Nenhuma ocorrência anterior relacionada foi encontrada.")}</p>${values(recurrence.previous_probable_causes).length ? `<details class="analysis-details"><summary>Ver causas anteriores</summary>${listMarkup(recurrence.previous_probable_causes)}</details>` : ""}</section>`;
  }

  function controlsMarkup(controls) {
    if (!controls || typeof controls !== "object") return "";
    return `<section class="result-section explainable-section"><div class="explainable-heading"><div><p class="eyebrow">CONTROLE DA EXECUÇÃO</p><h3>Limites, repetição e timeout</h3></div></div><div class="control-metrics"><span><b>${escapeHtml(controls.adaptive_rounds || 0)}</b>rodadas adaptativas</span><span><b>${escapeHtml(controls.evidence_collected || 0)}</b>evidências</span><span><b>${escapeHtml(controls.duplicate_requests_ignored || 0)}</b>repetições evitadas</span><span><b>${escapeHtml(controls.timeouts || 0)}</b>timeouts</span></div>${controls.command_limit_reached ? '<p class="analysis-warning">O limite máximo de comandos foi alcançado; a conclusão considera essa restrição.</p>' : ""}</section>`;
  }

  function explainabilityMarkup(analysis) {
    const context = analysis.target_context || {};
    const explanation = analysis.explainability || {};
    const hypotheses = values(analysis.hypotheses);
    const discarded = values(analysis.discarded_hypotheses);
    const missing = values(analysis.missing_information);
    const facts = values(analysis.facts);
    return `<div class="explainable-analysis" data-explainable-analysis>
      <section class="result-section target-context-card"><div><p class="eyebrow">ALVO IDENTIFICADO</p><h3>${escapeHtml(context.client_name || context.hostname || context.vpn_ip || "Alvo")}</h3><p>${escapeHtml(context.hostname && context.hostname !== context.client_name ? context.hostname : "")}</p></div><div class="target-context-meta"><span><b>IP</b>${escapeHtml(context.vpn_ip || "—")}</span><span><b>Ambiente</b>${escapeHtml(context.environment || "unknown")}</span><span><b>Perfil</b>${escapeHtml(context.profile || "unknown")}</span><span><b>Acesso</b>${escapeHtml(context.access_mode === "vpn_menu" ? "Menu VPN" : "SSH direto")}</span></div></section>
      ${accessJourneyMarkup(analysis.access_journey)}
      <section class="result-section explainable-section"><div class="explainable-heading"><div><p class="eyebrow">RACIOCÍNIO EXPLICÁVEL</p><h3>Fato, hipótese e lacuna</h3></div></div><div class="reasoning-columns"><article data-kind="fact"><h4>Fatos comprovados</h4>${listMarkup(facts, "Nenhum fato foi registrado.")}</article><article data-kind="hypothesis"><h4>Hipóteses em avaliação</h4>${listMarkup(hypotheses, "Nenhuma hipótese permanece em aberto.")}</article><article data-kind="missing"><h4>Evidências pendentes</h4>${listMarkup(missing, "Nenhuma evidência adicional foi solicitada.")}</article></div>${discarded.length ? `<details class="analysis-details"><summary>Hipóteses descartadas (${discarded.length})</summary>${listMarkup(discarded)}</details>` : ""}</section>
      <section class="result-section explainable-section"><div class="explainable-heading"><div><p class="eyebrow">RESPOSTAS OPERACIONAIS</p><h3>O que a IA consegue afirmar</h3></div></div><div class="explainability-answers"><article><span>Onde parou?</span><p>${escapeHtml(explanation.where_stopped || "Ponto de parada não registrado.")}</p></article><article><span>Causa mais provável</span><p>${escapeHtml(explanation.most_probable_cause || analysis.probable_cause || "Não definida.")}</p></article><article><span>Próximo passo seguro</span><p>${escapeHtml(explanation.next_safe_step || analysis.next_safe_step || "Não definido.")}</p></article></div></section>
      ${playbookMarkup(analysis.playbook_match)}
      ${recurrenceMarkup(analysis.recurrence)}
      ${qualityMarkup(analysis.quality)}
      ${controlsMarkup(analysis.execution_controls)}
    </div>`;
  }

  if (typeof showResult === "function") {
    const baseShowResult = showResult;
    showResult = function showExplainableResult(result) {
      const output = baseShowResult(result);
      const analysis = result?.analysis || {};
      const content = $("#result-content");
      if (content && !content.querySelector("[data-explainable-analysis]")) {
        const summary = content.querySelector(".result-summary");
        if (summary) summary.insertAdjacentHTML("afterend", explainabilityMarkup(analysis));
        else content.insertAdjacentHTML("afterbegin", explainabilityMarkup(analysis));
      }
      const displayName = result?.display_target || analysis?.target_context?.client_name;
      if (displayName) $("#result-title").textContent = displayName;
      return output;
    };
  }

  if (typeof investigationRow === "function") {
    investigationRow = function investigationRowWithClientName(item, columns = "recent") {
      const context = item.analysis?.target_context || {};
      const hostname = context.client_name || item.hostname || item.target;
      const technical = item.hostname && item.hostname !== hostname ? item.hostname : "";
      const ip = context.vpn_ip || (item.target !== hostname ? item.target : "");
      const targetLine = [ip, technical].filter(Boolean).join(" · ");
      if (columns === "recent") {
        return `<tr class="clickable" data-investigation-id="${escapeHtml(item.id)}"><td><strong>${escapeHtml(hostname)}</strong><small>${escapeHtml(targetLine)}</small></td><td>${environmentBadge(item.environment)}</td><td>${statusBadge(item.status)}</td><td>${escapeHtml(item.confidence ?? 0)}%</td><td>${escapeHtml(formatDate(item.created_at))}</td></tr>`;
      }
      return `<tr class="clickable" data-investigation-id="${escapeHtml(item.id)}"><td><strong>${escapeHtml(hostname)}</strong><small>${escapeHtml(targetLine)}</small></td><td title="${escapeHtml(item.objective)}">${escapeHtml(String(item.objective || "").slice(0, 82))}${String(item.objective || "").length > 82 ? "…" : ""}</td><td>${escapeHtml(item.playbook?.title || item.analysis?.playbook_match?.title || "Automático")}</td><td>${environmentBadge(item.environment)}</td><td>${statusBadge(item.status)}</td><td>${escapeHtml(item.confidence ?? 0)}%</td><td>${escapeHtml(formatDate(item.created_at))}</td></tr>`;
    };
  }

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
