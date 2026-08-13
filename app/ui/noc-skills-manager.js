(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
  let items = [];

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

  async function load() {
    const ui = window.AgentCompactUI;
    const modal = ui.modal('skills-manager-modal', 'Skills dos agentes', 'Crie, edite ou remova especialistas operacionais.');
    const body = $('.compact-modal-body', modal);
    const data = await api('/ui/api/noc/skills/catalog');
    items = data.items || [];
    body.innerHTML = `<div class="compact-toolbar"><strong>${items.length} skill(s)</strong><span class="compact-toolbar-spacer"></span><button type="button" class="primary-button" id="skill-new">Nova skill</button></div><div id="compact-skill-list">${items.map((skill) => `<article class="compact-skill-card"><header><div><code>${esc(skill.id)}</code><h4>${esc(skill.title || skill.id)}</h4><p>${esc(skill.objective || '')}</p></div></header><p>${skill.playbook_id ? `Playbook: ${esc(skill.playbook_id)}` : 'Sem playbook fixo'} · ${esc(skill.target_strategy || 'internal_ssh')}</p><footer><button type="button" class="secondary-button" data-edit="${esc(skill.id)}">Editar</button><button type="button" class="ghost-button" data-remove="${esc(skill.id)}">Remover</button></footer></article>`).join('')}</div>`;
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
