(() => {
  const baseShowView = showView;
  const SESSION_STORAGE_KEY = "agent-opencode-session-history-v1";

  state.openCodeLoaded = false;
  state.openCodeStatus = null;
  state.openCodeAllRuns = [];
  state.openCodeSessionId = null;
  state.openCodeCurrentRunIds = new Set();
  state.openCodePolling = new Set();
  state.openCodeExplicitNewSession = false;
  state.openCodeRefreshTimer = null;
  viewMeta.opencode = ["AGENTE DE DESENVOLVIMENTO", "OpenCode integrado"];

  showView = function showViewWithTools(name) {
    baseShowView(name);
    if (name === "opencode") {
      void loadOpenCodeWorkspace();
      startOpenCodeAutoRefresh();
    }
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

  function readStoredSessions() {
    try {
      const parsed = JSON.parse(localStorage.getItem(SESSION_STORAGE_KEY) || "[]");
      return Array.isArray(parsed) ? parsed.filter((item) => item && item.session_id) : [];
    } catch {
      return [];
    }
  }

  function writeStoredSessions(items) {
    const normalized = items
      .filter((item) => item && item.session_id)
      .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")))
      .slice(0, 50);
    localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(normalized));
  }

  function rememberSession(run) {
    if (!run?.session_id) return;
    const stored = readStoredSessions();
    const index = stored.findIndex((item) => item.session_id === run.session_id);
    const previous = index >= 0 ? stored[index] : {};
    const entry = {
      ...previous,
      session_id: run.session_id,
      title: String(previous.title || run.prompt || "Conversa do OpenCode").trim().slice(0, 90),
      updated_at: run.finished_at || run.started_at || run.created_at || new Date().toISOString(),
      model: run.model || previous.model || "",
      status: run.status || previous.status || "completed",
    };
    if (index >= 0) stored.splice(index, 1);
    stored.unshift(entry);
    writeStoredSessions(stored);
  }

  function sortedRuns(items) {
    return [...items].sort((left, right) => String(left.created_at || "").localeCompare(String(right.created_at || "")));
  }

  function currentConversationRuns() {
    const rows = sortedRuns(state.openCodeAllRuns || []);
    if (state.openCodeSessionId) {
      return rows.filter((run) => run.session_id === state.openCodeSessionId || state.openCodeCurrentRunIds.has(run.id));
    }
    return rows.filter((run) => state.openCodeCurrentRunIds.has(run.id));
  }

  function sessionRows() {
    const groups = new Map();
    for (const stored of readStoredSessions()) {
      groups.set(stored.session_id, { ...stored, runs: [] });
    }
    for (const run of state.openCodeAllRuns || []) {
      if (!run.session_id) continue;
      const row = groups.get(run.session_id) || {
        session_id: run.session_id,
        title: run.prompt || "Conversa do OpenCode",
        updated_at: run.created_at,
        model: run.model || "",
        status: run.status,
        runs: [],
      };
      row.runs = [...(row.runs || []), run];
      const oldest = sortedRuns(row.runs)[0];
      const newest = sortedRuns(row.runs).at(-1);
      row.title = String(oldest?.prompt || row.title || "Conversa do OpenCode").trim().slice(0, 90);
      row.updated_at = newest?.finished_at || newest?.started_at || newest?.created_at || row.updated_at;
      row.model = newest?.model || row.model || "";
      row.status = newest?.status || row.status;
      groups.set(run.session_id, row);
      rememberSession(newest || run);
    }
    return [...groups.values()].sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
  }

  function compactDate(value) {
    if (!value) return "sem data";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "sem data";
    return new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function renderSessionHistory() {
    const element = $("#opencode-session-history");
    const count = $("#opencode-session-count");
    if (!element) return;
    const sessions = sessionRows();
    if (count) count.textContent = String(sessions.length);
    if (!sessions.length) {
      element.innerHTML = '<div class="opencode-history-empty">As conversas aparecerão aqui depois da primeira resposta.</div>';
      return;
    }
    element.innerHTML = sessions.map((session) => {
      const active = state.openCodeSessionId === session.session_id;
      return `<button type="button" class="opencode-history-item${active ? " active" : ""}" data-opencode-session="${escapeHtml(session.session_id)}">
        <span class="opencode-history-title">${escapeHtml(session.title || "Conversa do OpenCode")}</span>
        <span class="opencode-history-meta">${escapeHtml(compactDate(session.updated_at))}${session.model ? ` · ${escapeHtml(session.model)}` : ""}</span>
        <span class="opencode-history-state" data-state="${escapeHtml(runStateTone(session.status))}">${escapeHtml(runStateLabel(session.status))}</span>
      </button>`;
    }).join("");
    $$('[data-opencode-session]', element).forEach((button) => button.addEventListener("click", () => {
      state.openCodeSessionId = button.dataset.opencodeSession || null;
      state.openCodeCurrentRunIds.clear();
      state.openCodeExplicitNewSession = false;
      renderSessionHistory();
      renderConversation();
      $("#opencode-prompt")?.focus();
    }));
  }

  function renderConversation() {
    const element = $("#opencode-conversation");
    if (!element) return;
    const runs = currentConversationRuns();
    if (!runs.length) {
      const selected = state.openCodeSessionId
        ? "Sessão selecionada. Envie uma nova mensagem para continuar esta conversa."
        : "Descreva o que precisa ser analisado ou desenvolvido no projeto.";
      element.innerHTML = `<div class="opencode-empty-chat"><span>⌘</span><h3>${state.openCodeSessionId ? "Continue a conversa" : "Nova sessão"}</h3><p>${escapeHtml(selected)} O modo Planejar não altera arquivos; o modo Aplicar exige confirmação explícita.</p></div>`;
      return;
    }

    element.innerHTML = runs.map((run) => {
      const running = ["queued", "running"].includes(run.status);
      const output = run.output || run.error || (running ? "O OpenCode está analisando o projeto..." : "Nenhuma resposta textual foi retornada.");
      return `<div class="opencode-turn">
        <article class="opencode-message user-message"><div class="message-avatar">Você</div><div><div class="message-meta"><strong>Solicitação</strong><span>${escapeHtml(run.agent === "build" ? "Aplicar" : "Planejar")} · ${escapeHtml(run.model || "rota padrão")}</span></div><p>${escapeHtml(run.prompt || "")}</p></div></article>
        <article class="opencode-message assistant-message" data-state="${runStateTone(run.status)}"><div class="message-avatar">OC</div><div><div class="message-meta"><strong>OpenCode</strong><span>${escapeHtml(runStateLabel(run.status))}${run.duration_ms != null ? ` · ${escapeHtml(formatDuration(run.duration_ms))}` : ""}</span></div><pre>${escapeHtml(output)}</pre>${run.session_id ? `<div class="opencode-message-actions"><code>sessão ${escapeHtml(String(run.session_id).slice(0, 12))}</code></div>` : ""}</div></article>
      </div>`;
    }).join("");
    element.scrollTop = element.scrollHeight;
  }

  function toggleBuildConfirmation() {
    const select = $("#opencode-agent");
    const block = $("#opencode-build-confirmation");
    if (!select || !block) return;
    block.hidden = select.value !== "build";
  }

  function startNewSession() {
    state.openCodeSessionId = null;
    state.openCodeCurrentRunIds.clear();
    state.openCodeExplicitNewSession = true;
    renderSessionHistory();
    renderConversation();
    $("#opencode-prompt")?.focus();
    toast("Nova sessão iniciada.");
  }

  function renderOpenCode(data) {
    state.openCodeStatus = data;
    const [label, stateName] = statusLabel(data);
    const ready = data.available && data.configured && data.interface_enabled;
    const canOpenExternal = data.web_reachable && data.web_url;

    $("#opencode-workspace").innerHTML = `<div class="opencode-inline-status compact-status-strip">
      <span><strong>Estado</strong><b class="opencode-state" data-state="${escapeHtml(stateName)}">${escapeHtml(label)}</b></span>
      <span><strong>Versão</strong>${escapeHtml(data.version || "não identificada")}</span>
      <span><strong>Projeto</strong>${escapeHtml(data.workdir || "não configurado")}</span>
      <span><strong>Gateway</strong>${escapeHtml(data.provider || "OmniRoute")} · ${escapeHtml(data.model || "sem rota")}</span>
    </div>
    <div class="opencode-integrated-layout">
      <section class="opencode-chat-panel">
        <header class="opencode-chat-header"><div><p class="eyebrow">SESSÃO DE DESENVOLVIMENTO</p><h3>Converse com o OpenCode</h3></div><button type="button" class="primary-button opencode-new-session" id="opencode-new-session">Nova sessão</button></header>
        <div class="opencode-conversation" id="opencode-conversation"></div>
        <form class="opencode-composer" id="opencode-form">
          <textarea id="opencode-prompt" rows="3" maxlength="12000" placeholder="Ex.: analise os testes que estão falhando e proponha a correção..." ${ready ? "" : "disabled"}></textarea>
          <div class="opencode-composer-controls">
            <label class="opencode-mode-control"><span>Modo</span><select id="opencode-agent" ${ready ? "" : "disabled"}><option value="plan">Planejar — sem alterações</option>${data.allow_build ? '<option value="build">Aplicar — editar e testar</option>' : ""}</select></label>
            <label class="opencode-model-control"><span>Rota</span><select id="opencode-model" ${ready ? "" : "disabled"}>${modelOptions(data)}</select></label>
            <button type="submit" class="primary-button" id="run-opencode" ${ready ? "" : "disabled"}><span class="button-label">Enviar</span><span class="button-spinner" aria-hidden="true"></span></button>
          </div>
          <label class="opencode-build-confirmation" id="opencode-build-confirmation" hidden><input type="checkbox" id="opencode-confirm-build"><span>Autorizo edição e testes somente neste projeto.</span></label>
        </form>
      </section>
      <aside class="opencode-side-panel">
        <section class="opencode-history-section"><div class="opencode-history-header"><div><p class="eyebrow">HISTÓRICO</p><strong>Conversas</strong></div><span id="opencode-session-count">0</span></div><div class="opencode-session-history" id="opencode-session-history"></div></section>
        <section class="opencode-side-safety"><strong>Separação de segurança</strong><p>O OpenCode atua somente no código deste projeto e não herda a conexão SSH das investigações.</p></section>
        <details class="opencode-advanced"><summary>Acesso avançado à interface original</summary><p>Use somente para recursos específicos da interface original.</p><code>${escapeHtml(data.tunnel_command || "")}</code><div>${canOpenExternal ? '<button type="button" class="secondary-button" id="open-opencode-web">Abrir interface original</button>' : ""}<button type="button" class="ghost-button" id="copy-opencode-tunnel">Copiar túnel</button></div></details>
      </aside>
    </div>`;

    renderSessionHistory();
    renderConversation();
    toggleBuildConfirmation();

    $("#opencode-agent")?.addEventListener("change", toggleBuildConfirmation);
    $("#opencode-form")?.addEventListener("submit", submitOpenCodePrompt);
    $("#opencode-new-session")?.addEventListener("click", startNewSession);
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
      state.openCodeAllRuns.push(run);
      state.openCodeCurrentRunIds.add(run.id);
      state.openCodeExplicitNewSession = false;
      $("#opencode-prompt").value = "";
      if ($("#opencode-confirm-build")) $("#opencode-confirm-build").checked = false;
      toggleBuildConfirmation();
      renderConversation();
      renderSessionHistory();
      void pollOpenCodeRun(run.id);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.classList.remove("loading");
    }
  }

  function replaceRun(updated) {
    const index = state.openCodeAllRuns.findIndex((item) => item.id === updated.id);
    if (index >= 0) state.openCodeAllRuns[index] = updated;
    else state.openCodeAllRuns.push(updated);
    if (updated.session_id && state.openCodeCurrentRunIds.has(updated.id)) {
      state.openCodeSessionId = updated.session_id;
      rememberSession(updated);
    }
  }

  async function pollOpenCodeRun(runId) {
    if (!runId || state.openCodePolling.has(runId)) return;
    state.openCodePolling.add(runId);
    const poll = async () => {
      try {
        const run = await api(`/ui/api/tools/opencode/runs/${encodeURIComponent(runId)}`);
        replaceRun(run);
        renderConversation();
        renderSessionHistory();
        if (["queued", "running"].includes(run.status)) {
          window.setTimeout(poll, 1400);
        } else {
          state.openCodePolling.delete(runId);
          rememberSession(run);
          renderSessionHistory();
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

  async function loadOpenCodeRuns({ quiet = false } = {}) {
    try {
      const data = await api("/ui/api/tools/opencode/runs?limit=100");
      state.openCodeAllRuns = data.items || [];
      state.openCodeAllRuns.forEach(rememberSession);
      if (!state.openCodeSessionId && !state.openCodeExplicitNewSession && !state.openCodeCurrentRunIds.size) {
        const latest = sessionRows()[0];
        if (latest?.session_id) state.openCodeSessionId = latest.session_id;
      }
      renderSessionHistory();
      renderConversation();
      state.openCodeAllRuns.filter((item) => ["queued", "running"].includes(item.status)).forEach((item) => void pollOpenCodeRun(item.id));
    } catch (error) {
      if (!quiet) toast(error.message, "error");
    }
  }

  async function refreshOpenCodeStatusOnly() {
    try {
      const data = await api("/ui/api/tools/opencode");
      state.openCodeStatus = data;
      const [label, stateName] = statusLabel(data);
      const stateElement = $(".opencode-state");
      if (stateElement) {
        stateElement.textContent = label;
        stateElement.dataset.state = stateName;
      }
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

  function startOpenCodeAutoRefresh() {
    if (state.openCodeRefreshTimer) return;
    state.openCodeRefreshTimer = window.setInterval(() => {
      if (!$("#view-opencode")?.classList.contains("active") || document.hidden) return;
      void refreshOpenCodeStatusOnly();
      void loadOpenCodeRuns({ quiet: true });
    }, 12000);
  }

  document.addEventListener("DOMContentLoaded", startOpenCodeAutoRefresh);
})();
