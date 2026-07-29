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

  showResult = function showResultWithCorrectionContinuation(result) {
    const output = baseFlowShowResult(result);
    bindContinuation(result);
    void normalizeHistoricalResult(result);
    return output;
  };
})();
