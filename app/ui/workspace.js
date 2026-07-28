(() => {
  const baseShowView = showView;
  const baseShowExecutionStart = showExecutionStart;
  const baseRenderBatchExecution = typeof renderBatchExecution === "function" ? renderBatchExecution : null;
  const hydratedInvestigations = new Set();

  function openAnalysisModal() {
    const modal = $("#analysis-modal");
    if (!modal) return;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("analysis-modal-open");
    window.setTimeout(() => $("#target")?.focus(), 80);
  }

  function closeAnalysisModal() {
    const modal = $("#analysis-modal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("analysis-modal-open");
  }

  showView = function enhancedShowView(name) {
    if (name === "analysis") {
      openAnalysisModal();
      return;
    }
    closeAnalysisModal();
    baseShowView(name);
  };

  showExecutionStart = function enhancedExecutionStart(payload) {
    closeAnalysisModal();
    baseShowExecutionStart(payload);
  };

  if (baseRenderBatchExecution) {
    renderBatchExecution = function enhancedBatchExecution() {
      closeAnalysisModal();
      return baseRenderBatchExecution();
    };
  }

  function statusTone(status) {
    if (["executed", "validated", "completed", "healthy", "success"].includes(status)) return "success";
    if (["failed", "critical", "error"].includes(status)) return "failure";
    return "warning";
  }

  function terminalStatusLabel(status) {
    const labels = {
      executed: "executado",
      validated: "validado",
      completed: "concluído",
      failed: "falhou",
      blocked: "bloqueado",
      unavailable: "indisponível",
      approval_required: "aguardando aprovação",
      pending: "pendente",
    };
    return labels[status] || status || "sem status";
  }

  function compactText(value, fallback = "") {
    if (value == null) return fallback;
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (typeof value === "object") {
      return String(value.statement || value.description || value.summary || value.reason || JSON.stringify(value));
    }
    return String(value);
  }

  function clipped(value, limit = 5200) {
    const text = compactText(value).trim();
    if (text.length <= limit) return { text, clipped: false };
    return { text: `${text.slice(0, limit)}\n\n[saída reduzida na interface]`, clipped: true };
  }

  function evidenceItems(result) {
    return Array.isArray(result.evidence) ? result.evidence : Array.isArray(result.evidence_preview) ? result.evidence_preview : [];
  }

  function renderCommandResult(item, index, prefix = "Evidência") {
    const status = String(item.status || "executed");
    const tone = statusTone(status);
    const command = compactText(item.command || item.tool || `${prefix} ${index + 1}`);
    const purpose = compactText(item.purpose || item.description || item.reason || "Coleta operacional");
    const stdout = clipped(item.stdout || "");
    const stderr = clipped(item.stderr || item.reason || "", 2600);
    const exitCode = item.exit_code == null ? "—" : item.exit_code;
    const outputLines = [
      `<span class="terminal-command">$ ${escapeHtml(command)}</span>`,
      stdout.text ? escapeHtml(stdout.text) : "[sem saída padrão]",
      stderr.text ? `<span class="terminal-error">${escapeHtml(stderr.text)}</span>` : "",
    ].filter(Boolean).join("\n");

    const renderChecks = (title, checks) => {
      if (!Array.isArray(checks) || !checks.length) return "";
      return checks.map((check, checkIndex) => {
        const checkCommand = compactText(check.command || `${title} ${checkIndex + 1}`);
        const checkOut = clipped(check.stdout || check.stderr || "[sem saída]", 2200).text;
        const rc = check.exit_code == null ? "—" : check.exit_code;
        return `<div class="terminal-validation"><strong>${escapeHtml(title)} · rc=${escapeHtml(rc)}</strong><pre>$ ${escapeHtml(checkCommand)}\n${escapeHtml(checkOut)}</pre></div>`;
      }).join("");
    };

    return `<article class="terminal-card" data-state="${tone}">
      <div class="terminal-titlebar"><span class="terminal-dots"><i></i><i></i><i></i></span><strong title="${escapeHtml(command)}">${escapeHtml(command)}</strong><span class="terminal-status">${escapeHtml(terminalStatusLabel(status))} · rc=${escapeHtml(exitCode)}</span></div>
      <div class="terminal-purpose">${escapeHtml(purpose)}${item.sudo ? " · sudo" : ""}${item.category ? ` · ${escapeHtml(item.category)}` : ""}</div>
      ${renderChecks("Pré-condição", item.preconditions)}
      <pre class="terminal-screen">${outputLines}</pre>
      ${renderChecks("Pós-validação", item.validations)}
    </article>`;
  }

  function renderEvidenceChart(items) {
    const groups = [
      { label: "Executadas", state: "success", count: items.filter((item) => statusTone(item.status) === "success").length },
      { label: "Alertas", state: "warning", count: items.filter((item) => statusTone(item.status) === "warning").length },
      { label: "Falhas", state: "failure", count: items.filter((item) => statusTone(item.status) === "failure").length },
    ];
    const maximum = Math.max(1, ...groups.map((item) => item.count));
    return `<div class="evidence-chart"><div class="evidence-chart-header"><strong>Distribuição das execuções</strong><span>${items.length} evidência(s)</span></div><div class="evidence-bars">${groups.map((item) => `<div class="evidence-bar-row" data-state="${item.state}"><span>${item.label}</span><div class="evidence-bar-track"><div class="evidence-bar-fill" style="width:${Math.max(item.count ? 8 : 0, Math.round((item.count / maximum) * 100))}%"></div></div><strong>${item.count}</strong></div>`).join("")}</div></div>`;
  }

  function resultSection(title, icon, body, extra = "") {
    return `<section class="result-section visual-section ${extra}"><header class="result-section-header"><div class="result-section-title"><span class="result-section-icon">${icon}</span><h3>${escapeHtml(title)}</h3></div></header><div class="result-section-body">${body}</div></section>`;
  }

  function renderTextCards(items, type) {
    const rows = (items || []).map((item) => compactText(item)).filter(Boolean);
    if (!rows.length) return "";
    if (type === "fact") {
      return `<div class="fact-grid">${rows.map((item) => `<div class="fact-card"><span>✓</span><p>${escapeHtml(item)}</p></div>`).join("")}</div>`;
    }
    return `<div class="recommendation-grid">${rows.map((item, index) => `<div class="recommendation-card"><strong>${index + 1}. Próximo passo</strong><p>${escapeHtml(item)}</p></div>`).join("")}</div>`;
  }

  function renderActions(actions) {
    if (!actions.length) return "";
    return `<div class="action-grid">${actions.map((item, index) => {
      const title = compactText(item.description || item.tool || `Ação ${index + 1}`);
      const detail = compactText(item.evidence_reason || item.reason || item.status || "Aguardando avaliação");
      const tool = item.tool ? `${item.tool}${item.arguments ? ` ${JSON.stringify(item.arguments)}` : ""}` : "";
      return `<div class="action-card"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p>${tool ? `<code>${escapeHtml(tool)}</code>` : ""}</div>`;
    }).join("")}</div>`;
  }

  function resultId(result) {
    return String(result.investigation_id || result.id || "");
  }

  async function hydrateEvidence(result) {
    const id = resultId(result);
    if (!id || evidenceItems(result).length || hydratedInvestigations.has(id)) return;
    hydratedInvestigations.add(id);
    try {
      const full = await api(`/ui/api/investigations/${encodeURIComponent(id)}`);
      if (state.currentInvestigationId === id && $("#result-drawer")?.classList.contains("open")) {
        renderEnhancedResult({ ...result, ...full }, true);
      }
    } catch (error) {
      const loading = $("#result-evidence-loading");
      if (loading) loading.textContent = `Não foi possível carregar as evidências completas: ${error.message}`;
    }
  }

  function renderEnhancedResult(result, hydrated = false) {
    const analysis = result.analysis || {};
    const actions = proposedActions(analysis, result);
    const ai = resultProvider(result);
    const environment = resultEnvironment(result);
    const status = analysis.status || result.status || "inconclusive";
    const confidence = Math.max(0, Math.min(100, Number(analysis.confidence ?? result.confidence ?? 0)));
    const facts = Array.isArray(analysis.facts) ? analysis.facts : [];
    const recommendations = Array.isArray(analysis.recommendations) ? analysis.recommendations : [];
    const review = result.review || analysis.review || {};
    const critic = analysis.critic || result.intelligence?.critic || {};
    const evidence = evidenceItems(result);
    const id = resultId(result);

    state.approvalToken = result.approval_token || null;
    state.currentInvestigationId = id || null;
    $("#result-title").textContent = result.hostname || result.target || "Resultado da investigação";

    const providerText = [ai.provider, ai.model].filter(Boolean).join(" · ") || "IA não identificada";
    const playbookText = result.playbook?.title || result.playbook?.id || "Seleção dinâmica";
    const duration = formatDuration(result.duration_ms || 0);
    const evidenceCount = evidence.length || result.evidence_count || 0;

    const overview = `<div class="result-summary">${statusBadge(status)}${environmentBadge(environment)}${providerBadge(ai.provider, ai.model)}${result.playbook ? `<span class="mode-badge">${escapeHtml(playbookText)}</span>` : ""}</div>
      <div class="result-hero"><div class="confidence-card"><div class="confidence-ring" style="--confidence-angle:${confidence * 3.6}deg"><strong>${escapeHtml(confidence)}%</strong></div><span>confiança validada</span></div><div class="result-kpis"><div class="result-kpi-card"><span class="kpi-icon">⌘</span><span>Evidências</span><strong>${escapeHtml(evidenceCount)} execução(ões)</strong></div><div class="result-kpi-card"><span class="kpi-icon">◷</span><span>Duração</span><strong>${escapeHtml(duration)}</strong></div><div class="result-kpi-card"><span class="kpi-icon">AI</span><span>Modelo utilizado</span><strong>${escapeHtml(providerText)}</strong></div><div class="result-kpi-card"><span class="kpi-icon">◇</span><span>Playbook</span><strong>${escapeHtml(playbookText)}</strong></div></div></div>`;

    const summaryBody = `<div class="result-callout" data-tone="${escapeHtml(status)}"><strong>Resumo operacional</strong><p>${escapeHtml(analysis.summary || "A análise foi concluída sem resumo textual.")}</p></div>${analysis.probable_cause ? `<div class="result-callout" data-tone="${escapeHtml(status)}" style="margin-top:10px"><strong>Causa provável</strong><p>${escapeHtml(analysis.probable_cause)}</p></div>` : ""}${analysis.conclusion ? `<div class="result-callout" data-tone="${escapeHtml(status)}" style="margin-top:10px"><strong>Conclusão</strong><p>${escapeHtml(analysis.conclusion)}</p></div>` : ""}`;

    const evidenceBody = evidence.length
      ? `${renderEvidenceChart(evidence)}<div class="terminal-list" style="margin-top:12px">${evidence.slice(0, 14).map((item, index) => renderCommandResult(item, index)).join("")}</div>${evidence.length > 14 ? `<details class="terminal-more"><summary>Mostrar mais ${evidence.length - 14} evidência(s)</summary><div class="terminal-list" style="margin-top:10px">${evidence.slice(14).map((item, index) => renderCommandResult(item, index + 14)).join("")}</div></details>` : ""}`
      : `<div class="result-loading-evidence" id="result-evidence-loading">${id && !hydrated ? "Carregando comandos e saídas persistidos..." : "Nenhuma saída de terminal foi registrada para esta investigação."}</div>`;

    const criticText = compactText(critic.summary || review.reason || review.summary || (review.approved ? "A proposta passou pela revisão independente." : "Nenhuma aprovação corretiva foi liberada."));
    const criticBody = `<div class="result-callout" data-tone="${review.approved || critic.verdict === "accept" ? "healthy" : "attention"}"><strong>${critic.verdict ? `Veredito: ${critic.verdict}` : "Revisão independente"}</strong><p>${escapeHtml(criticText)}</p></div>${critic.evidence_coverage != null ? `<div class="evidence-chart"><div class="evidence-chart-header"><strong>Cobertura de evidências</strong><span>${escapeHtml(critic.evidence_coverage)}%</span></div><div class="evidence-bar-track"><div class="evidence-bar-fill" style="width:${Math.max(0, Math.min(100, Number(critic.evidence_coverage)))}%"></div></div></div>` : ""}`;

    $("#result-content").innerHTML = `${overview}
      ${resultSection("Diagnóstico", "◆", summaryBody)}
      ${facts.length ? resultSection("Fatos confirmados", "✓", renderTextCards(facts, "fact")) : ""}
      ${resultSection("Execuções e evidências", "⌘", evidenceBody)}
      ${recommendations.length ? resultSection("Recomendações", "→", renderTextCards(recommendations, "recommendation")) : ""}
      ${actions.length ? resultSection("Ações propostas", "⚙", renderActions(actions)) : ""}
      ${(Object.keys(review).length || Object.keys(critic).length) ? resultSection("Revisão da segunda IA", "AI", criticBody) : ""}
      ${resultSection("Texto para ticket", "▣", `<p id="ticket-report">${escapeHtml(analysis.ticket_report || "Relatório de ticket não gerado.")}</p>`)}
      ${state.approvalToken ? `<div class="approval-box"><h3>Aprovação humana disponível</h3><p>Revise as ações e as evidências. Ambiente, política, segunda IA e pós-validação continuam obrigatórios.</p><button class="primary-button" id="approve-actions">Aprovar ações seguras</button></div>` : ""}
      <div class="result-actions"><button class="secondary-button" id="copy-ticket">Copiar texto do ticket</button>${id ? '<button class="ghost-button" id="open-full-detail">Atualizar evidências</button>' : ""}</div>
      <details class="raw-details"><summary>Ver retorno técnico completo</summary><pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre></details>`;

    $("#result-drawer").classList.add("open");
    $("#result-drawer").setAttribute("aria-hidden", "false");
    $("#copy-ticket")?.addEventListener("click", copyTicket);
    $("#approve-actions")?.addEventListener("click", approveActions);
    $("#open-full-detail")?.addEventListener("click", async () => {
      hydratedInvestigations.delete(id);
      await hydrateEvidence({ ...result, evidence: [] });
    });
    void hydrateEvidence(result);
  }

  showResult = renderEnhancedResult;

  function renderApprovedExecution(result) {
    const rows = Array.isArray(result.results) ? result.results : [];
    if (!rows.length) return `<div class="result-callout" data-tone="attention"><strong>Execução sem retorno</strong><p>Nenhum resultado técnico foi devolvido.</p></div>`;
    return `<div class="execution-result-panel"><h4>Comando, retorno e pós-validação</h4><div class="terminal-list">${rows.map((item, index) => renderCommandResult(item, index, "Ação")).join("")}</div></div>`;
  }

  approveActions = async function enhancedApproveActions() {
    if (!state.approvalToken || !state.currentInvestigationId) return;
    const confirmed = window.confirm("Você revisou as ações propostas e deseja autorizar somente as ações permitidas pelas políticas do Agent IA?");
    if (!confirmed) return;
    const button = $("#approve-actions");
    button.disabled = true;
    button.textContent = "Executando e validando...";
    try {
      const result = await api(`/ui/api/investigations/${encodeURIComponent(state.currentInvestigationId)}/approve`, { method: "POST", body: { token: state.approvalToken } });
      state.approvalToken = null;
      toast(result.status === "validated" ? "Execução aprovada e pós-validada." : "A execução terminou com falha de validação.", result.status === "validated" ? "success" : "error");
      button.closest(".approval-box").innerHTML = `<h3>${result.status === "validated" ? "Execução validada" : "Execução com falha"}</h3><p>O retorno abaixo mostra o comando aplicado e o estado coletado imediatamente depois.</p>${renderApprovedExecution(result)}`;
    } catch (error) {
      button.disabled = false;
      button.textContent = "Aprovar ações seguras";
      toast(error.message, "error");
    }
  };

  document.addEventListener("DOMContentLoaded", () => {
    $("#attach-batch-file")?.addEventListener("click", () => $("#batch-file")?.click());
    $$('[data-close-analysis]').forEach((element) => element.addEventListener("click", closeAnalysisModal));
    $("#analysis-modal")?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAnalysisModal();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && $("#analysis-modal")?.classList.contains("open")) closeAnalysisModal();
    });
  });
})();
