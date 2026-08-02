(() => {
  const baseShowResult = showResult;
  let latestResult = null;

  function rootCauseData(result) {
    const analysis = result?.analysis || {};
    return {
      symptom: analysis.symptom_contract || result?.symptom_contract || null,
      rootCause: analysis.root_cause || null,
      goal: analysis.recovery_goal || null,
      scope: analysis.recovery_scope || null,
      recovery: analysis.recovery_loop || result?.recovery || null,
    };
  }

  function statusLabel(status) {
    return {
      confirmed: "Causa confirmada",
      probable: "Causa provável",
      unknown: "Causa ainda não identificada",
      validated: "Resolvido e validado",
      partially_validated: "Resolvido parcialmente",
      approval_required: "Nova aprovação necessária",
      failed: "Recuperação não concluída",
    }[status] || String(status || "Em análise");
  }

  function ensureRecoveryStyles() {
    if (document.querySelector("#root-cause-recovery-styles")) return;
    const style = document.createElement("style");
    style.id = "root-cause-recovery-styles";
    style.textContent = `
      .root-cause-panel{display:grid;gap:14px}.root-cause-header{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.root-cause-header p{margin:4px 0 0;color:var(--muted)}
      .root-cause-state{display:inline-flex;padding:6px 10px;border-radius:999px;font-size:.68rem;font-weight:900;text-transform:uppercase;background:rgba(148,163,184,.14)}
      .root-cause-state.confirmed,.root-cause-state.validated{color:var(--good)}.root-cause-state.probable,.root-cause-state.partially_validated{color:var(--warning)}.root-cause-state.unknown,.root-cause-state.failed{color:var(--bad)}
      .root-cause-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.root-cause-card{padding:14px;border:1px solid var(--border);border-radius:14px;background:color-mix(in srgb,var(--surface) 92%,transparent)}
      .root-cause-card h4{margin:0 0 8px;font-size:.84rem}.root-cause-card p{margin:5px 0;color:var(--muted);font-size:.76rem;line-height:1.55}.root-cause-card ul{margin:8px 0 0;padding-left:18px;color:var(--muted);font-size:.74rem}
      .causal-chain{display:grid;gap:7px}.causal-node{padding:9px 10px;border-left:3px solid var(--accent);border-radius:7px;background:rgba(77,216,255,.06);font-size:.72rem}.causal-arrow{color:var(--muted);font-size:.72rem;padding-left:10px}
      .recovery-timeline{display:grid;gap:9px;margin-top:12px}.recovery-step{padding:11px 12px;border:1px solid var(--border);border-radius:12px;background:rgba(255,255,255,.025)}.recovery-step strong{display:block;font-size:.78rem}.recovery-step p{margin:4px 0 0;color:var(--muted);font-size:.72rem;line-height:1.5}.recovery-step[data-state="validated"]{border-color:color-mix(in srgb,var(--good) 45%,var(--border))}.recovery-step[data-state="new_blocker_found"],.recovery-step[data-state="mapped"]{border-color:color-mix(in srgb,var(--warning) 45%,var(--border))}
      .recovery-pending{margin-top:12px;padding:13px;border:1px solid color-mix(in srgb,var(--warning) 55%,var(--border));border-radius:12px;background:rgba(245,158,11,.07)}.recovery-pending h4{margin:0 0 7px}.recovery-pending p{color:var(--muted);font-size:.74rem}.recovery-pending button{margin-top:8px}
      @media(max-width:760px){.root-cause-grid{grid-template-columns:1fr}.root-cause-header{display:grid}}
    `;
    document.head.appendChild(style);
  }

  function list(items, empty) {
    const rows = (items || []).filter(Boolean);
    if (!rows.length) return `<p>${escapeHtml(empty)}</p>`;
    return `<ul>${rows.map((item) => `<li>${escapeHtml(typeof item === "string" ? item : item.statement || item.description || JSON.stringify(item))}</li>`).join("")}</ul>`;
  }

  function chainMarkup(chain) {
    const rows = (chain || []).filter((item) => item?.statement || typeof item === "string");
    if (!rows.length) return "<p>A cadeia causal ainda não foi sustentada pelas evidências.</p>";
    return `<div class="causal-chain">${rows.map((item, index) => `${index ? '<span class="causal-arrow">↓ produz</span>' : ""}<div class="causal-node">${escapeHtml(typeof item === "string" ? item : item.statement)}</div>`).join("")}</div>`;
  }

  function rootCauseMarkup(result) {
    const { symptom, rootCause, goal, scope } = rootCauseData(result);
    if (!symptom && !rootCause && !goal) return "";
    const status = rootCause?.status || "unknown";
    const allowed = scope?.allowed_correction_tools || [];
    return `<section class="result-section root-cause-panel">
      <div class="root-cause-header"><div><p class="eyebrow">CAUSA E RECUPERAÇÃO</p><h3>O alerta é o ponto de partida, não a conclusão</h3><p>A investigação procura o motivo da falha e acompanha a recuperação até a pós-validação.</p></div><span class="root-cause-state ${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span></div>
      <div class="root-cause-grid">
        <article class="root-cause-card"><h4>Sintoma recebido</h4><p>${escapeHtml(symptom?.statement || "Problema informado pelo operador.")}</p><p><b>Pergunta investigada:</b> ${escapeHtml(rootCause?.investigation_question || symptom?.investigation_question || "Qual causa produziu esse estado?")}</p></article>
        <article class="root-cause-card"><h4>Causa raiz</h4><p>${escapeHtml(rootCause?.statement || "A IA não repetiu o estado do alerta como causa. Ainda falta evidência do mecanismo que provocou a falha.")}</p></article>
        <article class="root-cause-card"><h4>Cadeia causal</h4>${chainMarkup(rootCause?.causal_chain)}</article>
        <article class="root-cause-card"><h4>Critérios para considerar resolvido</h4>${list(goal?.success_criteria, "A pós-validação será definida pelas ferramentas corretivas aprovadas.")}</article>
        <article class="root-cause-card"><h4>Envelope da recuperação</h4>${list(allowed, "Nenhuma ferramenta corretiva foi liberada pelo playbook.")}<p>Mesmo alvo: <b>sim</b> · Banco: <b>bloqueado</b> · Reboot: <b>bloqueado</b> · Container: <b>bloqueado</b></p></article>
      </div>
    </section>`;
  }

  function bindRootCauseSection(result) {
    ensureRecoveryStyles();
    const content = $("#result-content");
    if (!content || content.querySelector(".root-cause-panel")) return;
    const markup = rootCauseMarkup(result);
    if (!markup) return;
    const holder = document.createElement("div");
    holder.innerHTML = markup;
    const firstSection = content.querySelector(".result-section");
    content.insertBefore(holder.firstElementChild, firstSection || content.firstChild);

    const scope = rootCauseData(result).scope;
    const box = content.querySelector(".approval-box");
    if (box && scope) {
      const allowed = scope.allowed_correction_tools || [];
      const paragraph = box.querySelector("p");
      if (paragraph) paragraph.textContent = `A autorização permite até ${scope.max_correction_actions || 0} ação(ões), no mesmo alvo, usando somente: ${allowed.join(", ") || "nenhuma ferramenta"}. Um passo fora desse envelope exigirá nova aprovação.`;
    }
  }

  function recoveryTimelineMarkup(execution) {
    const recovery = execution?.recovery || {};
    const rounds = recovery.rounds || [];
    const pending = execution?.pending_actions || recovery.pending_actions || [];
    const review = execution?.pending_review || {};
    const timeline = rounds.length
      ? `<div class="recovery-timeline">${rounds.map((item) => {
          const phase = item.phase === "blocker_diagnosis" ? "Novo bloqueio investigado" : "Ação corretiva observada";
          const action = item.action?.description || item.action?.tool || item.summary || "Etapa de recuperação";
          const detail = item.phase === "blocker_diagnosis"
            ? `${item.summary || "Bloqueio mapeado"}${item.causal_link ? ` — ${item.causal_link}` : ""}`
            : `${item.result?.tool || "ação"}: ${item.result?.status || item.state || "processada"}`;
          return `<article class="recovery-step" data-state="${escapeHtml(item.state || "")}"><strong>${escapeHtml(phase)} · ${escapeHtml(action)}</strong><p>${escapeHtml(detail)}</p></article>`;
        }).join("")}</div>`
      : "<p>Nenhuma rodada adaptativa foi necessária.</p>";
    const pendingMarkup = pending.length
      ? `<div class="recovery-pending"><h4>Próximo passo descoberto</h4>${pending.map((item) => `<p><b>${escapeHtml(item.description || item.tool)}</b><br>${escapeHtml(item.evidence_reason || item.reason || "Ação necessária após o novo bloqueio.")}</p>`).join("")}<p><b>Revisão:</b> ${escapeHtml(review.reason || (review.approved ? "A segunda IA aprovou a nova proposta." : "A nova proposta não foi aprovada."))}</p>${execution.next_approval_token ? '<button type="button" class="primary-button" id="approve-next-recovery">Aprovar próximo passo seguro</button>' : ""}</div>`
      : "";
    return `<div><span class="root-cause-state ${escapeHtml(execution.status || "failed")}">${escapeHtml(statusLabel(execution.status))}</span><p>${escapeHtml(recovery.summary || "Recuperação processada.")}</p>${timeline}${pendingMarkup}<details class="raw-details"><summary>Ver retorno completo da recuperação</summary><pre>${escapeHtml(JSON.stringify(execution, null, 2))}</pre></details></div>`;
  }

  showResult = function recoveryAwareShowResult(result) {
    latestResult = result;
    baseShowResult(result);
    bindRootCauseSection(result);
  };

  approveActions = async function adaptiveApproveActions() {
    if (!state.approvalToken || !state.currentInvestigationId) return;
    const scope = rootCauseData(latestResult || {}).scope || {};
    const allowed = scope.allowed_correction_tools || [];
    const confirmed = window.confirm(
      `Autorizar a recuperação no mesmo alvo usando somente ${allowed.join(", ") || "as ações exibidas"}? Se surgir um passo fora desse escopo, a aplicação vai parar e pedir outra aprovação.`
    );
    if (!confirmed) return;
    const button = $("#approve-actions") || $("#approve-next-recovery");
    if (button) {
      button.disabled = true;
      button.textContent = "Corrigindo, observando e replanejando...";
    }
    try {
      const execution = await api(`/ui/api/investigations/${encodeURIComponent(state.currentInvestigationId)}/approve`, {
        method: "POST",
        body: { token: state.approvalToken },
      });
      state.approvalToken = execution.next_approval_token || null;
      const box = button?.closest(".approval-box") || button?.closest(".recovery-pending") || $("#result-content");
      if (box) box.innerHTML = recoveryTimelineMarkup(execution);
      if (execution.next_approval_token) {
        toast("A IA mapeou um novo bloqueio. Revise e aprove o próximo passo para continuar.");
        $("#approve-next-recovery")?.addEventListener("click", approveActions);
      } else if (execution.status === "validated") {
        toast("Causa tratada e recuperação pós-validada.");
      } else {
        toast(execution.recovery?.summary || "A recuperação terminou com bloqueios.", "error");
      }
      state.investigationsLoaded = false;
      state.dashboardLoaded = false;
    } catch (error) {
      if (button) {
        button.disabled = false;
        button.textContent = "Aprovar ações seguras";
      }
      toast(error.message, "error");
    }
  };
})();
