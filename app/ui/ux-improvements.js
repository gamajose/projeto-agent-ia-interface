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
          <div class="playbook-import-warning" id="playbook-import-warning" hidden></div>
          <div class="playbook-editor-note">A IA transforma YAML, Word, PDF ou texto em ferramentas estruturadas e somente de leitura. Revise os campos antes de salvar; nenhuma ação corretiva é executada durante a importação.</div>
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

  function renderImportWarnings(draft = {}) {
    const element = $("#playbook-import-warning");
    if (!element) return;
    const warnings = Array.isArray(draft.import_warnings) ? draft.import_warnings.filter(Boolean) : [];
    const safety = Array.isArray(draft.safety_rules) ? draft.safety_rules.filter(Boolean) : [];
    const validations = Array.isArray(draft.validation_notes) ? draft.validation_notes.filter(Boolean) : [];
    const summary = String(draft.extracted_summary || "").trim();
    const items = [
      ...warnings.map((item) => `Ajuste: ${item}`),
      ...safety.map((item) => `Segurança: ${item}`),
      ...validations.map((item) => `Validação: ${item}`),
    ];
    element.hidden = !(summary || items.length);
    element.innerHTML = element.hidden ? "" : `${summary ? `<p><strong>Resumo extraído:</strong> ${escapeHtml(summary)}</p>` : ""}${items.length ? `<strong>Revisão da importação</strong><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}`;
  }

  function openPlaybookEditor(draft = null) {
    const modal = $("#playbook-editor-modal");
    if (!modal) return;
    const imported = Boolean(draft?.source_filename);
    $("#playbook-editor-title").textContent = imported
      ? "Revisar playbook importado"
      : (draft ? "Revisar rascunho de playbook" : "Adicionar playbook");
    $("#playbook-editor-id").value = draft?.id || "";
    $("#playbook-editor-name").value = draft?.title || "";
    $("#playbook-editor-priority").value = draft?.priority ?? 20;
    $("#playbook-editor-profiles").value = (draft?.profiles || ["linux_generic"]).join(", ");
    $("#playbook-editor-patterns").value = (draft?.patterns || []).join("\n");
    $("#playbook-editor-steps").value = draft?.steps_yaml || defaultSteps();
    renderImportWarnings(draft || {});
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

  function bytesToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunk = 0x8000;
    for (let index = 0; index < bytes.length; index += chunk) {
      binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
    }
    return btoa(binary);
  }

  async function importPlaybookFile(event) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    const button = $("#import-playbook");
    const originalLabel = button?.querySelector("span")?.textContent || "Importar";
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error("O documento deve ter no máximo 5 MB.");
      if (!/\.(ya?ml|txt|md|docx|pdf)$/i.test(file.name)) throw new Error("Use YAML, YML, TXT, MD, DOCX ou PDF.");
      if (button) {
        button.disabled = true;
        const label = button.querySelector("span");
        if (label) label.textContent = "Analisando...";
      }
      toast("A IA está lendo o documento e montando um playbook seguro...");
      const contentBase64 = bytesToBase64(await file.arrayBuffer());
      const response = await api("/ui/api/playbooks/intelligent-import-preview", {
        method: "POST",
        body: {
          filename: file.name,
          content_base64: contentBase64,
          provider: $("#provider")?.value || null,
          model: $("#model")?.value || null,
        },
      });
      openPlaybookEditor(response.draft);
      toast(response.message || "Documento analisado. Revise antes de salvar.");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      input.value = "";
      if (button) {
        button.disabled = false;
        const label = button.querySelector("span");
        if (label) label.textContent = originalLabel;
      }
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
    section.querySelector("[data-create-playbook-draft]").addEventListener("click", async (draftEvent) => {
      const draftButton = draftEvent.currentTarget;
      draftButton.disabled = true;
      draftButton.textContent = "Gerando rascunho...";
      try {
        const response = await api(`/ui/api/investigations/${encodeURIComponent(investigationId)}/playbook-draft`, { method: "POST" });
        openPlaybookEditor(response.draft);
      } catch (error) {
        toast(error.message, "error");
      } finally {
        draftButton.disabled = false;
        draftButton.textContent = "Criar rascunho de playbook";
      }
    });
  }

  showResult = function showResultWithPlaybookSuggestion(result) {
    baseShowResult(result);
    appendPlaybookSuggestion(result);
  };

  function setupPlaybookManagement() {
    if (!$("#playbook-editor-modal")) document.body.insertAdjacentHTML("beforeend", playbookModalMarkup());

    const fileInput = $("#import-playbook-file");
    if (fileInput) fileInput.accept = ".yml,.yaml,.txt,.md,.docx,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain";
    const addButton = $("#add-playbook");
    addButton?.addEventListener("click", () => openPlaybookEditor());
    $("#import-playbook")?.addEventListener("click", () => fileInput?.click());
    fileInput?.addEventListener("change", importPlaybookFile);
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
