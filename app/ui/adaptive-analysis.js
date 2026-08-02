(() => {
  const previousShowResult = showResult;

  function adaptiveData(result) {
    const analysis = result?.analysis || {};
    return {
      adaptive: analysis.adaptive_hypotheses || result?.adaptive_hypotheses || null,
      fingerprint: analysis.environment_fingerprint || result?.environment_fingerprint || null,
      graph: analysis.adaptive_dependency_graph || result?.adaptive_dependency_graph || null,
      grouping: analysis.adaptive_alert_grouping || result?.adaptive_alert_grouping || null,
      memory: analysis.validated_memory_guidance || result?.validated_memory_guidance || null,
    };
  }

  function ensureStyles() {
    if (document.querySelector("#adaptive-analysis-styles")) return;
    const style = document.createElement("style");
    style.id = "adaptive-analysis-styles";
    style.textContent = `
      .adaptive-panel{display:grid;gap:14px;border-color:color-mix(in srgb,var(--accent) 42%,var(--border));background:linear-gradient(145deg,rgba(77,216,255,.06),rgba(99,102,241,.035))}
      .adaptive-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.adaptive-head p{margin:5px 0 0;color:var(--muted);font-size:.76rem;line-height:1.5}
      .adaptive-state{display:inline-flex;padding:6px 10px;border-radius:999px;font-size:.67rem;font-weight:900;text-transform:uppercase;background:rgba(148,163,184,.14);white-space:nowrap}.adaptive-state.confirmed{color:var(--good)}.adaptive-state.probable,.adaptive-state.testing{color:var(--warning)}.adaptive-state.open{color:var(--muted)}.adaptive-state.discarded{color:var(--bad)}
      .adaptive-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.adaptive-card{padding:13px;border:1px solid var(--border);border-radius:13px;background:rgba(0,0,0,.09)}.adaptive-card h4{margin:0 0 7px;font-size:.81rem}.adaptive-card p,.adaptive-card li{font-size:.73rem;line-height:1.5;color:var(--muted)}.adaptive-card ul{margin:7px 0 0;padding-left:18px}
      .hypothesis-list{display:grid;gap:9px}.hypothesis-card{padding:12px;border:1px solid var(--border);border-radius:12px;background:rgba(255,255,255,.025)}.hypothesis-card[data-status="confirmed"]{border-color:color-mix(in srgb,var(--good) 55%,var(--border));background:rgba(34,197,94,.06)}.hypothesis-card[data-status="probable"],.hypothesis-card[data-status="testing"]{border-color:color-mix(in srgb,var(--warning) 45%,var(--border));background:rgba(245,158,11,.045)}.hypothesis-card[data-status="discarded"]{opacity:.68}.hypothesis-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.hypothesis-top strong{font-size:.78rem}.hypothesis-card p{margin:6px 0 0}.hypothesis-evidence{margin-top:8px;padding-top:8px;border-top:1px solid var(--border)}
      .fingerprint-chips{display:flex;gap:6px;flex-wrap:wrap}.fingerprint-chip{padding:5px 8px;border-radius:999px;background:rgba(148,163,184,.11);font-size:.68rem;color:var(--muted)}
      .adaptive-path{display:grid;gap:7px}.adaptive-path-row{padding:8px 9px;border-left:3px solid var(--accent);border-radius:7px;background:rgba(77,216,255,.045);font-size:.71rem;color:var(--muted)}
      .adaptive-details{margin-top:4px}.adaptive-details summary{cursor:pointer;color:var(--muted);font-size:.72rem}.adaptive-details[open] summary{margin-bottom:8px}
      @media(max-width:760px){.adaptive-grid{grid-template-columns:1fr}.adaptive-head{display:grid}.adaptive-state{justify-self:start}}
    `;
    document.head.appendChild(style);
  }

  function stateLabel(status) {
    return {
      confirmed: "Causa confirmada",
      probable: "Hipótese forte",
      testing: "Em teste",
      open: "Hipótese aberta",
      discarded: "Descartada",
    }[status] || String(status || "Em análise");
  }

  function list(items, empty) {
    const rows = (items || []).filter(Boolean);
    if (!rows.length) return `<p>${escapeHtml(empty)}</p>`;
    return `<ul>${rows.map((item) => `<li>${escapeHtml(typeof item === "string" ? item : item.purpose || item.statement || item.title || JSON.stringify(item))}</li>`).join("")}</ul>`;
  }

  function evidenceMarkup(items) {
    const rows = (items || []).filter((item) => item?.command || item?.excerpt);
    if (!rows.length) return "";
    return `<div class="hypothesis-evidence"><strong>Evidências associadas</strong>${list(rows.map((item) => `${item.command || "coleta"}: ${item.excerpt || "resultado associado"}`), "")}</div>`;
  }

  function hypothesisMarkup(adaptive) {
    const hypotheses = (adaptive?.hypotheses || []).filter((item) => item && item.status !== "discarded");
    const discarded = (adaptive?.hypotheses || []).filter((item) => item?.status === "discarded");
    if (!hypotheses.length) return "<p>A árvore de hipóteses ainda não recebeu evidências suficientes.</p>";
    const confirmed = hypotheses.find((item) => item.status === "confirmed");
    const visible = confirmed ? [confirmed] : hypotheses.slice(0, 5);
    const cards = visible.map((item) => `<article class="hypothesis-card" data-status="${escapeHtml(item.status)}">
      <div class="hypothesis-top"><strong>${escapeHtml(item.title || item.id)}</strong><span class="adaptive-state ${escapeHtml(item.status)}">${escapeHtml(stateLabel(item.status))}</span></div>
      <p>${escapeHtml(item.mechanism || "Mecanismo ainda em avaliação.")}</p>
      ${evidenceMarkup(item.supporting_evidence)}
      ${item.contradicting_evidence?.length ? `<div class="hypothesis-evidence"><strong>Contradições</strong>${list(item.contradicting_evidence.map((row) => `${row.command}: ${row.excerpt}`), "")}</div>` : ""}
    </article>`).join("");
    const discardedMarkup = discarded.length
      ? `<details class="adaptive-details"><summary>${discarded.length} hipótese(s) descartada(s)</summary>${list(discarded.map((item) => item.title || item.id), "")}</details>`
      : "";
    return `<div class="hypothesis-list">${cards}</div>${discardedMarkup}`;
  }

  function fingerprintMarkup(fingerprint) {
    if (!fingerprint) return "<p>Fingerprint ainda não disponível.</p>";
    const platform = fingerprint.platform || {};
    const chips = [
      platform.name,
      platform.version ? `versão ${platform.version}` : "",
      fingerprint.init_system,
      fingerprint.virtualization,
      ...(fingerprint.monitoring_stack || []),
      ...(fingerprint.omd_sites || []).map((site) => `OMD ${site}`),
    ].filter(Boolean);
    return `<div class="fingerprint-chips">${chips.map((item) => `<span class="fingerprint-chip">${escapeHtml(item)}</span>`).join("")}</div><p>Assinatura operacional: <code>${escapeHtml(fingerprint.signature || "não calculada")}</code></p>`;
  }

  function graphMarkup(graph) {
    const nodes = new Map((graph?.nodes || []).map((item) => [item.id, item.label]));
    const edges = (graph?.edges || []).slice(0, 10);
    if (!edges.length) return "<p>O grafo será ampliado conforme dependências forem descobertas.</p>";
    return `<div class="adaptive-path">${edges.map((edge) => `<div class="adaptive-path-row">${escapeHtml(nodes.get(edge.source) || edge.source)} <b>→ ${escapeHtml(edge.relation || "depende de")} →</b> ${escapeHtml(nodes.get(edge.target) || edge.target)}</div>`).join("")}</div>`;
  }

  function groupingMarkup(grouping) {
    const groups = grouping?.groups || [];
    if (!groups.length) return `<p>${grouping?.alerts?.length ? "Nenhum agrupamento causal foi confirmado ainda." : "A solicitação contém um único alerta principal."}</p>`;
    return groups.map((group) => `<div class="adaptive-path-row"><b>${escapeHtml(group.domain || "incidente")}</b>: ${escapeHtml(group.relationship || "alertas relacionados")} · ${escapeHtml((group.alert_ids || []).length)} alerta(s)</div>`).join("");
  }

  function adaptiveMarkup(result) {
    const { adaptive, fingerprint, graph, grouping, memory } = adaptiveData(result);
    if (!adaptive && !fingerprint) return "";
    const leader = adaptive?.confirmed_cause || adaptive?.leader || {};
    const nextTests = adaptive?.next_best_tests || [];
    const memoryCases = memory?.verified_cases || [];
    return `<section class="result-section adaptive-panel">
      <div class="adaptive-head"><div><p class="eyebrow">ANÁLISE ADAPTATIVA</p><h3>A investigação muda conforme cada evidência</h3><p>O motor mantém uma árvore causal, descarta caminhos incompatíveis e prioriza o próximo teste que melhor reduz a dúvida.</p></div><span class="adaptive-state ${escapeHtml(leader.status || "open")}">${escapeHtml(stateLabel(leader.status || "open"))}</span></div>
      <div class="adaptive-grid">
        <article class="adaptive-card"><h4>Árvore de hipóteses</h4>${hypothesisMarkup(adaptive)}</article>
        <article class="adaptive-card"><h4>Fingerprint do ambiente</h4>${fingerprintMarkup(fingerprint)}</article>
        <article class="adaptive-card"><h4>Próximos testes de maior valor</h4>${list(nextTests.map((item) => `${item.tool}: ${item.purpose}`), adaptive?.stop_decision?.ready ? "A causa já possui evidência suficiente; não há teste concorrente obrigatório." : "Nenhum próximo teste estruturado foi encontrado no catálogo atual.")}</article>
        <article class="adaptive-card"><h4>Grafo de dependências</h4>${graphMarkup(graph)}</article>
        <article class="adaptive-card"><h4>Agrupamento de alertas</h4>${groupingMarkup(grouping)}</article>
        <article class="adaptive-card"><h4>Memória validada</h4>${list(memoryCases.map((item) => `${item.probable_cause || item.symptom} · similaridade ${item.similarity ?? "—"}`), "Nenhuma resolução validada semelhante foi usada nesta análise.")}<p>Casos anteriores priorizam testes, mas nunca substituem evidência atual.</p></article>
      </div>
    </section>`;
  }

  function injectAdaptivePanel(result) {
    ensureStyles();
    const content = $("#result-content");
    if (!content) return;
    content.querySelector(".adaptive-panel")?.remove();
    const markup = adaptiveMarkup(result);
    if (!markup) return;
    const holder = document.createElement("div");
    holder.innerHTML = markup;
    const rootCause = content.querySelector(".root-cause-panel");
    content.insertBefore(holder.firstElementChild, rootCause || content.firstChild);
  }

  showResult = function adaptiveShowResult(result) {
    previousShowResult(result);
    injectAdaptivePanel(result);
  };
})();
