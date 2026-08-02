(() => {
  const baseShowResult = showResult;
  let latestResult = null;

  function investigationId(result) {
    return String(result?.investigation_id || result?.id || "");
  }

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
      resolved: "Problema sanado",
      unresolved: "Problema ainda presente",
      inconclusive: "Validação inconclusiva",
    }[status] || String(status || "Em análise");
  }

  function ensureRecoveryStyles() {
    if (document.querySelector("#root-cause-recovery-styles")) return;
    const style = document.createElement("style");
    style.id = "root-cause-recovery-styles";
    style.textContent = `
      .root-cause-panel{display:grid;gap:14px}.root-cause-header{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.root-cause-header p{margin:4px 0 0;color:var(--muted)}
      .root-cause-state{display:inline-flex;padding:6px 10px;border-radius:999px;font-size:.68rem;font-weight:900;text-transform:uppercase;background:rgba(148,163,184,.14)}
      .root-cause-state.confirmed,.root-cause-state.validated,.root-cause-state.resolved{color:var(--good)}.root-cause-state.probable,.root-cause-state.partially_validated,.root-cause-state.inconclusive{color:var(--warning)}.root-cause-state.unknown,.root-cause-state.failed,.root-cause-state.unresolved{color:var(--bad)}
      .root-cause-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.root-cause-card{padding:14px;border:1px solid var(--border);border-radius:14px;background:color-mix(in srgb,var(--surface) 92%,transparent)}
      .root-cause-card h4{margin:0 0 8px;font-size:.84rem}.root-cause-card p{margin:5px 0;color:var(--muted);font-size:.76rem;line-height:1.55}.root-cause-card ul{margin:8px 0 0;padding-left:18px;color:var(--muted);font-size:.74rem}
      .causal-chain{display:grid;gap:7px}.causal-node{padding:9px 10px;border-left:3px solid var(--accent);border-radius:7px;background:rgba(77,216,255,.06);font-size:.72rem}.causal-arrow{color:var(--muted);font-size:.72rem;padding-left:10px}
      .recovery-timeline{display:grid;gap:9px;margin-top:12px}.recovery-step{padding:11px 12px;border:1px solid var(--border);border-radius:12px;background:rgba(255,255,255,.025)}.recovery-step strong{display:block;font-size:.78rem}.recovery-step p{margin:4px 0 0;color:var(--muted);font-size:.72rem;line-height:1.5}.recovery-step[data-state="validated"],.recovery-step[data-state="resolved"]{border-color:color-mix(in srgb,var(--good) 45%,var(--border))}.recovery-step[data-state="new_blocker_found"],.recovery-step[data-state="mapped"],.recovery-step[data-state="inconclusive"]{border-color:color-mix(in srgb,var(--warning) 45%,var(--border))}
      .recovery-pending{margin-top:12px;padding:13px;border:1px solid color-mix(in srgb,var(--warning) 55%,var(--border));border-radius:12px;background:rgba(245,158,11,.07)}.recovery-pending h4{margin:0 0 7px}.recovery-pending p{color:var(--muted);font-size:.74rem}.recovery-pending button{margin-top:8px}
      .correction-request-panel{display:grid;gap:13px;border-color:color-mix(in srgb,var(--accent) 45%,var(--border));background:linear-gradient(145deg,rgba(77,216,255,.07),rgba(99,102,241,.04))}.correction-request-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}.correction-request-head p{margin:5px 0 0;color:var(--muted)}
      .correction-request-actions{display:flex;gap:9px;flex-wrap:wrap}.correction-plan-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.correction-plan-card{padding:13px;border:1px solid var(--border);border-radius:12px;background:rgba(0,0,0,.1)}.correction-plan-card h4{margin:0 0 7px;font-size:.8rem}.correction-plan-card p,.correction-plan-card li{color:var(--muted);font-size:.73rem;line-height:1.5}.correction-plan-card ul{margin:7px 0 0;padding-left:18px}.correction-plan-card[data-tone="warning"]{border-color:color-mix(in srgb,var(--warning) 50%,var(--border));background:rgba(245,158,11,.06)}.correction-plan-card[data-tone="blocked"]{border-color:color-mix(in srgb,var(--bad) 45%,var(--border));background:rgba(239,68,68,.05)}
      .manual-restart-box{margin-top:12px;padding:14px;border:1px solid color-mix(in srgb,var(--warning) 55%,var(--border));border-radius:12px;background:rgba(245,158,11,.07)}.manual-restart-box h4{margin:0 0 7px}.manual-restart-box p{color:var(--muted);font-size:.74rem;line-height:1.55}.manual-restart-box button{margin-top:8px}
      @media(max-width:760px){.root-cause-grid,.correction-plan-grid{grid-template-columns:1fr}.root-cause-header,.correction-request-head{display:grid}}
    `;
    document.head.appendChild(style);
  }

  function list(items, empty) {
    const rows = (items || []).filter(Boolean);
    if (!rows.length) return `<p>${escapeHtml(empty)}</p>`;
    return `<ul>${rows.map((item) => `<li>${escapeHtml(typeof item === "string" ? item : item.statement || item.description || item.label || JSON.stringify(item))}</li>`).join("")}</ul>`;
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
        <article class="root-cause-card"><h4>Envelope da recuperação</h4>${list(allowed, "Nenhuma ferramenta corretiva foi liberada pelo playbook.")}<p>Mesmo alvo: <b>sim</b> · Banco: <b>bloqueado</b> · Reinício da máquina: <b>manual</b> · Container: <b>bloqueado</b></p></article>
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
  }

  function shouldOfferCorrection(result) {
    const id = investigationId(result);
    if (!id) return false;
    const analysis = result?.analysis || {};
    const status = String(analysis.status || result?.status || "").toLowerCase();
    const correctionStatus = String(analysis.correction_status || "").toLowerCase();
    const recoveryState = String(analysis.recovery_state || "").toLowerCase();
    if (["healthy", "resolved", "validated"].includes(status)) return false;
    if (correctionStatus === "validated" || recoveryState === "resolved_and_validated") return false;
    return true;
  }

  function correctionRequestMarkup(result) {
    if (!shouldOfferCorrection(result)) return "";
    return `<section class="result-section correction-request-panel">
      <div class="correction-request-head"><div><p class="eyebrow">DECISÃO DO ANALISTA</p><h3>Deseja solicitar a correção?</h3><p>A IA vai revalidar a proposta, identificar reinícios de serviço e verificar se existe evidência de que a VM ou o servidor precisa ser reiniciado. Nada será executado antes da sua confirmação.</p></div><span class="root-cause-state probable">Aguardando decisão</span></div>
      <div class="correction-request-actions"><button type="button" class="primary-button" data-request-correction="${escapeHtml(investigationId(result))}">Solicitar correção</button></div>
    </section>`;
  }

  function actionLabel(item) {
    const args = item?.arguments || {};
    const target = args.unit || args.service || args.site || args.container || "";
    return item?.description || `${item?.tool || "Ação"}${target ? ` · ${target}` : ""}`;
  }

  function readinessMarkup(response) {
    const readiness = response.correction_readiness || {};
    const service = readiness.service_restart || {};
    const host = readiness.host_restart || {};
    const actions = response.actions || [];
    const hostTone = host.status === "required" || host.status === "recommended" ? "warning" : "";
    const serviceRows = (service.items || []).map((item) => `${item.operation} · ${item.target}`);
    return `<div class="correction-request-head"><div><p class="eyebrow">PLANO PRÉ-CORREÇÃO</p><h3>Revise antes de autorizar</h3><p>${escapeHtml(response.message || readiness.policy_message || "A proposta foi preparada para revisão.")}</p></div><span class="root-cause-state ${response.can_execute ? "probable" : "failed"}">${response.can_execute ? "Pronta para confirmação" : "Execução bloqueada"}</span></div>
      <div class="correction-plan-grid">
        <article class="correction-plan-card"><h4>Ações propostas</h4>${list(actions.map(actionLabel), "Nenhuma ação estruturada foi liberada.")}</article>
        <article class="correction-plan-card"><h4>Reinício de serviço ou componente</h4><p>${escapeHtml(service.reason || "Não identificado.")}</p>${list(serviceRows, "Nenhum reinício de serviço previsto.")}</article>
        <article class="correction-plan-card" data-tone="${hostTone}"><h4>Reinício da VM ou do servidor</h4><p><b>${escapeHtml(host.status === "required" ? "Necessário" : host.status === "recommended" ? "Recomendado" : "Não necessário pelas evidências atuais")}</b></p><p>${escapeHtml(host.reason || "Sem avaliação disponível.")}</p>${list(host.evidence, "Nenhuma evidência de reinício obrigatório foi encontrada.")}</article>
        <article class="correction-plan-card" data-tone="${response.can_execute ? "" : "blocked"}"><h4>Política do ambiente</h4><p>${escapeHtml(readiness.policy_message || response.reason || "")}</p><p>${escapeHtml(response.reason || "As ações permitidas exigem aprovação humana e pós-validação.")}</p></article>
        <article class="correction-plan-card"><h4>Nova varredura</h4>${list(readiness.validation_plan, "A aplicação executará as validações disponíveis após a ação.")}</article>
      </div>`;
  }

  function correctionChoiceMarkup(response) {
    const host = response?.correction_readiness?.host_restart || {};
    if (!response.can_execute || !response.approval_token) {
      return `<div class="correction-request-actions"><button type="button" class="secondary-button" data-close-correction-plan>Fechar plano</button></div>`;
    }
    if (host.decision_required) {
      return `<div class="correction-request-actions">
        <button type="button" class="primary-button" data-correction-choice="without_restart">Executar sem reiniciar a máquina</button>
        <button type="button" class="secondary-button" data-correction-choice="manual_restart">Executar ações e preparar reinício manual</button>
      </div>`;
    }
    return `<div class="correction-request-actions"><button type="button" class="primary-button" data-correction-choice="without_restart">Confirmar e executar correção</button></div>`;
  }

  async function runRecheck(investigationIdValue, button = null) {
    if (!investigationIdValue) return;
    if (button) {
      button.disabled = true;
      button.textContent = "Executando nova varredura...";
    }
    try {
      const recheck = await api(`/ui/api/investigations/${encodeURIComponent(investigationIdValue)}/recheck`, { method: "POST" });
      toast("Nova varredura concluída. O resultado atual foi comparado com o incidente anterior.");
      showResult(recheck);
      state.investigationsLoaded = false;
      state.dashboardLoaded = false;
    } catch (error) {
      toast(error.message, "error");
      if (button) {
        button.disabled = false;
        button.textContent = "Já reiniciei; executar nova varredura";
      }
    }
  }

  function manualRestartMarkup(investigationIdValue) {
    return `<div class="manual-restart-box"><h4>Reinício manual aguardado</h4><p>As ações aprovadas foram processadas. Realize o reinício da VM ou do servidor dentro da janela autorizada. Quando o host voltar e o acesso estiver estável, confirme abaixo para a IA executar outra varredura.</p><button type="button" class="primary-button" data-manual-recheck="${escapeHtml(investigationIdValue)}">Já reiniciei; executar nova varredura</button></div>`;
  }

  function recoveryTimelineMarkup(execution) {
    const recovery = execution?.recovery || {};
    const rounds = recovery.rounds || [];
    const pending = execution?.pending_actions || recovery.pending_actions || [];
    const review = execution?.pending_review || {};
    const comparison = execution?.before_after || {};
    const timeline = rounds.length
      ? `<div class="recovery-timeline">${rounds.map((item) => {
          const phase = item.phase === "blocker_diagnosis" ? "Novo bloqueio investigado" : "Ação corretiva observada";
          const action = item.action?.description || item.action?.tool || item.summary || "Etapa de recuperação";
          const detail = item.phase === "blocker_diagnosis"
            ? `${item.summary || "Bloqueio mapeado"}${item.causal_link ? ` — ${item.causal_link}` : ""}`
            : `${item.result?.tool || "ação"}: ${item.result?.status || item.state || "processada"}`;
          return `<article class="recovery-step" data-state="${escapeHtml(item.state || "")}"><strong>${escapeHtml(phase)} · ${escapeHtml(action)}</strong><p>${escapeHtml(detail)}</p></article>`;
        }).join("")}</div>`
      : "<p>Nenhuma rodada adaptativa adicional foi necessária.</p>";
    const validationMarkup = `<article class="recovery-step" data-state="${escapeHtml(execution.status === "validated" ? "resolved" : "inconclusive")}"><strong>Varredura e pós-validação</strong><p>${escapeHtml(comparison.summary || recovery.summary || "As validações posteriores foram processadas.")}</p></article>`;
    const pendingMarkup = pending.length
      ? `<div class="recovery-pending"><h4>Próximo passo descoberto</h4>${pending.map((item) => `<p><b>${escapeHtml(item.description || item.tool)}</b><br>${escapeHtml(item.evidence_reason || item.reason || "Ação necessária após o novo bloqueio.")}</p>`).join("")}<p><b>Revisão:</b> ${escapeHtml(review.reason || (review.approved ? "A segunda IA aprovou a nova proposta." : "A nova proposta não foi aprovada."))}</p>${execution.next_approval_token ? '<button type="button" class="primary-button" id="approve-next-recovery">Aprovar próximo passo seguro</button>' : ""}</div>`
      : "";
    return `<div><span class="root-cause-state ${escapeHtml(execution.status || "failed")}">${escapeHtml(statusLabel(execution.status))}</span><p>${escapeHtml(recovery.summary || "Recuperação processada.")}</p>${timeline}<div class="recovery-timeline">${validationMarkup}</div>${pendingMarkup}<details class="raw-details"><summary>Ver retorno completo da recuperação</summary><pre>${escapeHtml(JSON.stringify(execution, null, 2))}</pre></details></div>`;
  }

  async function executeCorrectionToken(token, button, { manualRestart = false } = {}) {
    const id = state.currentInvestigationId || investigationId(latestResult || {});
    if (!token || !id) return;
    const message = manualRestart
      ? "Executar agora as ações aprovadas e, depois, aguardar que o reinício da máquina seja realizado manualmente pelo analista?"
      : "Executar as ações aprovadas sem reiniciar a VM ou o servidor e realizar a pós-validação?";
    if (!window.confirm(message)) return;

    if (button) {
      button.disabled = true;
      button.textContent = "Corrigindo, observando e replanejando...";
    }
    try {
      const execution = await api(`/ui/api/investigations/${encodeURIComponent(id)}/approve`, {
        method: "POST",
        body: { token },
      });
      state.approvalToken = execution.next_approval_token || null;
      const section = button?.closest(".correction-request-panel") || button?.closest(".approval-box") || button?.closest(".recovery-pending") || $("#result-content");
      if (section) {
        section.innerHTML = recoveryTimelineMarkup(execution);
        if (manualRestart && !execution.next_approval_token) {
          section.insertAdjacentHTML("beforeend", manualRestartMarkup(id));
          section.querySelector("[data-manual-recheck]")?.addEventListener("click", (event) => runRecheck(id, event.currentTarget));
        }
      }
      if (execution.next_approval_token) {
        toast("A IA encontrou outro bloqueio e preparou o próximo passo para revisão.");
        $("#approve-next-recovery")?.addEventListener("click", approveActions);
      } else if (manualRestart) {
        toast("Ações processadas. Aguarde o reinício manual antes da nova varredura.");
      } else {
        toast(execution.status === "validated" ? "Correção processada. Iniciando nova varredura." : "A correção terminou com bloqueios. Iniciando nova varredura.");
        await runRecheck(id);
      }
      state.investigationsLoaded = false;
      state.dashboardLoaded = false;
    } catch (error) {
      if (button) {
        button.disabled = false;
        button.textContent = "Confirmar e executar correção";
      }
      toast(error.message, "error");
    }
  }

  function bindPreparedCorrection(response, section) {
    section.innerHTML = readinessMarkup(response) + correctionChoiceMarkup(response);
    section.querySelector("[data-close-correction-plan]")?.addEventListener("click", () => {
      section.remove();
    });
    section.querySelectorAll("[data-correction-choice]").forEach((button) => button.addEventListener("click", async () => {
      state.currentInvestigationId = response.investigation_id;
      state.approvalToken = response.approval_token;
      await executeCorrectionToken(response.approval_token, button, {
        manualRestart: button.dataset.correctionChoice === "manual_restart",
      });
    }));
  }

  function bindCorrectionRequest(result) {
    ensureRecoveryStyles();
    const content = $("#result-content");
    if (!content) return;
    content.querySelector(".correction-continuation")?.remove();
    content.querySelector(".approval-box")?.remove();
    if (content.querySelector(".correction-request-panel")) return;
    const markup = correctionRequestMarkup(result);
    if (!markup) return;
    const holder = document.createElement("div");
    holder.innerHTML = markup;
    const section = holder.firstElementChild;
    const actions = content.querySelector(".result-actions");
    content.insertBefore(section, actions || content.querySelector(".raw-details") || null);
    section.querySelector("[data-request-correction]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Verificando impacto e reinícios...";
      try {
        const response = await api(`/ui/api/investigations/${encodeURIComponent(investigationId(result))}/prepare-correction`, { method: "POST" });
        bindPreparedCorrection(response, section);
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
        button.textContent = "Solicitar correção";
      }
    });
  }

  showResult = function recoveryAwareShowResult(result) {
    latestResult = result;
    baseShowResult(result);
    state.currentInvestigationId = investigationId(result) || state.currentInvestigationId;
    bindRootCauseSection(result);
    bindCorrectionRequest(result);
  };

  approveActions = async function adaptiveApproveActions() {
    if (!state.approvalToken || !state.currentInvestigationId) return;
    const button = $("#approve-actions") || $("#approve-next-recovery");
    await executeCorrectionToken(state.approvalToken, button, { manualRestart: false });
  };
})();
