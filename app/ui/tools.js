(() => {
  const baseShowView = showView;
  state.openCodeLoaded = false;
  state.openCodeStatus = null;
  state.openCodeRuns = [];
  state.openCodeSessionId = null;
  state.openCodePolling = new Set();
  viewMeta.opencode = ["AGENTE DE DESENVOLVIMENTO", "OpenCode integrado"];

  showView = function showViewWithTools(name) {
    baseShowView(name);
    if (name === "opencode" && !state.openCodeLoaded) void loadOpenCodeWorkspace();
  };

  function statusLabel(data) {
    if (!data.enabled || !data.interface_enabled) return ["Desabilitado", "disabled"];
    if (!data.available) return ["Não instalado", "unavailable"];
    if (!data.configured) return ["Configuração incompleta", "attention"];
    return [data.active_runs ? `${data.active_runs} execução ativa` : "Disponível", "available"];
  }

  function runStateLabel(status) {
    const labels = {
      queued: "Na fila",
      running: "Executando",
      completed: "Concluído",
      failed: "Falhou",
      timeout: "Tempo excedido",
    };
    return labels[status] || status;
  }

  function runStateTone(status) {
    if (status === "completed") return "success";
    if (["failed", "timeout"].includes(status)) return "failure";
    return "running";
  }

  function modelOptions(data) {
    const rows = Array.isArray(data.models) ? data.models : [];
    if (!rows.length && data.model) return `<option value="${escapeHtml(data.model)}">${escapeHtml(data.model)}</option>`;
    return rows.map((item) => `<option value="${escapeHtml(item.value)}" ${item.value === data.model ? "selected" : ""}>${escapeHtml(item.label || item.value)}</option>`).join("");
  }

  function renderConversation() {
    const element = $("#opencode-conversation");
    if (!element) return;
    if (!state.openCodeRuns.length) {
      element.innerHTML = `<div class="opencode-empty-chat"><span>⌘</span><h3>Use o OpenCode aqui dentro</h3><p>Descreva o que precisa ser analisado ou desenvolvido no projeto. O modo Planejar não altera arquivos; o modo Aplicar exige confirmação explícita.</p></div>`;
      return;
    }

    element.innerHTML = state.openCodeRuns.map((run) => {
      const running = ["queued", "running"].includes(run.status);
      const output = run.output || run.error || (running ? "O OpenCode está analisando o projeto..." : "Nenhuma resposta textual foi retornada.");
      const sessionAction = run.session_id
        ? `<button type="button" class="ghost-button opencode-continue-session" data-session="${escapeHtml(run.session_id)}">Continuar esta sessão</button>`
        : "";
      return `<div class="opencode-turn">
        <article class="opencode-message user-message"><div class="message-avatar">Você</div><div><div class="message-meta"><strong>Solicitação</strong><span>${escapeHtml(run.agent === "build" ? "Aplicar" : "Planejar")} · ${escapeHtml(run.model || "rota padrão")}</span></div><p>${escapeHtml(run.prompt || "")}</p></div></article>
        <article class="opencode-message assistant-message" data-state="${runStateTone(run.status)}"><div class="message-avatar">OC</div><div><div class="message-meta"><strong>OpenCode</strong><span>${escapeHtml(runStateLabel(run.status))}${run.duration_ms != null ? ` · ${escapeHtml(formatDuration(run.duration_ms))}` : ""}</span></div><pre>${escapeHtml(output)}</pre><div class="opencode-message-actions">${run.session_id ? `<code>sessão ${escapeHtml(String(run.session_id).slice(0, 12))}</code>` : ""}${sessionAction}</div></div></article>
      </div>`;
    }).join("");

    $$(".opencode-continue-session", element).forEach((button) => button.addEventListener("click", () => {
      state.openCodeSessionId = button.dataset.session || null;
      updateSessionBadge();
      $("#opencode-prompt")?.focus();
      toast("Sessão do OpenCode selecionada para continuar.");
    }));
    element.scrollTop = element.scrollHeight;
  }

  function updateSessionBadge() {
    const badge = $("#opencode-session-badge");
    if (!badge) return;
    badge.textContent = state.openCodeSessionId
      ? `Continuando sessão ${state.openCodeSessionId.slice(0, 12)}`
      : "Nova sessão";
    badge.dataset.active = state.openCodeSessionId ? "true" : "false";
  }

  function toggleBuildConfirmation() {
    const select = $("#opencode-agent");
    const block = $("#opencode-build-confirmation");
    if (!select || !block) return;
    block.hidden = select.value !== "build";
  }

  function renderOpenCode(data) {
    state.openCodeStatus = data;
    const [label, stateName] = statusLabel(data);
    const ready = data.available && data.configured && data.interface_enabled;
    const canOpenExternal = data.web_reachable && data.web_url;

    $("#opencode-workspace").innerHTML = `<div class="opencode-hero compact" data-state="${escapeHtml(stateName)}">
      <div class="opencode-brand"><span>⌘</span><div><p class="eyebrow">OPEN SOURCE CODING AGENT</p><h2>OpenCode dentro do Agent IA</h2><p>Os prompts são executados no projeto configurado e enviados ao OmniRoute. Esta área não recebe automaticamente SSH, bastion ou credenciais dos servidores monitorados.</p></div></div>
      <span class="opencode-state">${escapeHtml(label)}</span>
    </div>
    <div class="opencode-inline-status">
      <span><strong>Versão</strong>${escapeHtml(data.version || "não identificada")}</span>
      <span><strong>Projeto</strong>${escapeHtml(data.workdir || "não configurado")}</span>
      <span><strong>Gateway</strong>${escapeHtml(data.provider || "OmniRoute")} · ${escapeHtml(data.model || "sem rota")}</span>
    </div>
    <div class="opencode-integrated-layout">
      <section class="opencode-chat-panel">
        <header class="opencode-chat-header"><div><p class="eyebrow">SESSÃO DE DESENVOLVIMENTO</p><h3>Converse com o OpenCode</h3></div><div class="opencode-chat-header-actions"><span class="mode-badge" id="opencode-session-badge">Nova sessão</span><button type="button" class="ghost-button" id="opencode-new-session">Nova sessão</button></div></header>
        <div class="opencode-conversation" id="opencode-conversation"></div>
        <form class="opencode-composer" id="opencode-form">
          <textarea id="opencode-prompt" rows="4" maxlength="12000" placeholder="Ex.: analise os testes que estão falhando e proponha a correção..." ${ready ? "" : "disabled"}></textarea>
          <div class="opencode-composer-controls">
            <label><span>Modo</span><select id="opencode-agent" ${ready ? "" : "disabled"}><option value="plan">Planejar — sem alterações</option>${data.allow_build ? '<option value="build">Aplicar — editar e testar</option>' : ""}</select></label>
            <label><span>Rota do OmniRoute</span><select id="opencode-model" ${ready ? "" : "disabled"}>${modelOptions(data)}</select></label>
            <button type="submit" class="primary-button" id="run-opencode" ${ready ? "" : "disabled"}><span class="button-label">Enviar ao OpenCode</span><span class="button-spinner" aria-hidden="true"></span></button>
          </div>
          <label class="opencode-build-confirmation" id="opencode-build-confirmation" hidden><input type="checkbox" id="opencode-confirm-build"><span>Autorizo o OpenCode a editar arquivos e executar comandos somente no diretório deste projeto.</span></label>
        </form>
      </section>
      <aside class="opencode-side-panel">
        <section><p class="eyebrow">COMO FUNCIONA</p><ol><li><span>1</span><p>O Agent IA recebe seu prompt pela interface.</p></li><li><span>2</span><p>O backend executa <code>opencode run</code> no projeto.</p></li><li><span>3</span><p>O OpenCode usa a rota selecionada no OmniRoute.</p></li><li><span>4</span><p>A resposta e os comandos voltam para esta tela.</p></li></ol></section>
        <section class="opencode-side-safety"><strong>Separação de segurança</strong><p>O OpenCode atua no código deste projeto. Ele não herda a conexão SSH usada nas investigações de infraestrutura.</p></section>
        <details class="opencode-advanced"><summary>Acesso avançado à interface original</summary><p>O workspace integrado é o acesso principal. A interface original continua disponível por túnel SSH para recursos específicos do OpenCode.</p><code>${escapeHtml(data.tunnel_command || "")}</code><div>${canOpenExternal ? '<button type="button" class="secondary-button" id="open-opencode-web">Abrir interface original</button>' : ""}<button type="button" class="ghost-button" id="copy-opencode-tunnel">Copiar túnel</button></div></details>
      </aside>
    </div>`;

    renderConversation();
    updateSessionBadge();
    toggleBuildConfirmation();

    $("#opencode-agent")?.addEventListener("change", toggleBuildConfirmation);
    $("#opencode-form")?.addEventListener("submit", submitOpenCodePrompt);
    $("#opencode-new-session")?.addEventListener("click", () => {
      state.openCodeSessionId = null;
      updateSessionBadge();
      $("#opencode-prompt")?.focus();
    });
    $("#open-opencode-web")?.addEventListener("click", () => window.open(data.web_url, "_blank", "noopener,noreferrer"));
    $("#copy-opencode-tunnel")?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(data.tunnel_command || "");
        toast("Comando do túnel copiado.");
      } catch {
        toast("Não foi possível copiar o comando automaticamente.", "error");
      }
    });
  }

  async function submitOpenCodePrompt(event) {
    event.preventDefault();
    const prompt = $("#opencode-prompt")?.value.trim() || "";
    const agent = $("#opencode-agent")?.value || "plan";
    const model = $("#opencode-model")?.value || null;
    const confirmChanges = Boolean($("#opencode-confirm-build")?.checked);
    if (prompt.length < 3) {
      toast("Descreva a tarefa para o OpenCode.", "error");
      return;
    }
    if (agent === "build" && !confirmChanges) {
      toast("Confirme a autorização para alterações no projeto.", "error");
      return;
    }

    const button = $("#run-opencode");
    button.disabled = true;
    button.classList.add("loading");
    try {
      const run = await api("/ui/api/tools/opencode/runs", {
        method: "POST",
        body: {
          prompt,
          agent,
          model,
          session_id: state.openCodeSessionId,
          confirm_changes: confirmChanges,
        },
      });
      state.openCodeRuns.push(run);
      $("#opencode-prompt").value = "";
      if ($("#opencode-confirm-build")) $("#opencode-confirm-build").checked = false;
      renderConversation();
      pollOpenCodeRun(run.id);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.classList.remove("loading");
    }
  }

  function replaceRun(updated) {
    const index = state.openCodeRuns.findIndex((item) => item.id === updated.id);
    if (index >= 0) state.openCodeRuns[index] = updated;
    else state.openCodeRuns.push(updated);
    if (updated.session_id) state.openCodeSessionId = updated.session_id;
  }

  async function pollOpenCodeRun(runId) {
    if (!runId || state.openCodePolling.has(runId)) return;
    state.openCodePolling.add(runId);
    const poll = async () => {
      try {
        const run = await api(`/ui/api/tools/opencode/runs/${encodeURIComponent(runId)}`);
        replaceRun(run);
        renderConversation();
        updateSessionBadge();
        if (["queued", "running"].includes(run.status)) {
          window.setTimeout(poll, 1400);
        } else {
          state.openCodePolling.delete(runId);
          toast(run.status === "completed" ? "OpenCode concluiu a tarefa." : (run.error || "A execução do OpenCode falhou."), run.status === "completed" ? "success" : "error");
          void refreshOpenCodeStatusOnly();
        }
      } catch (error) {
        state.openCodePolling.delete(runId);
        toast(error.message, "error");
      }
    };
    await poll();
  }

  async function loadOpenCodeRuns() {
    try {
      const data = await api("/ui/api/tools/opencode/runs?limit=20");
      state.openCodeRuns = (data.items || []).slice().reverse();
      renderConversation();
      state.openCodeRuns.filter((item) => ["queued", "running"].includes(item.status)).forEach((item) => pollOpenCodeRun(item.id));
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function refreshOpenCodeStatusOnly() {
    try {
      const data = await api("/ui/api/tools/opencode");
      state.openCodeStatus = data;
      const stateElement = $(".opencode-state");
      if (stateElement) stateElement.textContent = statusLabel(data)[0];
    } catch {
      // A conversa permanece disponível mesmo quando a atualização de status falha.
    }
  }

  async function loadOpenCodeWorkspace(force = false) {
    if (state.openCodeLoaded && !force) return;
    $("#opencode-workspace").innerHTML = '<div class="empty-state">Preparando o workspace integrado do OpenCode...</div>';
    try {
      const data = await api("/ui/api/tools/opencode");
      renderOpenCode(data);
      state.openCodeLoaded = true;
      await loadOpenCodeRuns();
    } catch (error) {
      $("#opencode-workspace").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
      toast(error.message, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("#refresh-opencode")?.addEventListener("click", () => loadOpenCodeWorkspace(true));
  });
})();
