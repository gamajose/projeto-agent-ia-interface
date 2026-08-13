(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
  let items = [];
  let playbooks = [];
  let generatedSkills = [];

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

  async function openPlaybookImporter() {
    const ui = window.AgentCompactUI;
    if (!ui) return;
    const modal = ui.modal('skill-playbook-import-modal', 'Importar skills de playbook', 'A IA lê o playbook, separa os problemas específicos e completa reconhecimento, objetivo, conhecimento e restrições.');
    const body = $('.compact-modal-body', modal);
    generatedSkills = [];
    body.innerHTML = `<div class="skill-import-layout">
      <div class="skill-import-controls">
        <label><span>Playbook</span><select id="skill-import-playbook"><option value="">Carregando playbooks...</option></select></label>
        <button type="button" class="primary-button" id="skill-import-generate">Analisar com IA</button>
      </div>
      <div id="skill-import-preview"><div class="empty-state">Escolha um playbook. A IA vai transformar os cenários cobertos em skills específicas.</div></div>
      <div class="compact-toolbar skill-import-actions"><span class="compact-toolbar-spacer"></span><button type="button" class="secondary-button" data-close-import>Cancelar</button><button type="button" class="primary-button" id="skill-import-save" hidden>Importar selecionadas</button></div>
    </div>`;
    $('[data-close-import]', modal)?.addEventListener('click', () => ui.close(modal));

    try {
      const data = await api('/ui/api/playbooks/manage');
      playbooks = data.items || [];
      const select = $('#skill-import-playbook', modal);
      select.innerHTML = '<option value="">Selecione um playbook</option>' + playbooks.map((item) => `<option value="${esc(item.id)}">${esc(item.title || item.id)} · ${esc(item.id)}</option>`).join('');
    } catch (error) {
      $('#skill-import-preview', modal).innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
    }

    $('#skill-import-generate', modal)?.addEventListener('click', async () => {
      const playbookId = $('#skill-import-playbook', modal)?.value || '';
      if (!playbookId) { window.alert('Selecione um playbook.'); return; }
      const button = $('#skill-import-generate', modal);
      const preview = $('#skill-import-preview', modal);
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
        preview.innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
      } finally {
        button.disabled = false;
        button.textContent = 'Analisar com IA';
      }
    });

    $('#skill-import-save', modal)?.addEventListener('click', async () => {
      const selected = [...modal.querySelectorAll('[data-generated-skill]:checked')]
        .map((input) => generatedSkills[Number(input.dataset.generatedSkill)])
        .filter(Boolean);
      if (!selected.length) { window.alert('Selecione ao menos uma skill.'); return; }
      const button = $('#skill-import-save', modal);
      button.disabled = true;
      button.textContent = 'Importando...';
      try {
        for (const skill of selected) await api('/ui/api/noc/skills/catalog', { method: 'POST', body: skill });
        ui.close(modal);
        await load();
      } catch (error) {
        window.alert(error.message);
      } finally {
        button.disabled = false;
        button.textContent = 'Importar selecionadas';
      }
    });

    ui.open(modal);
  }

  async function load() {
    const ui = window.AgentCompactUI;
    const modal = ui.modal('skills-manager-modal', 'Skills dos agentes', 'Crie, edite, importe do playbook ou remova especialistas operacionais.');
    const body = $('.compact-modal-body', modal);
    const data = await api('/ui/api/noc/skills/catalog');
    items = data.items || [];
    body.innerHTML = `<div class="compact-toolbar"><strong>${items.length} skill(s)</strong><span class="compact-toolbar-spacer"></span><button type="button" class="secondary-button" id="skill-import-playbook-button">Importar do playbook</button><button type="button" class="primary-button" id="skill-new">Nova skill</button></div><div id="compact-skill-list">${items.map((skill) => `<article class="compact-skill-card"><header><div><code>${esc(skill.id)}</code><h4>${esc(skill.title || skill.id)}</h4><p>${esc(skill.objective || '')}</p></div></header><p>${skill.playbook_id ? `Playbook: ${esc(skill.playbook_id)}` : 'Sem playbook fixo'} · ${esc(skill.target_strategy || 'internal_ssh')}</p><footer><button type="button" class="secondary-button" data-edit="${esc(skill.id)}">Editar</button><button type="button" class="ghost-button" data-remove="${esc(skill.id)}">Remover</button></footer></article>`).join('')}</div>`;
    $('#skill-import-playbook-button', modal).addEventListener('click', () => void openPlaybookImporter());
    $('#skill-new', modal).addEventListener('click', () => editor());
    modal.querySelectorAll('[data-edit]').forEach((button) => button.addEventListener('click', () => editor(items.find((item) => item.id === button.dataset.edit) || {})));
    modal.querySelectorAll('[data-remove]').forEach((button) => button.addEventListener('click', () => { const skill = items.find((item) => item.id === button.dataset.remove); if (skill) void removeSkill(skill); }));
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