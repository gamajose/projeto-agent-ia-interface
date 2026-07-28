(() => {
  const baseShowView = showView;
  state.aiSettingsLoaded = false;
  state.aiSettingsData = null;
  viewMeta.settings = ["CONFIGURAÇÃO DINÂMICA", "Configurações de IA"];

  let draggedProviderId = null;
  let editingProviderPriority = 100;

  showView = function showViewWithAISettings(name) {
    baseShowView(name);
    if (name === "settings" && !state.aiSettingsLoaded) void loadAISettings();
  };

  function tierLabel(value) {
    return ({ free: "gratuito", paid: "pago/créditos", local: "local", gateway: "gateway", custom: "personalizado" })[value] || value || "personalizado";
  }

  function diagnosticState(provider) {
    return provider.diagnostic?.state || (provider.configured ? "available" : "not_configured");
  }

  function orderedProviders(data) {
    const order = new Map((data.automatic_order || []).map((id, index) => [id, index]));
    return [...data.providers].sort((left, right) => {
      const leftOrder = order.has(left.id) ? order.get(left.id) : Number.MAX_SAFE_INTEGER;
      const rightOrder = order.has(right.id) ? order.get(right.id) : Number.MAX_SAFE_INTEGER;
      if (leftOrder !== rightOrder) return leftOrder - rightOrder;
      return (left.priority || 999) - (right.priority || 999) || left.label.localeCompare(right.label, "pt-BR");
    });
  }

  function updateOrderNumbers() {
    $$("#provider-config-grid .provider-config-card").forEach((card, index) => {
      const badge = card.querySelector(".provider-order-index");
      if (badge) badge.textContent = String(index + 1).padStart(2, "0");
    });
  }

  async function persistCardOrder() {
    const providers = $$("#provider-config-grid .provider-config-card").map((card) => card.dataset.providerId).filter(Boolean);
    const status = $("#provider-order-status");
    status.dataset.state = "saving";
    status.textContent = "Salvando nova prioridade...";
    try {
      await api("/ui/api/settings/ai/order", { method: "PUT", body: { providers } });
      if (state.aiSettingsData) state.aiSettingsData.automatic_order = [...providers];
      state.providersLoaded = false;
      status.dataset.state = "saved";
      status.textContent = "Ordem salva automaticamente.";
      toast("Prioridade automática atualizada.");
      if (typeof loadProviders === "function") await loadProviders();
    } catch (error) {
      status.dataset.state = "error";
      status.textContent = "Não foi possível salvar a ordem.";
      toast(error.message, "error");
      await loadAISettings(true);
    }
  }

  function bindCardSorting(container) {
    $$("#provider-config-grid .provider-config-card").forEach((card) => {
      card.addEventListener("dragstart", (event) => {
        draggedProviderId = card.dataset.providerId;
        card.classList.add("is-dragging");
        card.setAttribute("aria-grabbed", "true");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", draggedProviderId || "");
      });
      card.addEventListener("dragend", () => {
        card.classList.remove("is-dragging");
        card.setAttribute("aria-grabbed", "false");
        draggedProviderId = null;
        $$("#provider-config-grid .provider-config-card").forEach((item) => item.classList.remove("drag-over"));
      });
      card.addEventListener("dragover", (event) => {
        event.preventDefault();
        const dragging = container.querySelector(".is-dragging");
        if (!dragging || dragging === card) return;
        const bounds = card.getBoundingClientRect();
        const insertBefore = event.clientX < bounds.left + bounds.width / 2;
        container.insertBefore(dragging, insertBefore ? card : card.nextSibling);
        updateOrderNumbers();
      });
      card.addEventListener("drop", async (event) => {
        event.preventDefault();
        if (!draggedProviderId) return;
        await persistCardOrder();
      });
    });
  }

  function renderProviderCards(data) {
    const container = $("#provider-config-grid");
    const providers = orderedProviders(data);
    if (!providers.length) {
      container.innerHTML = '<div class="settings-empty">Nenhum provedor cadastrado.</div>';
      return;
    }
    container.innerHTML = providers.map((provider, index) => {
      const diagnostic = provider.diagnostic || {};
      const stateLabel = diagnostic.state_label || (provider.configured ? "configurado" : "não configurado");
      const detail = diagnostic.detail || (provider.configured ? "Credencial registrada no backend." : "Cadastre uma chave para habilitar.");
      return `<article class="provider-config-card" draggable="true" aria-grabbed="false" data-provider-id="${escapeHtml(provider.id)}" data-state="${escapeHtml(diagnosticState(provider))}">
        <div class="provider-drag-row"><span class="provider-order-index">${String(index + 1).padStart(2, "0")}</span><span class="provider-drag-handle" title="Arraste para alterar a prioridade" aria-hidden="true">⋮⋮</span></div>
        <div class="provider-card-head"><div><strong>${escapeHtml(provider.label)}</strong><p>${escapeHtml(provider.id)}</p></div><span class="provider-tier">${escapeHtml(tierLabel(provider.tier))}</span></div>
        <code>${escapeHtml(provider.default_model || "sem modelo padrão")}</code>
        <p><strong>${escapeHtml(stateLabel)}</strong> · ${escapeHtml(detail)}</p>
        <p>${escapeHtml(provider.base_url || "endpoint local/especial")}</p>
        <div class="provider-card-actions"><button class="secondary-button" type="button" data-edit-provider="${escapeHtml(provider.id)}">Configurar</button><button class="ghost-button" type="button" data-test-provider="${escapeHtml(provider.id)}">Testar</button>${provider.builtin ? "" : `<button class="ghost-button" type="button" data-delete-provider="${escapeHtml(provider.id)}">Excluir</button>`}</div>
      </article>`;
    }).join("");

    bindCardSorting(container);
    $$('[data-edit-provider]').forEach((button) => button.addEventListener("click", () => editProvider(button.dataset.editProvider)));
    $$('[data-test-provider]').forEach((button) => button.addEventListener("click", () => testProvider(button.dataset.testProvider, button)));
    $$('[data-delete-provider]').forEach((button) => button.addEventListener("click", () => deleteProvider(button.dataset.deleteProvider)));
  }

  function openProviderModal(provider = null) {
    const custom = !provider || !provider.builtin;
    editingProviderPriority = provider?.priority || 100;
    $("#provider-editor-title").textContent = provider ? `Configurar ${provider.label}` : "Adicionar IA";
    $("#provider-id").value = provider?.id || "";
    $("#provider-id").readOnly = Boolean(provider);
    $("#provider-label").value = provider?.label || "";
    $("#provider-label").disabled = Boolean(provider?.builtin);
    $("#provider-base-url").value = provider?.base_url || "";
    $("#provider-default-model").value = provider?.default_model || "";
    $("#provider-models").value = (provider?.models || []).join("\n");
    $("#provider-api-key").value = "";
    $("#provider-tier").value = provider?.tier || "custom";
    $("#provider-tier").disabled = Boolean(provider?.builtin);
    $("#provider-enabled").checked = provider?.enabled !== false;
    $("#provider-enabled").disabled = Boolean(provider?.builtin);
    $("#provider-delete").hidden = !provider || provider.builtin;
    $("#provider-api-key").disabled = !custom && provider?.id === "ollama";

    const modal = $("#provider-modal");
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("settings-modal-open");
    window.setTimeout(() => $("#provider-id").focus(), 50);
  }

  function closeProviderModal() {
    const modal = $("#provider-modal");
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("settings-modal-open");
  }

  function editProvider(id) {
    const provider = state.aiSettingsData?.providers.find((item) => item.id === id);
    if (provider) openProviderModal(provider);
  }

  function editorPayload() {
    const id = $("#provider-id").value.trim().toLowerCase();
    return {
      id,
      label: $("#provider-label").value.trim(),
      api_key: $("#provider-api-key").value.trim() || null,
      base_url: $("#provider-base-url").value.trim() || null,
      default_model: $("#provider-default-model").value.trim() || null,
      models: $("#provider-models").value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean),
      enabled: $("#provider-enabled").checked,
      tier: $("#provider-tier").value,
      priority: editingProviderPriority,
    };
  }

  async function saveProvider(event) {
    event.preventDefault();
    const payload = editorPayload();
    if (!payload.id) return toast("Informe o identificador do provedor.", "error");
    const button = $("#provider-save");
    button.disabled = true;
    button.textContent = "Salvando...";
    try {
      const response = await api(`/ui/api/settings/ai/providers/${encodeURIComponent(payload.id)}`, { method: "PUT", body: payload });
      toast(response.message || "Provedor salvo.");
      state.providersLoaded = false;
      closeProviderModal();
      await loadAISettings(true);
      if (typeof loadProviders === "function") await loadProviders();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Salvar IA";
    }
  }

  async function testProvider(id, button) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Testando...";
    try {
      const result = await api(`/ui/api/settings/ai/providers/${encodeURIComponent(id)}/test`, { method: "POST" });
      toast(`${result.label}: ${result.state_label} — ${result.detail}`, result.selectable ? "success" : "error");
      await loadAISettings(true);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function deleteProvider(id) {
    if (!window.confirm(`Excluir o provedor personalizado ${id}? A chave continuará no .env até ser removida manualmente.`)) return;
    try {
      await api(`/ui/api/settings/ai/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast("Provedor removido do catálogo.");
      closeProviderModal();
      await loadAISettings(true);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderSettings(data) {
    state.aiSettingsData = data;
    $("#provider-order-status").textContent = data.queue_note || "Arraste os cards para definir a prioridade automática.";
    renderProviderCards(data);
  }

  async function loadAISettings(force = false) {
    if (state.aiSettingsLoaded && !force) return;
    $("#provider-config-grid").innerHTML = '<div class="settings-empty">Carregando provedores e diagnósticos...</div>';
    try {
      const data = await api("/ui/api/settings/ai");
      renderSettings(data);
      state.aiSettingsLoaded = true;
    } catch (error) {
      $("#provider-config-grid").innerHTML = `<div class="settings-empty">${escapeHtml(error.message)}</div>`;
      toast(error.message, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("#provider-editor-form")?.addEventListener("submit", saveProvider);
    $("#provider-new")?.addEventListener("click", () => openProviderModal());
    $("#provider-delete")?.addEventListener("click", () => deleteProvider($("#provider-id").value));
    $$('[data-close-provider-modal]').forEach((button) => button.addEventListener("click", closeProviderModal));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && $("#provider-modal")?.classList.contains("open")) closeProviderModal();
    });
  });
})();
