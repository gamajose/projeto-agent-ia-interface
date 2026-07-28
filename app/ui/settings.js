(() => {
  const baseShowView = showView;
  state.aiSettingsLoaded = false;
  state.aiSettingsData = null;
  viewMeta.settings = ["CONFIGURAÇÃO DINÂMICA", "Configurações de IA"];

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

  function renderProviderCards(data) {
    const container = $("#provider-config-grid");
    if (!data.providers.length) {
      container.innerHTML = '<div class="settings-empty">Nenhum provedor cadastrado.</div>';
      return;
    }
    container.innerHTML = data.providers.map((provider) => {
      const diagnostic = provider.diagnostic || {};
      const stateLabel = diagnostic.state_label || (provider.configured ? "configurado" : "não configurado");
      const detail = diagnostic.detail || (provider.configured ? "Credencial registrada no backend." : "Cadastre uma chave para habilitar.");
      return `<article class="provider-config-card" data-state="${escapeHtml(diagnosticState(provider))}">
        <div class="provider-card-head"><div><strong>${escapeHtml(provider.label)}</strong><p>${escapeHtml(provider.id)}</p></div><span class="provider-tier">${escapeHtml(tierLabel(provider.tier))}</span></div>
        <code>${escapeHtml(provider.default_model || "sem modelo padrão")}</code>
        <p><strong>${escapeHtml(stateLabel)}</strong> · ${escapeHtml(detail)}</p>
        <p>${escapeHtml(provider.base_url || "endpoint local/especial")}</p>
        <div class="provider-card-actions"><button class="secondary-button" type="button" data-edit-provider="${escapeHtml(provider.id)}">Configurar</button><button class="ghost-button" type="button" data-test-provider="${escapeHtml(provider.id)}">Testar</button>${provider.builtin ? "" : `<button class="ghost-button" type="button" data-delete-provider="${escapeHtml(provider.id)}">Excluir</button>`}</div>
      </article>`;
    }).join("");

    $$('[data-edit-provider]').forEach((button) => button.addEventListener("click", () => editProvider(button.dataset.editProvider)));
    $$('[data-test-provider]').forEach((button) => button.addEventListener("click", () => testProvider(button.dataset.testProvider, button)));
    $$('[data-delete-provider]').forEach((button) => button.addEventListener("click", () => deleteProvider(button.dataset.deleteProvider)));
  }

  function setEditor(provider = null) {
    const custom = !provider || !provider.builtin;
    $("#provider-editor-title").textContent = provider ? `Configurar ${provider.label}` : "Adicionar API OpenAI-compatible";
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
    $("#provider-priority").value = provider?.priority || 100;
    $("#provider-priority").disabled = Boolean(provider?.builtin);
    $("#provider-enabled").checked = provider?.enabled !== false;
    $("#provider-enabled").disabled = Boolean(provider?.builtin);
    $("#provider-delete").hidden = !provider || provider.builtin;
    $("#provider-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
    if (!custom && provider?.id === "ollama") $("#provider-api-key").disabled = true;
    else $("#provider-api-key").disabled = false;
  }

  function editProvider(id) {
    const provider = state.aiSettingsData?.providers.find((item) => item.id === id);
    if (provider) setEditor(provider);
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
      priority: Number($("#provider-priority").value || 100),
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
      await loadAISettings(true);
      if (typeof loadProviders === "function") await loadProviders();
      editProvider(payload.id);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Salvar provedor";
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
      setEditor();
      await loadAISettings(true);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function saveOrder() {
    const providers = $("#ai-provider-order").value.split(/[\n,]/).map((item) => item.trim().toLowerCase()).filter(Boolean);
    try {
      await api("/ui/api/settings/ai/order", { method: "PUT", body: { providers } });
      toast("Prioridade automática atualizada.");
      state.providersLoaded = false;
      await loadAISettings(true);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function applyDeepSeekPreset() {
    setEditor({ id: "deepseek", label: "DeepSeek", builtin: true, base_url: "https://api.deepseek.com", default_model: "deepseek-v4-flash", models: ["deepseek-v4-flash", "deepseek-v4-pro"], tier: "paid", priority: 25, enabled: true });
  }

  function renderSettings(data) {
    state.aiSettingsData = data;
    $("#ai-provider-order").value = data.automatic_order.join(",");
    $("#ai-settings-note").textContent = data.queue_note;
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
    $("#provider-new")?.addEventListener("click", () => setEditor());
    $("#provider-deepseek")?.addEventListener("click", applyDeepSeekPreset);
    $("#provider-save-order")?.addEventListener("click", saveOrder);
    $("#provider-delete")?.addEventListener("click", () => deleteProvider($("#provider-id").value));
    $("#refresh-ai-settings")?.addEventListener("click", () => loadAISettings(true));
  });
})();
