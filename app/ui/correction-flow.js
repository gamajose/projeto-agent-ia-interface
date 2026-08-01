(() => {
  const normalizing = new Set();
  const baseFlowShowResult = showResult;

  function investigationId(result) {
    return String(result?.investigation_id || result?.id || "");
  }

  function proposedActions(result) {
    const analysis = result?.analysis || {};
    return (analysis.proposed_actions || []).filter((item) => item && item.status === "proposed");
  }

  function resultMode(result) {
    return String(result?.requested_mode || result?.mode || "");
  }

  function resultEnvironmentValue(result) {
    return String(result?.environment_classification?.environment || result?.environment || "unknown");
  }

  function textValues(value) {
    if (typeof value === "string") return [value];
    if (Array.isArray(value)) return value.flatMap(textValues);
    if (value && typeof value === "object") return Object.values(value).flatMap(textValues);
    return [];
  }

  function looksEnglish(result) {
    const analysis = result?.analysis || {};
    if (analysis.language === "pt-BR") return false;
    const text = textValues({
      summary: analysis.summary,
      probable_cause: analysis.probable_cause,
      conclusion: analysis.conclusion,
      recommendations: analysis.recommendations,
      ticket_report: analysis.ticket_report,
    }).join(" ").toLowerCase();
    const matches = text.match(/\b(the|and|this|that|with|from|server|memory|service|evidence|summary|likely|cause|recommendation|should|investigation|confidence|because)\b/g) || [];
    return matches.length >= 4;
  }

  function hasConfidenceMismatch(result) {
    const analysis = result?.analysis || {};
    const confidence = Number(analysis.confidence ?? result?.confidence);
    if (!Number.isFinite(confidence)) return false;
    const ticket = String(analysis.ticket_report || "");
    const match = ticket.match(/(?:confidence|confid[eê]ncia)\D{0,12}(\d{1,3})\s*%/i);
    return Boolean(match && Number(match[1]) !== confidence);
  }

  async function normalizeHistoricalResult(result) {
    const id = investigationId(result);
    if (!id || normalizing.has(id) || (!looksEnglish(result) && !hasConfidenceMismatch(result))) return;
    normalizing.add(id);
    try {
      const normalized = await api(`/ui/api/investigations/${encodeURIComponent(id)}/normalize-presentation`, { method: "POST" });
      showResult(normalized);
      state.investigationsLoaded = false;
    } catch {
      // Mantém o resultado original quando o provedor de tradução não está disponível.
    } finally {
      normalizing.delete(id);
    }
  }

  function continuationMarkup(result) {
    const id = investigationId(result);
    const mode = resultMode(result);
    const environment = resultEnvironmentValue(result);
    const actions = proposedActions(result);
    const review = result?.review || result?.analysis?.review || {};
    if (!id || mode !== "propose" || result?.approval_token) return "";

    if (["production", "standby", "unknown"].includes(environment)) {
      return `<section class="result-section correction-continuation" data-tone="attention"><h3>Proposta preservada</h3><p>A investigação pode ser consultada normalmente, mas o ambiente ${escapeHtml(environment)} não permite correção pelo Agent IA. Produção, standby e ambiente desconhecido continuam somente com investigação e proposta.</p></section>`;
    }
    if (!actions.length) {
      return `<section class="result-section correction-continuation"><h3>Continuar sem repetir a análise</h3><p>As evidências estão preservadas, mas nenhuma ação corretiva foi liberada. Para corrigir sem refazer a coleta, associe um playbook com ferramenta corretiva permitida e execute uma nova proposta baseada nesse playbook.</p><button type="button" class="secondary-button" data-open-playbooks>Ver playbooks</button></section>`;
    }
    if (!review.approved) {
      return `<section class="result-section correction-continuation" data-tone="attention"><h3>Correção não liberada</h3><p>A segunda IA não aprovou as ações. A investigação continua disponível, porém não pode avançar para execução.</p></section>`;
    }
    return `<section class="result-section correction-continuation"><h3>Continuar para correção</h3><p>Reutilize esta análise e as ${actions.length} ação(ões) já revisadas, sem repetir SSH, coleta ou diagnóstico. Uma nova autorização temporária será gerada e ainda exigirá sua confirmação.</p><button type="button" class="primary-button" data-prepare-correction="${escapeHtml(id)}">Continuar para correção</button></section>`;
  }

  function bindContinuation(result) {
    const content = $("#result-content");
    if (!content || content.querySelector(".correction-continuation")) return;
    const markup = continuationMarkup(result);
    if (!markup) return;
    const actions = content.querySelector(".result-actions");
    const holder = document.createElement("div");
    holder.innerHTML = markup;
    const section = holder.firstElementChild;
    content.insertBefore(section, actions || content.querySelector(".raw-details") || null);

    section.querySelector("[data-open-playbooks]")?.addEventListener("click", () => {
      closeDrawer?.();
      showView("playbooks");
    });
    section.querySelector("[data-prepare-correction]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Revalidando proposta...";
      try {
        const response = await api(`/ui/api/investigations/${encodeURIComponent(investigationId(result))}/prepare-correction`, { method: "POST" });
        toast(response.message || "Proposta pronta para aprovação.");
        showResult({ ...result, approval_token: response.approval_token });
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
        button.textContent = "Continuar para correção";
      }
    });
  }

  function ensureIncidentStyles() {
    if (document.querySelector("#incident-intelligence-styles")) return;
    const style = document.createElement("style");
    style.id = "incident-intelligence-styles";
    style.textContent = `
      .incident-intelligence{display:grid;gap:14px}.incident-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.incident-head p{margin:4px 0 0;color:var(--muted)}
      .incident-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.incident-card{padding:14px;border:1px solid var(--border);border-radius:14px;background:color-mix(in srgb,var(--surface) 92%,transparent)}
      .incident-card h4{margin:0 0 8px;font-size:.85rem}.incident-card p{margin:0;color:var(--muted);font-size:.76rem;line-height:1.55}.incident-card ul{margin:9px 0 0;padding-left:18px;color:var(--muted);font-size:.74rem}
      .incident-verdict{display:inline-flex;padding:5px 9px;border-radius:999px;font-size:.68rem;font-weight:900;text-transform:uppercase;background:rgba(148,163,184,.14)}.incident-verdict.supported{color:var(--good)}.incident-verdict.contradicted{color:var(--bad)}.incident-verdict.needs_more_evidence{color:var(--warning)}
      .dependency-chain{display:flex;align-items:center;gap:7px;overflow:auto;padding:4px 0 8px}.dependency-node{flex:0 0 auto;padding:8px 10px;border:1px solid var(--border);border-radius:10px;background:rgba(77,216,255,.06);font-size:.7rem}.dependency-arrow{color:var(--muted)}
      .incident-feedback-buttons{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.incident-feedback-buttons button.selected{outline:2px solid var(--accent)}.incident-feedback-status{display:block;margin-top:8px;color:var(--muted);font-size:.7rem}
      .playbook-draft-preview{margin-top:9px;padding:10px;max-height:220px;overflow:auto;border-radius:10px;background:rgba(0,0,0,.22);font-size:.68rem;white-space:pre-wrap}.draft-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
      @media(max-width:760px){.incident-grid{grid-template-columns:1fr}.incident-head{display:grid}}
    `;
    document.head.appendChild(style);
  }

  function incidentData(result) {
    return result?.analysis?.incident_intelligence || result?.incident_intelligence || null;
  }

  function listMarkup(items, empty = "Nenhum item registrado.") {
    const rows = (items || []).filter(Boolean);
    return rows.length ? `<ul>${rows.map((item) => `<li>${escapeHtml(typeof item === "string" ? item : item.label || item.objective || JSON.stringify(item))}</li>`).join("")}</ul>` : `<p>${escapeHtml(empty)}</p>`;
  }

  function dependencyMarkup(map) {
    const nodes = map?.nodes || [];
    if (!nodes.length) return "<p>Dependências ainda não identificadas.</p>";
    return `<div class="dependency-chain">${nodes.map((node, index) => `${index ? '<span class="dependency-arrow">→</span>' : ""}<span class="dependency-node"><b>${escapeHtml(node.type)}</b><br>${escapeHtml(node.label)}</span>`).join("")}</div>${map.missing_layers?.length ? `<p>Camadas ainda não identificadas: ${escapeHtml(map.missing_layers.join(", "))}.</p>` : ""}`;
  }

  function draftFromResult(result) {
    return result?.playbook_draft || result?.analysis?.playbook_draft || null;
  }

  function incidentMarkup(result) {
    const intelligence = incidentData(result);
    if (!intelligence) return "";
    const correlation = intelligence.alert_correlation || {};
    const validation = intelligence.conclusion_validation || {};
    const freshness = intelligence.evidence_freshness || {};
    const failure = intelligence.access_failure;
    const draft = draftFromResult(result);
    const comparison = result?.analysis?.correction_validation || {};
    const id = investigationId(result);
    return `<section class="result-section incident-intelligence">
      <div class="incident-head"><div><p class="eyebrow">INTELIGÊNCIA DE INCIDENTES</p><h3>Correlação, dependências e validação independente</h3><p>O agente diferencia alertas derivados, testa a conclusão contra as evidências e registra sua confirmação operacional.</p></div><span class="incident-verdict ${escapeHtml(validation.verdict || "needs_more_evidence")}">${escapeHtml(validation.verdict === "supported" ? "Conclusão sustentada" : validation.verdict === "contradicted" ? "Contradição detectada" : "Evidência adicional")}</span></div>
      <div class="incident-grid">
        <article class="incident-card"><h4>Incidente correlacionado</h4><p><b>Primário:</b> ${escapeHtml(correlation.primary_alert?.label || "não classificado")}</p><p>${escapeHtml(correlation.reason || "")}</p>${listMarkup((correlation.related_alerts || []).map((item) => item.label), "Nenhum alerta derivado relacionado.")}</article>
        <article class="incident-card"><h4>Validação contrária da conclusão</h4><p>${escapeHtml(validation.recommendation || "Sem validação independente disponível.")}</p>${listMarkup(validation.contradictions, "Nenhuma contradição determinística encontrada.")}</article>
        <article class="incident-card"><h4>Mapa de dependências</h4>${dependencyMarkup(intelligence.dependency_map)}</article>
        <article class="incident-card"><h4>Validade das evidências</h4><p>${escapeHtml(freshness.summary || "Sem informação temporal.")}</p><p>Cobertura de horário: <b>${escapeHtml(freshness.timestamp_coverage ?? 0)}%</b></p></article>
        ${failure ? `<article class="incident-card"><h4>Falha de acesso classificada</h4><p><b>${escapeHtml(failure.layer)}</b>: ${escapeHtml(failure.summary)}</p><p>Próximo passo: ${escapeHtml(failure.next_step)}</p></article>` : ""}
        ${comparison.status ? `<article class="incident-card"><h4>Comparação antes e depois</h4><p>${escapeHtml(comparison.summary || "")}</p><p>Status: <b>${escapeHtml(comparison.status)}</b></p></article>` : ""}
        <article class="incident-card incident-feedback"><h4>O diagnóstico da IA foi confirmado?</h4><p>Seu retorno melhora a memória operacional sem alterar automaticamente os playbooks.</p><div class="incident-feedback-buttons"><button type="button" class="secondary-button" data-incident-feedback="confirmed">Confirmado</button><button type="button" class="secondary-button" data-incident-feedback="partial">Parcial</button><button type="button" class="secondary-button" data-incident-feedback="rejected">Não confirmado</button></div><span class="incident-feedback-status" data-feedback-status>${id ? "Carregando retornos anteriores..." : "Investigação ainda sem identificador persistido."}</span></article>
        ${draft ? `<article class="incident-card playbook-draft"><h4>Playbook gerado para revisão</h4><p>${escapeHtml(draft.title || draft.playbook_id || "Rascunho")}</p><p>Status: <b>${escapeHtml(draft.status || "draft")}</b></p>${draft.yaml_content ? `<details><summary>Ver YAML</summary><pre class="playbook-draft-preview">${escapeHtml(draft.yaml_content)}</pre></details>` : ""}${draft.id && draft.status === "draft" ? `<div class="draft-actions"><button type="button" class="primary-button" data-review-draft="approve" data-draft-id="${escapeHtml(draft.id)}">Aprovar e ativar</button><button type="button" class="secondary-button" data-review-draft="reject" data-draft-id="${escapeHtml(draft.id)}">Rejeitar</button></div>` : ""}</article>` : comparison.status === "validated" ? `<article class="incident-card playbook-draft"><h4>Transformar solução em playbook</h4><p>A correção passou pela pós-validação. Gere um YAML revisável antes de ativá-lo.</p><div class="draft-actions"><button type="button" class="primary-button" data-generate-draft="${escapeHtml(id)}">Gerar rascunho</button></div></article>` : ""}
      </div>
    </section>`;
  }

  async function refreshFeedback(result, section) {
    const id = investigationId(result);
    const status = section?.querySelector("[data-feedback-status]");
    if (!id || !status) return;
    try {
      const response = await api(`/ui/api/investigations/${encodeURIComponent(id)}/feedback`);
      status.textContent = response.total
        ? `${response.total} retorno(s): ${response.counts.confirmed} confirmado(s), ${response.counts.partial} parcial(is), ${response.counts.rejected} não confirmado(s).`
        : "Nenhum operador avaliou este diagnóstico ainda.";
      const mine = (response.items || []).find((item) => item.operator === state.session?.operator);
      if (mine) section.querySelector(`[data-incident-feedback="${mine.verdict}"]`)?.classList.add("selected");
    } catch (error) {
      status.textContent = `Não foi possível carregar feedback: ${error.message}`;
    }
  }

  function bindIncidentSection(result, section) {
    section.querySelectorAll("[data-incident-feedback]").forEach((button) => button.addEventListener("click", async () => {
      const id = investigationId(result);
      if (!id) return;
      const verdict = button.dataset.incidentFeedback;
      const comment = window.prompt("Observação opcional sobre o diagnóstico:", "") || "";
      const confirmedCause = verdict === "rejected"
        ? (window.prompt("Qual causa foi confirmada pelo operador? Campo opcional:", "") || "")
        : "";
      button.disabled = true;
      try {
        await api(`/ui/api/investigations/${encodeURIComponent(id)}/feedback`, {
          method: "POST",
          body: { verdict, comment, confirmed_cause: confirmedCause },
        });
        section.querySelectorAll("[data-incident-feedback]").forEach((item) => item.classList.toggle("selected", item === button));
        toast("Feedback operacional registrado.");
        await refreshFeedback(result, section);
      } catch (error) {
        toast(error.message, "error");
      } finally {
        button.disabled = false;
      }
    }));

    section.querySelector("[data-generate-draft]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Gerando YAML...";
      try {
        const response = await api(`/ui/api/investigations/${encodeURIComponent(investigationId(result))}/playbook-draft`, { method: "POST" });
        toast("Rascunho de playbook criado para revisão.");
        const refreshed = await api(`/ui/api/investigations/${encodeURIComponent(investigationId(result))}`);
        showResult(refreshed);
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
        button.textContent = "Gerar rascunho";
      }
    });

    section.querySelectorAll("[data-review-draft]").forEach((button) => button.addEventListener("click", async () => {
      const action = button.dataset.reviewDraft;
      const notes = window.prompt(action === "approve" ? "Observação da aprovação (opcional):" : "Motivo da rejeição (opcional):", "") || "";
      if (action === "approve" && !window.confirm("Ativar este playbook no catálogo operacional após a revisão?")) return;
      button.disabled = true;
      try {
        await api(`/ui/api/playbook-drafts/${encodeURIComponent(button.dataset.draftId)}/review`, { method: "POST", body: { action, notes } });
        toast(action === "approve" ? "Playbook aprovado e ativado." : "Rascunho rejeitado.");
        state.playbooksLoaded = false;
        const refreshed = await api(`/ui/api/investigations/${encodeURIComponent(investigationId(result))}`);
        showResult(refreshed);
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
      }
    }));

    void refreshFeedback(result, section);
  }

  function renderIncidentSection(result) {
    ensureIncidentStyles();
    const content = $("#result-content");
    if (!content || content.querySelector(".incident-intelligence")) return;
    const markup = incidentMarkup(result);
    if (!markup) return;
    const holder = document.createElement("div");
    holder.innerHTML = markup;
    const section = holder.firstElementChild;
    const actions = content.querySelector(".result-actions");
    content.insertBefore(section, actions || content.querySelector(".raw-details") || null);
    bindIncidentSection(result, section);
  }

  showResult = function showResultWithCorrectionContinuation(result) {
    const output = baseFlowShowResult(result);
    bindContinuation(result);
    renderIncidentSection(result);
    void normalizeHistoricalResult(result);
    return output;
  };
})();
