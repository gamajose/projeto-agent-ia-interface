(() => {
  const baseShowResult = showResult;
  const importHistory = [];
  let currentSourceFilename = "";

  function enhanceProviderStateField() {
    const checkbox = $("#provider-enabled");
    const field = checkbox?.closest("label");
    const control = checkbox?.parentElement;
    if (!checkbox || !field || !control || control.classList.contains("provider-toggle")) return;
    field.classList.add("provider-enabled-field");
    control.classList.add("provider-toggle");
    [...control.childNodes].forEach((node) => { if (node.nodeType === Node.TEXT_NODE) node.remove(); });
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
            <label class="full"><span>Resumo extraído</span><textarea id="playbook-editor-summary" placeholder="Resumo do objetivo, cenário e uso do playbook"></textarea></label>
            <label class="full"><span>Entradas necessárias</span><textarea id="playbook-editor-inputs" placeholder="Uma entrada por linha"></textarea></label>
            <label class="full"><span>Regras de segurança</span><textarea id="playbook-editor-safety" placeholder="Uma regra por linha"></textarea></label>
            <label class="full"><span>Validações finais</span><textarea id="playbook-editor-validations" placeholder="Uma validação por linha"></textarea></label>
            <label class="full"><span>Observações da importação</span><textarea id="playbook-editor-notes" placeholder="Você pode apagar, editar ou acrescentar observações"></textarea></label>
            <label class="full"><span>Etapas estruturadas em YAML</span><textarea class="playbook-steps" id="playbook-editor-steps" spellcheck="false" required></textarea></label>
          </div>
          <div class="playbook-editor-note">Todos os campos acima podem ser alterados antes de salvar. O backend continua recusando comandos shell e ferramentas corretivas.</div>
          <div class="playbook-editor-actions"><span class="action-spacer"></span><button type="button" class="secondary-button" data-close-playbook-modal>Cancelar</button><button type="submit" class="primary-button" id="playbook-editor-save">Salvar playbook</button></div>
        </form>
      </div>
    </aside>`;
  }

  function importLogMarkup() {
    return `<article class="playbook-import-log" id="playbook-import-log">
      <div class="playbook-import-log-head"><div><p class="eyebrow">ATIVIDADE</p><h4>Log de importação</h4></div><button type="button" class="ghost-button" id="clear-playbook-import-log">Limpar</button></div>
      <div id="playbook-import-log-items" class="playbook-import-log-items"><p class="empty-state">Nenhuma importação nesta sessão.</p></div>
    </article>`;
  }

  function nowLabel() { return new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }

  function addImportLog(status, title, detail = "") {
    importHistory.unshift({ status, title, detail, time: nowLabel() });
    if (importHistory.length > 20) importHistory.length = 20;
    renderImportLog();
  }

  function renderImportLog() {
    const container = $("#playbook-import-log-items");
    if (!container) return;
    container.innerHTML = importHistory.length ? importHistory.map((item) => `<div class="playbook-import-log-item" data-status="${escapeHtml(item.status)}"><span class="playbook-import-log-dot"></span><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail || "")}</p></div><time>${escapeHtml(item.time)}</time></div>`).join("") : '<p class="empty-state">Nenhuma importação nesta sessão.</p>';
  }

  function defaultSteps() { return `- tool: system.basics\n  arguments: {}\n  purpose: Identificar o host, kernel, uptime e horário.\n`; }
  function splitValues(value) { return String(value || "").split(/[\n,]/).map((item) => item.trim()).filter(Boolean); }
  function joinValues(value) {
    if (Array.isArray(value)) return value.filter(Boolean).join("\n");
    if (typeof value === "string") return value;
    return "";
  }

  function openPlaybookEditor(draft = null) {
    const modal = $("#playbook-editor-modal");
    if (!modal) return;
    const imported = Boolean(draft?.source_filename);
    currentSourceFilename = draft?.source_filename || "";
    $("#playbook-editor-title").textContent = imported ? "Revisar playbook importado" : (draft ? "Revisar rascunho de playbook" : "Adicionar playbook");
    $("#playbook-editor-id").value = draft?.id || "";
    $("#playbook-editor-name").value = draft?.title || "";
    $("#playbook-editor-priority").value = draft?.priority ?? 20;
    $("#playbook-editor-profiles").value = (draft?.profiles || ["linux_generic"]).join(", ");
    $("#playbook-editor-patterns").value = joinValues(draft?.patterns);
    $("#playbook-editor-summary").value = draft?.extracted_summary || draft?.summary || "";
    $("#playbook-editor-inputs").value = joinValues(draft?.required_inputs);
    $("#playbook-editor-safety").value = joinValues(draft?.safety_rules);
    $("#playbook-editor-validations").value = joinValues(draft?.validation_notes || draft?.validation);
    $("#playbook-editor-notes").value = joinValues(draft?.import_warnings || draft?.import_notes);
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
      summary: $("#playbook-editor-summary").value.trim(),
      required_inputs: splitValues($("#playbook-editor-inputs").value),
      safety_rules: splitValues($("#playbook-editor-safety").value),
      validation_notes: splitValues($("#playbook-editor-validations").value),
      import_notes: splitValues($("#playbook-editor-notes").value),
      source_filename: currentSourceFilename,
      steps_yaml: $("#playbook-editor-steps").value,
    };
    button.disabled = true;
    button.textContent = "Salvando...";
    try {
      const response = await api("/ui/api/playbooks", { method: "POST", body: payload });
      addImportLog("success", "Playbook salvo", `${payload.title} foi adicionado ao catálogo.`);
      toast(response.message || "Playbook salvo.");
      closePlaybookEditor();
      state.playbooksLoaded = false;
      await loadPlaybooks();
      await loadPlaybookOptions();
    } catch (error) {
      addImportLog("error", "Falha ao salvar playbook", error.message);
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Salvar playbook";
    }
  }

  function bytesToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunk = 0x8000;
    for (let index = 0; index < bytes.length; index += chunk) binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
    return btoa(binary);
  }

  async function importPlaybookFile(event) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    const button = $("#import-playbook");
    const originalLabel = button?.querySelector("span")?.textContent || "Importar";
    addImportLog("working", "Arquivo recebido", `${file.name} · ${Math.ceil(file.size / 1024)} KB`);
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("O documento deve ter no máximo 5 MB.");
      if (!/\.(ya?ml|txt|md|docx|pdf)$/i.test(file.name)) throw new Error("Use YAML, YML, TXT, MD, DOCX ou PDF.");
      if (button) { button.disabled = true; const label = button.querySelector("span"); if (label) label.textContent = "Analisando..."; }
      addImportLog("working", "Analisando documento", "Extraindo conteúdo e selecionando a IA configurada.");
      toast("A IA está lendo o documento e montando um playbook seguro...");
      const response = await api("/ui/api/playbooks/intelligent-import-preview", {
        method: "POST",
        body: { filename: file.name, content_base64: bytesToBase64(await file.arrayBuffer()), provider: $("#provider")?.value || null, model: $("#model")?.value || null },
      });
      const ai = response.draft?.ai_metadata;
      addImportLog("success", "Rascunho criado", ai?.provider ? `IA: ${ai.provider}${ai.model ? ` · ${ai.model}` : ""}. Aguardando revisão.` : "Documento pronto para revisão.");
      openPlaybookEditor(response.draft);
      toast(response.message || "Documento analisado. Revise antes de salvar.");
    } catch (error) {
      addImportLog("error", `Falha ao importar ${file.name}`, error.message || "Erro desconhecido");
      toast(error.message, "error");
    } finally {
      input.value = "";
      if (button) { button.disabled = false; const label = button.querySelector("span"); if (label) label.textContent = originalLabel; }
    }
  }

  function resultPlaybook(result) {
    if (result?.playbook?.id) return result.playbook;
    for (const plan of result?.plans || []) if (plan?.playbook?.id) return plan.playbook;
    return null;
  }

  function appendPlaybookSuggestion(result) {
    const investigationId = result?.investigation_id || result?.id || state.currentInvestigationId;
    if (!investigationId || resultPlaybook(result)) return;
    const content = $("#result-content");
    if (!content || content.querySelector("[data-create-playbook-draft]")) return;
    const section = document.createElement("section");
    section.className = "result-section playbook-suggestion";
    section.innerHTML = `<h3>Nenhum playbook correspondente</h3><p>A investigação foi concluída sem um playbook específico. Gere um rascunho, revise e só então salve.</p><button type="button" class="primary-button" data-create-playbook-draft="${escapeHtml(investigationId)}">Criar rascunho de playbook</button>`;
    content.insertBefore(section, content.querySelector(".raw-details") || null);
    section.querySelector("[data-create-playbook-draft]").addEventListener("click", async (draftEvent) => {
      const draftButton = draftEvent.currentTarget;
      draftButton.disabled = true;
      draftButton.textContent = "Gerando rascunho...";
      try { openPlaybookEditor((await api(`/ui/api/investigations/${encodeURIComponent(investigationId)}/playbook-draft`, { method: "POST" })).draft); }
      catch (error) { toast(error.message, "error"); }
      finally { draftButton.disabled = false; draftButton.textContent = "Criar rascunho de playbook"; }
    });
  }

  showResult = function showResultWithPlaybookSuggestion(result) { baseShowResult(result); appendPlaybookSuggestion(result); };

  function setupPlaybookManagement() {
    if (!$("#playbook-editor-modal")) document.body.insertAdjacentHTML("beforeend", playbookModalMarkup());
    const playbookView = $("#view-playbooks .panel");
    if (playbookView && !$("#playbook-import-log")) playbookView.insertAdjacentHTML("beforeend", importLogMarkup());
    const fileInput = $("#import-playbook-file");
    if (fileInput) fileInput.accept = ".yml,.yaml,.txt,.md,.docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";
    $("#add-playbook")?.addEventListener("click", () => openPlaybookEditor());
    $("#import-playbook")?.addEventListener("click", () => fileInput?.click());
    fileInput?.addEventListener("change", importPlaybookFile);
    $("#playbook-editor-form")?.addEventListener("submit", savePlaybook);
    $("#clear-playbook-import-log")?.addEventListener("click", () => { importHistory.length = 0; renderImportLog(); });
    $$('[data-close-playbook-modal]').forEach((button) => button.addEventListener("click", closePlaybookEditor));
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" && $("#playbook-editor-modal")?.classList.contains("open")) closePlaybookEditor(); });
  }

  document.addEventListener("DOMContentLoaded", () => { enhanceProviderStateField(); setupPlaybookManagement(); });
})();
