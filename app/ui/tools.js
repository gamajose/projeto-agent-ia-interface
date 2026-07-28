(() => {
  const baseShowView = showView;
  state.openCodeLoaded = false;
  viewMeta.opencode = ["AGENTE DE DESENVOLVIMENTO", "OpenCode"];

  showView = function showViewWithTools(name) {
    baseShowView(name);
    if (name === "opencode" && !state.openCodeLoaded) void loadOpenCodeStatus();
  };

  function statusLabel(data) {
    if (!data.enabled) return ["Desabilitado", "disabled"];
    if (!data.available) return ["Não instalado", "unavailable"];
    if (!data.configured) return ["Configuração incompleta", "attention"];
    if (!data.web_reachable) return ["Instalado · serviço parado", "attention"];
    return ["Disponível", "available"];
  }

  function renderOpenCode(data) {
    const [label, stateName] = statusLabel(data);
    const canOpen = data.web_reachable && data.web_url;
    const openButton = canOpen
      ? `<button type="button" class="primary-button" id="open-opencode-web">Abrir OpenCode</button>`
      : `<button type="button" class="primary-button" disabled>OpenCode indisponível</button>`;

    $("#opencode-workspace").innerHTML = `<div class="opencode-hero" data-state="${escapeHtml(stateName)}">
      <div class="opencode-brand"><span>⌘</span><div><p class="eyebrow">OPEN SOURCE CODING AGENT</p><h2>OpenCode conectado ao OmniRoute</h2><p>Ferramenta de desenvolvimento separada do motor de troubleshooting. Ela usa as rotas do gateway sem receber automaticamente SSH, credenciais de servidores ou permissões operacionais.</p></div></div>
      <span class="opencode-state">${escapeHtml(label)}</span>
    </div>
    <div class="opencode-grid">
      <article class="opencode-card"><span>Versão</span><strong>${escapeHtml(data.version || "não identificada")}</strong><small>${escapeHtml(data.command || "executável não localizado")}</small></article>
      <article class="opencode-card"><span>Provedor</span><strong>${escapeHtml(data.provider || "OmniRoute")}</strong><small>${escapeHtml(data.base_url || "endpoint não configurado")}</small></article>
      <article class="opencode-card"><span>Modelo padrão</span><strong>${escapeHtml(data.model || "sem rota")}</strong><small>Selecionado entre as rotas configuradas no OmniRoute.</small></article>
      <article class="opencode-card"><span>Projeto</span><strong>${escapeHtml(data.workdir || "diretório não configurado")}</strong><small>${escapeHtml(data.config_path || "configuração não encontrada")}</small></article>
    </div>
    <section class="opencode-access-panel">
      <div><p class="eyebrow">ACESSO SEGURO</p><h3>Interface web em localhost</h3><p>Abra o túnel SSH no Windows, mantenha o terminal conectado e depois acesse o OpenCode. O serviço usa autenticação HTTP própria.</p></div>
      <div class="opencode-command"><code id="opencode-tunnel-command">${escapeHtml(data.tunnel_command || "")}</code><button type="button" class="secondary-button" id="copy-opencode-tunnel">Copiar túnel</button></div>
      <div class="opencode-actions">${openButton}<button type="button" class="secondary-button" id="refresh-opencode-inline">Atualizar status</button></div>
    </section>
    <div class="opencode-safety"><span>i</span><p>O OpenCode pode ler, editar arquivos e executar comandos no diretório escolhido. A configuração gerada exige confirmação para edição e bash, bloqueia diretórios externos e não grava a chave do OmniRoute no JSON.</p></div>`;

    $("#open-opencode-web")?.addEventListener("click", () => window.open(data.web_url, "_blank", "noopener,noreferrer"));
    $("#copy-opencode-tunnel")?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(data.tunnel_command || "");
        toast("Comando do túnel copiado.");
      } catch {
        toast("Não foi possível copiar o comando automaticamente.", "error");
      }
    });
    $("#refresh-opencode-inline")?.addEventListener("click", () => loadOpenCodeStatus(true));
  }

  async function loadOpenCodeStatus(force = false) {
    if (state.openCodeLoaded && !force) return;
    $("#opencode-workspace").innerHTML = '<div class="empty-state">Consultando instalação, configuração e serviço web...</div>';
    try {
      const data = await api("/ui/api/tools/opencode");
      renderOpenCode(data);
      state.openCodeLoaded = true;
    } catch (error) {
      $("#opencode-workspace").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
      toast(error.message, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("#refresh-opencode")?.addEventListener("click", () => loadOpenCodeStatus(true));
  });
})();
