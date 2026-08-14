(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
  const ICONS = {
    edit: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/></svg>',
    delete: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 14h10l1-14M9 7V4h6v3"/></svg>',
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  };
  let items = [];
  let playbooks = [];
  let generatedSkills = [];
  const importer = { query: '', page: 1, size: 8, selected: '' };

  async function api(path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const headers = { ...(options.headers || {}) };
    let body = options.body;
    if (method !== 'GET') headers['X-Agent-UI'] = '1';
    if (body && typeof body === 'object') { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
    const response = await fetch(path, { ...options, method, headers, body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  function split(value) { return String(value || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean); }
  function join(value) { return (value || []).join('\n'); }

  function editor(skill = {}) {
    const ui = window.AgentCompactUI;
    const modal = ui.modal('skill-editor-modal', skill.id ? 'Editar skill' : 'Nova skill', 'A configuração fica no diretório de dados e não altera o Git.');
    const body = $('.compact-modal-body', modal);
    const match = skill.match || {};
    body.innerHTML = `<form id="skill-editor-form" class="compact-skill-editor">
      <label><span>ID</span><input id="skill-id" required value="${esc(skill.id || '')}" placeholder="linux-systemd"></label>
      <label><span>Nome</span><input id="skill-title" required value="${esc(skill.title || '')}"></label>
      <label><span>Prioridade</span><input id="skill-priority" type="number" value="${esc(skill.priority ?? 0)}"></label>
      <label><span>Estratégia</span><select id="skill-strategy"><option value="internal_ssh">SSH interno</option><option value="entry_context">Contexto de entrada</option></select></label>
      <label class="full"><span>Playbook opcional</span><input id="skill-playbook" value="${esc(skill.playbook_id || '')}"></label>
      <label class="full"><span>Serviços · um padrão por linha</span><textarea id="skill-service" rows="3">${esc(join(match.service))}</textarea></label>
      <label class="full"><span>Outputs · um padrão por linha</span><textarea id="skill-output" rows="3">${esc(join(match.output))}</textarea></label>
      <label class="full"><span>Hosts · um padrão por linha</span><textarea id="skill-host" rows="3">${esc(join(match.host))}</textarea></label>
      <label class="full"><span>Objetivo</span><textarea id="skill-objective" rows="3">${esc(skill.objective || 'Investigar a causa do alerta usando somente evidências verificáveis.')}</textarea></label>
      <label class="full"><span>Conhecimento · um item por linha</span><textarea id="skill-knowledge" rows="3">${esc(join(skill.knowledge))}</textarea></label>
      <label class="full"><span>Restrições · um item por linha</span><textarea id="skill-constraints" rows="3">${esc(join(skill.constraints))}</textarea></label>
      <div class="full compact-toolbar"><span class="compact-toolbar-spacer"></span><button type="button" class="secondary-button" data-cancel>Cancelar</button><button type="submit" class="primary-button">Salvar</button></div>
    </form>`;
    $('#skill-strategy', modal).value = skill.target_strategy || 'internal_ssh';
    $('[data-cancel]', modal).addEventListener('click', () => ui.close(modal));
    $('#skill-editor-form', modal).addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {
        id: $('#skill-id', modal).value.trim().toLowerCase(),
        title: $('#skill-title', modal).value.trim(),
        priority: Number($('#skill-priority', modal).value || 0),
        target_strategy: $('#skill-strategy', modal).value,
        playbook_id: $('#skill-playbook', modal).value.trim() || null,
        match: { service: split($('#skill-service', modal).value), output: split($('#skill-output', modal).value), host: split($('#skill-host', modal).value) },
        objective: $('#skill-objective', modal).value.trim(),
        knowledge: split($('#skill-knowledge', modal).value),
        constraints: split($('#skill-constraints', modal).value),
      };
      try { await api('/ui/api/noc/skills/catalog', { method: 'POST', body: payload }); ui.close(modal); await load(); } catch (error) { window.alert(error.message); }
    });
    ui.open(modal);
  }

  async function removeSkill(skill) {
    if (!window.confirm(`Remover ${skill.title || skill.id}?`)) return;
    try { await api(`/ui/api/noc/skills/catalog/${encodeURIComponent(skill.id)}`, { method: 'DELETE' }); await load(); } catch (error) { window.alert(error.message); }
  }

  function generatedSkillCard(skill, index) {
    const match = skill.match || {};
    const clues = [...(match.service || []), ...(match.output || [])].slice(0, 4);
    return `<label class="compact-skill-card skill-import-card">
      <header><input type="checkbox" data-generated-skill="${index}" checked><div><code>${esc(skill.id)}</code><h4>${esc(skill.title || skill.id)}</h4><p>${esc(skill.objective || '')}</p></div></header>
      <p><strong>Playbook:</strong> ${esc(skill.playbook_id || '—')}</p>
      ${clues.length ? `<p><strong>Reconhece:</strong> ${clues.map(esc).join(' · ')}</p>` : '<p>Correspondência será guiada pelo objetivo e pelo playbook.</p>'}
    </label>`;
  }

  function renderGenerated(modal, metadata = {}, fallback = false) {
    const root = $('#skill-import-preview', modal);
    const save = $('#skill-import-save', modal);
    if (!root || !save) return;
    const ai = [metadata.provider, metadata.model].filter(Boolean).join(' · ');
    root.innerHTML = generatedSkills.length
      ? `<div class="skill-import-summary"><strong>${generatedSkills.length} skill(s) encontrada(s)</strong><span>${fallback ? 'Conversão estruturada de contingência' : (ai ? `IA: ${esc(ai)}` : 'IA configurada')}</span></div><div class="skill-import-grid">${generatedSkills.map(generatedSkillCard).join('')}</div>`
      : '<div class="empty-state">Nenhuma skill específica foi encontrada neste playbook.</div>';
    save.hidden = !generatedSkills.length;
  }

  function importMessage(modal, message = '', tone = '') {
    const root = $('#skill-import-message', modal);
    if (!root) return;
    root.textContent = message;
    root.dataset.tone = tone;
  }

  function filteredPlaybooks() {
    const query = importer.query.trim().toLocaleLowerCase('pt-BR');
    if (!query) return [...playbooks];
    return playbooks.filter((item) => JSON.stringify(item || {}).toLocaleLowerCase('pt-BR').includes(query));
  }

  function renderPlaybookCards(modal) {
    const grid = $('#skill-import-playbook-grid', modal);
    const pager = $('#skill-import-playbook-pager', modal);
    if (!grid || !pager) return;
    const filtered = filteredPlaybooks();
    const pages = Math.max(1, Math.ceil(filtered.length / importer.size));
    importer.page = Math.max(1, Math.min(importer.page, pages));
    const start = (importer.page - 1) * importer.size;
    const pageItems = filtered.slice(start, start + importer.size);
    grid.innerHTML = pageItems.length ? pageItems.map((item) => `<article class="skill-playbook-card ${importer.selected === item.id ? 'selected' : ''}" data-skill-playbook="${esc(item.id)}" tabindex="0" role="button" aria-pressed="${importer.selected === item.id ? 'true' : 'false'}"><h4>${esc(item.title || item.id)}</h4><p>${esc(item.summary || item.id)}</p><small>${esc(item.id)} · P${esc(item.priority ?? 0)}</small></article>`).join('') : '<div class="empty-state">Nenhum playbook encontrado.</div>';
    pager.innerHTML = `<span>${filtered.length} playbook(s) · página ${importer.page}/${pages}</span><button type="button" class="secondary-button" data-prev ${importer.page <= 1 ? 'disabled' : ''}>Anterior</button><button type="button" class="secondary-button" data-next ${importer.page >= pages ? 'disabled' : ''}>Próxima</button>`;
    pager.querySelector('[data-prev]')?.addEventListener('click', () => { importer.page -= 1; renderPlaybookCards(modal); });
    pager.querySelector('[data-next]')?.addEventListener('click', () => { importer.page += 1; renderPlaybookCards(modal); });
    grid.querySelectorAll('[data-skill-playbook]').forEach((card) => {
      const select = () => {
        importer.selected = card.dataset.skillPlaybook || '';
        importMessage(modal, '');
        renderPlaybookCards(modal);
      };
      card.addEventListener('click', select);
      card.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } });
    });
  }

  async function openPlaybookImporter() {
    const ui = window.AgentCompactUI;
    if (!ui) return;
    const modal = ui.modal('skill-playbook-import-modal', 'Importar skills de playbook', 'A IA lê o playbook, separa os problemas específicos e completa reconhecimento, objetivo, conhecimento e restrições.');
    const body = $('.compact-modal-body', modal);
    generatedSkills = [];
    importer.query = '';
    importer.page = 1;
    importer.selected = '';
    body.innerHTML = `<div class="skill-import-layout">
      <div class="skill-import-controls">
        <label><span>Pesquisar playbook</span><input id="skill-import-playbook-search" type="search" placeholder="Nome, serviço, container, socket ou conteúdo"></label>
        <button type="button" class="primary-button" id="skill-import-generate">Analisar com IA</button>
      </div>
      <div class="skill-import-message" id="skill-import-message"></div>
      <div class="skill-playbook-grid" id="skill-import-playbook-grid"><div class="empty-state">Carregando playbooks...</div></div>
      <div class="compact-pager skill-playbook-pager" id="skill-import-playbook-pager"></div>
      <div id="skill-import-preview"><div class="empty-state">Selecione um playbook nos cards acima e peça para a IA extrair as skills.</div></div>
      <div class="compact-toolbar skill-import-actions"><span class="compact-toolbar-spacer"></span><button type="button" class="secondary-button" data-close-import>Cancelar</button><button type="button" class="primary-button" id="skill-import-save" hidden>Importar selecionadas</button></div>
    </div>`;
    $('[data-close-import]', modal)?.addEventListener('click', () => ui.close(modal));
    $('#skill-import-playbook-search', modal)?.addEventListener('input', (event) => {
      importer.query = event.target.value || '';
      importer.page = 1;
      renderPlaybookCards(modal);
    });

    try {
      const data = await api('/ui/api/playbooks/manage');
      playbooks = data.items || [];
      renderPlaybookCards(modal);
    } catch (error) {
      $('#skill-import-playbook-grid', modal).innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
    }

    $('#skill-import-generate', modal)?.addEventListener('click', async () => {
      const playbookId = importer.selected;
      if (!playbookId) {
        importMessage(modal, 'Selecione um playbook para continuar.', 'error');
        return;
      }
      const button = $('#skill-import-generate', modal);
      const preview = $('#skill-import-preview', modal);
      importMessage(modal, '');
      button.disabled = true;
      button.textContent = 'Analisando...';
      preview.innerHTML = '<div class="empty-state">A IA está lendo o playbook e separando as skills...</div>';
      try {
        const response = await api('/ui/api/noc/skills/from-playbook-preview', {
          method: 'POST',
          body: {
            playbook_id: playbookId,
            provider: document.querySelector('#provider')?.value || null,
            model: document.querySelector('#model')?.value || null,
          },
        });
        generatedSkills = response.items || [];
        renderGenerated(modal, response.ai_metadata || {}, Boolean(response.fallback));
      } catch (error) {
        generatedSkills = [];
        importMessage(modal, error.message, 'error');
        preview.innerHTML = '<div class="empty-state">Não foi possível gerar a prévia das skills.</div>';
      } finally {
        button.disabled = false;
        button.textContent = 'Analisar com IA';
      }
    });

    $('#skill-import-save', modal)?.addEventListener('click', async () => {
      const selected = [...modal.querySelectorAll('[data-generated-skill]:checked')]
        .map((input) => generatedSkills[Number(input.dataset.generatedSkill)])
        .filter(Boolean);
      if (!selected.length) {
        importMessage(modal, 'Selecione ao menos uma skill para importar.', 'error');
        return;
      }
      const button = $('#skill-import-save', modal);
      button.disabled = true;
      button.textContent = 'Importando...';
      try {
        for (const skill of selected) await api('/ui/api/noc/skills/catalog', { method: 'POST', body: skill });
        ui.close(modal);
        await load();
      } catch (error) {
        importMessage(modal, error.message, 'error');
      } finally {
        button.disabled = false;
        button.textContent = 'Importar selecionadas';
      }
    });

    ui.open(modal);
  }

  function skillCard(skill) {
    return `<article class="compact-skill-card" data-skill-card="${esc(skill.id)}" tabindex="0" role="button" aria-label="Editar ${esc(skill.title || skill.id)}"><header><div><code>${esc(skill.id)}</code><h4>${esc(skill.title || skill.id)}</h4><p>${esc(skill.objective || '')}</p></div></header><p>${skill.playbook_id ? `Playbook: ${esc(skill.playbook_id)}` : 'Sem playbook fixo'} · ${esc(skill.target_strategy || 'internal_ssh')}</p><footer><button type="button" class="secondary-button icon-action-button" data-edit="${esc(skill.id)}" aria-label="Editar" title="Editar">${ICONS.edit}</button><button type="button" class="ghost-button icon-action-button" data-remove="${esc(skill.id)}" aria-label="Remover" title="Remover">${ICONS.delete}</button></footer></article>`;
  }

  async function load() {
    const ui = window.AgentCompactUI;
    const modal = ui.modal('skills-manager-modal', 'Skills dos agentes', 'Crie, edite, importe do playbook ou remova especialistas operacionais.');
    const body = $('.compact-modal-body', modal);
    const data = await api('/ui/api/noc/skills/catalog');
    items = data.items || [];
    body.innerHTML = `<div class="compact-toolbar"><strong>${items.length} skill(s)</strong><span class="compact-toolbar-spacer"></span><button type="button" class="secondary-button" id="skill-import-playbook-button" data-keep-text-action>Importar do playbook</button><button type="button" class="primary-button icon-plus-button" id="skill-new" aria-label="Nova skill" title="Nova skill">${ICONS.plus}</button></div><div id="compact-skill-list">${items.map(skillCard).join('')}</div>`;
    $('#skill-import-playbook-button', modal).addEventListener('click', () => void openPlaybookImporter());
    $('#skill-new', modal).addEventListener('click', () => editor());
    modal.querySelectorAll('[data-edit]').forEach((button) => button.addEventListener('click', (event) => { event.stopPropagation(); editor(items.find((item) => item.id === button.dataset.edit) || {}); }));
    modal.querySelectorAll('[data-remove]').forEach((button) => button.addEventListener('click', (event) => { event.stopPropagation(); const skill = items.find((item) => item.id === button.dataset.remove); if (skill) void removeSkill(skill); }));
    modal.querySelectorAll('[data-skill-card]').forEach((card) => {
      const open = () => editor(items.find((item) => item.id === card.dataset.skillCard) || {});
      card.addEventListener('click', (event) => { if (!event.target.closest('button')) open(); });
      card.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    });
    return modal;
  }

  async function openManager() {
    const ui = window.AgentCompactUI;
    if (!ui) return;
    try { const modal = await load(); ui.open(modal); } catch (error) { window.alert(error.message); }
  }

  function setup() {
    const actions = document.querySelector('#noc-fleet-panel .cmk-master-actions');
    if (!actions || document.getElementById('skills-manager-button')) return;
    const button = document.createElement('button');
    button.id = 'skills-manager-button';
    button.type = 'button';
    button.className = 'secondary-button';
    button.textContent = 'Skills';
    button.addEventListener('click', () => void openManager());
    actions.appendChild(button);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup); else setup();
  setInterval(setup, 1200);
})();
