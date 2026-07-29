(() => {
  const baseShowResult = showResult;

  function enhanceProviderStateField() {
    const checkbox = $("#provider-enabled");
    const field = checkbox?.closest("label");
    const control = checkbox?.parentElement;
    if (!checkbox || !field || !control || control.classList.contains("provider-toggle")) return;

    field.classList.add("provider-enabled-field");
    control.classList.add("provider-toggle");
    [...control.childNodes].forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) node.remove();
    });
    const track = document.createElement("span");
    track.className = "provider-toggle-track";
    track.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.className = "provider-toggle-label";
    text.textContent = "Habilitado";
    checkbox.after(track, text);
  }

  function playbookModalMarkup() {
    return `<aside class="playbook-modal" id="playbook-editor-modal" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="playbook-editor-title">
      <div class="playbook-modal-backdrop" data-close-playbook-modal></div>
      <div class="playbook-modal-panel">
        <header class="playbook-modal-header"><div><p class="eyebrow">PLAYBOOK OPERACIONAL</p><h2 id="playbook-editor-title">Adicionar playbook</h2></div><button class="icon-button" type="button" data-close-playbook-modal aria-label="Fechar playbook">×</button></header>
        <form class="playbook-editor" id="playbook-editor-form">
          <div class="playbook-editor-grid">
            <label><span>Identificador</span><input id="playbook-editor-id" placeholder="ex.: diagnostico-swap" required></label>
            <label class="wide"><span>Título</span><input id="playbook-editor-name" placeholder="Diagnóstico de memória e swap" required></label>
            <label><span>Prioridade</span><input id="playbook-editor-priority" type="number" min="0" max="999" value="20" required></label>
            <label class="wide"><span>Perfis</span><input id="playbook-editor-profiles" placeholder="linux_generic, oracle_linux" value="linux_generic"></label>
            <label class="full"><span>Padrões de correspondência</span><textarea id="playbook-editor-patterns" placeholder="Uma expressão regular por linha" required></textarea></label>
            <label class="full"><span>Etapas estruturadas em YAML</span><textarea class="playbook-steps" id="playbook-editor-steps" spellcheck="false" required></textarea></label>
          </div>
          <div class="playbook-editor-note">A interface aceita somente ferramentas estruturadas e de leitura. Comandos shell e ferramentas corretivas são recusados pelo backend. O playbook só entra no catálogo depois de você revisar e salvar.</div>
          <div class="playbook-editor-actions"><span class="action-spacer"></span><button type="button" class="secondary-button" data-close-playbook-modal>Cancelar</button><button type="submit" class="primary-button" id="playbook-editor-save">Salvar playbook</button></div>
        </form>
      </div>
    </aside>`;
  }

  function defaultSteps() {
    return `- tool: system.basics\n  arguments: {}\n  purpose: Identificar o host, kernel, uptime e horário.\n`;
  }

  function splitValues(value) {
    return String(value || "").split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
  }

  function openPlaybookEditor(draft = null) {
    const modal = $("#playbook-editor-modal");
    if (!modal) return;
    $("#playbook-editor-title").textContent = draft ? "Revisar rascunho de playbook" : "Adicionar playbook";
    $("#playbook-editor-id").value = draft?.id || "";
    $("#playbook-editor-name").value = draft?.title || "";
    $("#playbook-editor-priority").value = draft?.priority ?? 20;
    $("#playbook-editor-profiles").value = (draft?.profiles || ["linux_generic"]).join(", ");
    $("#playbook-editor-patterns").value = (draft?.patterns || []).join("\n");
    $("#playbook-editor-steps").value = draft?.steps_yaml || defaultSteps();
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("settings-modal-open");
    window.setTimeout(() => $("#playbook-editor-id")?.focus(), 40);
  }

  function closePlaybookEditor() {
    const modal = $("#playbook-editor-modal");
    modal?.classList.remove("open");
    modal?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("settings-modal-open");
  }

  async function savePlaybook(event) {
    event.preventDefault();
    const button = $("#playbook-editor-save");
    const payload = {
      id: $("#playbook-editor-id").value.trim().toLowerCase(),
      title: $("#playbook-editor-name").value.trim(),
      priority: Number($("#playbook-editor-priority").value || 20),
      profiles: splitValues($("#playbook-editor-profiles").value),
      patterns: splitValues($("#playbook-editor-patterns").value),
      steps_yaml: $("#playbook-editor-steps").value,
    };
    button.disabled = true;
    button.textContent = "Salvando...";
    try {
      const response = await api("/ui/api/playbooks", { method: "POST", body: payload });
      toast(response.message || "Playbook salvo.");
      closePlaybookEditor();
      state.playbooksLoaded = false;
      await loadPlaybooks();
      await loadPlaybookOptions();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Salvar playbook";
    }
  }

  function resultPlaybook(result) {
    if (result?.playbook?.id) return result.playbook;
    for (const plan of result?.plans || []) {
      if (plan?.playbook?.id) return plan.playbook;
    }
    return null;
  }

  function appendPlaybookSuggestion(result) {
    const investigationId = result?.investigation_id || result?.id || state.currentInvestigationId;
    if (!investigationId || resultPlaybook(result)) return;
    const content = $("#result-content");
    if (!content || content.querySelector("[data-create-playbook-draft]")) return;
    const section = document.createElement("section");
    section.className = "result-section playbook-suggestion";
    section.innerHTML = `<h3>Nenhum playbook correspondente</h3><p>A investigação foi concluída sem um playbook específico. Você pode gerar um rascunho com o objetivo e as ferramentas utilizadas, revisar e só então salvar no catálogo.</p><button type="button" class="primary-button" data-create-playbook-draft="${escapeHtml(investigationId)}">Criar rascunho de playbook</button>`;
    const raw = content.querySelector(".raw-details");
    content.insertBefore(section, raw || null);
    section.querySelector("[data-create-playbook-draft]").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Gerando rascunho...";
      try {
        const response = await api(`/ui/api/investigations/${encodeURIComponent(investigationId)}/playbook-draft`, { method: "POST" });
        openPlaybookEditor(response.draft);
      } catch (error) {
        toast(error.message, "error");
      } finally {
        button.disabled = false;
        button.textContent = "Criar rascunho de playbook";
      }
    });
  }

  showResult = function showResultWithPlaybookSuggestion(result) {
    baseShowResult(result);
    appendPlaybookSuggestion(result);
  };

  function setupPlaybookManagement() {
    const header = $("#view-playbooks .panel-header");
    if (header && !$("#add-playbook")) {
      const badge = header.querySelector(".mode-badge");
      const actions = document.createElement("div");
      actions.className = "playbook-header-actions";
      if (badge) actions.appendChild(badge);
      const button = document.createElement("button");
      button.type = "button";
      button.id = "add-playbook";
      button.className = "primary-button";
      button.textContent = "Adicionar playbook";
      button.addEventListener("click", () => openPlaybookEditor());
      actions.appendChild(button);
      header.appendChild(actions);
    }

    if (!$("#playbook-editor-modal")) document.body.insertAdjacentHTML("beforeend", playbookModalMarkup());
    $("#playbook-editor-form")?.addEventListener("submit", savePlaybook);
    $$('[data-close-playbook-modal]').forEach((button) => button.addEventListener("click", closePlaybookEditor));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && $("#playbook-editor-modal")?.classList.contains("open")) closePlaybookEditor();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    enhanceProviderStateField();
    setupPlaybookManagement();
  });
})();
