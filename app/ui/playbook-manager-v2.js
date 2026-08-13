(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const state = { items: [], query: '', page: 1, size: 12, loading: false, rendering: false };

  async function request(path, options = {}) {
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

  const join = (value) => Array.isArray(value) ? value.join('\n') : String(value || '');
  const split = (value) => String(value || '').split(/[\n,]/).map((item) => item.trim()).filter(Boolean);

  function searchText(item) {
    return JSON.stringify(item || {}).toLocaleLowerCase('pt-BR');
  }

  function filteredItems() {
    const query = state.query.trim().toLocaleLowerCase('pt-BR');
    return query ? state.items.filter((item) => searchText(item).includes(query)) : [...state.items];
  }

  function ensureToolbar() {
    const grid = $('#playbook-grid');
    if (!grid || $('#playbook-manager-toolbar')) return;
    const toolbar = document.createElement('div');
    toolbar.id = 'playbook-manager-toolbar';
    toolbar.className = 'playbook-manager-toolbar';
    toolbar.innerHTML = '<input id="playbook-manager-search" type="search" placeholder="Pesquisar por nome, container, socket, ferramenta ou conteúdo"><button type="button" class="secondary-button" id="playbook-manager-filter">Filtrar</button><select id="playbook-manager-size"><option value="12">12/página</option><option value="24">24/página</option><option value="48">48/página</option></select>';
    grid.insertAdjacentElement('beforebegin', toolbar);
    $('#playbook-manager-filter')?.addEventListener('click', () => {
      state.query = $('#playbook-manager-search')?.value || '';
      state.page = 1;
      render();
    });
    $('#playbook-manager-search')?.addEventListener('input', (event) => {
      state.query = event.target.value || '';
      state.page = 1;
      render();
    });
    $('#playbook-manager-size')?.addEventListener('change', (event) => {
      state.size = Number(event.target.value || 12);
      state.page = 1;
      render();
    });
    const pager = document.createElement('div');
    pager.id = 'playbook-manager-pager';
    pager.className = 'compact-pager';
    grid.insertAdjacentElement('afterend', pager);
  }

  function card(item) {
    const corrections = item.allowed_corrections || [];
    return `<article class="playbook-card" data-playbook-id="${esc(item.id)}" tabindex="0" role="button" aria-label="Editar ${esc(item.title || item.id)}">
      <div class="card-top"><div><h4>${esc(item.title || item.id)}</h4><p>${esc(item.id)}</p></div><span class="mode-badge">P${esc(item.priority ?? 0)}</span></div>
      <div class="card-meta"><div><span>Perfis</span><strong>${esc((item.profiles || []).join(', '))}</strong></div><div><span>Etapas</span><strong>${esc(String((item.steps_yaml || '').split('\n- ').length - 1 || '—'))}</strong></div><div><span>SSH</span><strong>${esc(item.ssh_port || 'Automática')}</strong></div><div><span>Arquivo</span><strong>${esc(item.file || `${item.id}.yml`)}</strong></div></div>
      <div class="correction-tags">${corrections.length ? corrections.map((value) => `<span>${esc(value)}</span>`).join('') : '<span>somente leitura / preservado</span>'}</div>
      <div class="playbook-card-actions"><button type="button" class="secondary-button" data-edit-playbook="${esc(item.id)}">Editar</button><button type="button" class="ghost-button" data-delete-playbook="${esc(item.id)}">Remover</button></div>
    </article>`;
  }

  function render() {
    if (state.rendering) return;
    const grid = $('#playbook-grid');
    const pager = $('#playbook-manager-pager');
    if (!grid || !pager) return;
    state.rendering = true;
    try {
      const items = filteredItems();
      const pages = Math.max(1, Math.ceil(items.length / state.size));
      state.page = Math.max(1, Math.min(state.page, pages));
      const start = (state.page - 1) * state.size;
      const pageItems = items.slice(start, start + state.size);
      grid.innerHTML = pageItems.length ? pageItems.map(card).join('') : '<div class="empty-state">Nenhum playbook encontrado com este filtro.</div>';
      grid.dataset.managedPlaybooks = '1';
      pager.innerHTML = `<span>${items.length} playbook(s) · página ${state.page}/${pages}</span><button type="button" class="secondary-button" data-prev ${state.page <= 1 ? 'disabled' : ''}>Anterior</button><button type="button" class="secondary-button" data-next ${state.page >= pages ? 'disabled' : ''}>Próxima</button>`;
      pager.querySelector('[data-prev]')?.addEventListener('click', () => { state.page -= 1; render(); });
      pager.querySelector('[data-next]')?.addEventListener('click', () => { state.page += 1; render(); });
      grid.querySelectorAll('[data-edit-playbook]').forEach((button) => button.addEventListener('click', (event) => { event.stopPropagation(); void openEditor(button.dataset.editPlaybook); }));
      grid.querySelectorAll('[data-delete-playbook]').forEach((button) => button.addEventListener('click', (event) => { event.stopPropagation(); void removePlaybook(button.dataset.deletePlaybook); }));
      grid.querySelectorAll('[data-playbook-id]').forEach((node) => {
        const open = () => void openEditor(node.dataset.playbookId);
        node.addEventListener('click', (event) => { if (!event.target.closest('button')) open(); });
        node.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
      });
    } finally { state.rendering = false; }
  }

  async function loadCatalog() {
    if (state.loading) return;
    state.loading = true;
    ensureToolbar();
    try {
      const data = await request('/ui/api/playbooks/manage');
      state.items = data.items || [];
      render();
    } catch (error) {
      const grid = $('#playbook-grid');
      if (grid) grid.innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
    } finally { state.loading = false; }
  }

  function editorPayload(modal) {
    return {
      id: $('#playbook-editor-id', modal).value.trim().toLowerCase(),
      title: $('#playbook-editor-name', modal).value.trim(),
      priority: Number($('#playbook-editor-priority', modal).value || 20),
      profiles: split($('#playbook-editor-profiles', modal).value),
      patterns: split($('#playbook-editor-patterns', modal).value),
      summary: $('#playbook-editor-summary', modal).value.trim(),
      required_inputs: split($('#playbook-editor-inputs', modal).value),
      safety_rules: split($('#playbook-editor-safety', modal).value),
      validation_notes: split($('#playbook-editor-validations', modal).value),
      import_notes: split($('#playbook-editor-notes', modal).value),
      source_filename: modal.dataset.sourceFilename || '',
      steps_yaml: $('#playbook-editor-steps', modal).value,
    };
  }

  async function openEditor(id) {
    const modal = $('#playbook-editor-modal');
    if (!modal) return;
    try {
      const item = state.items.find((row) => row.id === id) || await request(`/ui/api/playbooks/${encodeURIComponent(id)}`);
      modal.dataset.editingId = id;
      modal.dataset.sourceFilename = item.source_filename || '';
      $('#playbook-editor-title', modal).textContent = 'Editar playbook';
      const idField = $('#playbook-editor-id', modal);
      idField.value = item.id || id;
      idField.disabled = true;
      $('#playbook-editor-name', modal).value = item.title || '';
      $('#playbook-editor-priority', modal).value = item.priority ?? 20;
      $('#playbook-editor-profiles', modal).value = (item.profiles || []).join(', ');
      $('#playbook-editor-patterns', modal).value = join(item.patterns);
      $('#playbook-editor-summary', modal).value = item.summary || '';
      $('#playbook-editor-inputs', modal).value = join(item.required_inputs);
      $('#playbook-editor-safety', modal).value = join(item.safety_rules);
      $('#playbook-editor-validations', modal).value = join(item.validation_notes);
      $('#playbook-editor-notes', modal).value = join(item.import_notes);
      $('#playbook-editor-steps', modal).value = item.steps_yaml || '';
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      document.body.classList.add('settings-modal-open');
    } catch (error) { window.alert(error.message); }
  }

  function resetEditorMode() {
    const modal = $('#playbook-editor-modal');
    if (!modal) return;
    delete modal.dataset.editingId;
    delete modal.dataset.sourceFilename;
    const field = $('#playbook-editor-id', modal);
    if (field) field.disabled = false;
  }

  async function saveEdit(event) {
    const form = event.target;
    if (form?.id !== 'playbook-editor-form') return;
    const modal = $('#playbook-editor-modal');
    const id = modal?.dataset.editingId;
    if (!modal || !id) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const button = $('#playbook-editor-save', modal);
    if (button) { button.disabled = true; button.textContent = 'Salvando...'; }
    try {
      const response = await request(`/ui/api/playbooks/${encodeURIComponent(id)}`, { method: 'PUT', body: editorPayload(modal) });
      if (typeof toast === 'function') toast(response.message || 'Playbook atualizado.');
      modal.querySelector('[data-close-playbook-modal]')?.click();
      resetEditorMode();
      if (typeof loadPlaybookOptions === 'function') await loadPlaybookOptions();
      await loadCatalog();
    } catch (error) {
      if (typeof toast === 'function') toast(error.message, 'error'); else window.alert(error.message);
    } finally {
      if (button) { button.disabled = false; button.textContent = 'Salvar playbook'; }
    }
  }

  async function removePlaybook(id) {
    const item = state.items.find((row) => row.id === id);
    if (!window.confirm(`Remover o playbook ${item?.title || id}?`)) return;
    try {
      await request(`/ui/api/playbooks/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (typeof toast === 'function') toast('Playbook removido.');
      if (typeof loadPlaybookOptions === 'function') await loadPlaybookOptions();
      await loadCatalog();
    } catch (error) { if (typeof toast === 'function') toast(error.message, 'error'); else window.alert(error.message); }
  }

  function setup() {
    ensureToolbar();
    $('#add-playbook')?.addEventListener('click', resetEditorMode, true);
    $('#import-playbook-file')?.addEventListener('change', resetEditorMode, true);
    document.querySelectorAll('[data-close-playbook-modal]').forEach((button) => button.addEventListener('click', resetEditorMode));
  }

  document.addEventListener('submit', saveEdit, true);
  window.addEventListener('agent:playbooks-open', () => {
    void loadCatalog();
    window.setTimeout(() => void loadCatalog(), 350);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup); else setup();
  window.setInterval(() => {
    const modal = $('#compact-playbooks-modal.open');
    const grid = $('#playbook-grid');
    if (modal && grid && !grid.querySelector('[data-playbook-id]') && !grid.querySelector('.empty-state')) void loadCatalog();
  }, 1400);
})();
