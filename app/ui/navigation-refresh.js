(() => {
  const iconPaths = {
    dashboard: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10.5V20h13v-9.5"/><path d="M9 20v-6h6v6"/>',
    noc: '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/><path d="M8 12h.01M16 12h.01M8 16h8"/>',
    investigations: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 5 5"/><path d="M8 11h6M11 8v6"/>',
    inventory: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    customers: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    projects: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 3v3h6V3M8 12l2 2 5-5"/>',
    opencode: '<path d="m8 9-4 3 4 3M16 9l4 3-4 3M14 5l-4 14"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21h-4v-.1A1.7 1.7 0 0 0 8.6 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H3v-4h.1A1.7 1.7 0 0 0 4.6 8.6a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V3h4v.1A1.7 1.7 0 0 0 15.4 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.2.37.56.73 1 1 .33.2.7.33 1.1.4h.1v4h-.1c-.4.07-.77.2-1.1.4-.44.27-.8.63-1 1Z"/>',
  };

  function icon(name) {
    return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${iconPaths[name] || iconPaths.dashboard}</svg>`;
  }

  function decorateNav() {
    const nav = document.querySelector('.nav');
    if (!nav) return;
    nav.querySelectorAll('.nav-item').forEach((button) => {
      const view = button.dataset.view;
      if (view === 'replay' || view === 'playbooks' || view === 'health') {
        button.remove();
        return;
      }
      const holder = button.querySelector('.nav-icon');
      if (holder && iconPaths[view]) holder.innerHTML = icon(view);
      button.classList.add('top-nav-item');
    });
    document.querySelector('#view-replay')?.remove();
  }

  function keepNewInvestigationVisible(button) {
    if (!button || button.dataset.globalVisibilityGuard === '1') return;
    button.dataset.globalVisibilityGuard = '1';
    const reveal = () => {
      if (button.hidden) button.hidden = false;
      button.removeAttribute('hidden');
    };
    reveal();
    new MutationObserver(reveal).observe(button, { attributes: true, attributeFilter: ['hidden'] });
  }

  function promoteGlobalHeaderActions() {
    const shellHeader = document.querySelector('.sidebar');
    const actions = document.querySelector('.topbar-actions');
    if (!shellHeader || !actions) return;

    document.querySelector('.sidebar-safety')?.remove();
    document.querySelector('.topbar')?.classList.add('page-titlebar-hidden');

    actions.classList.add('global-header-actions');
    if (actions.parentElement !== shellHeader) shellHeader.appendChild(actions);

    const start = document.querySelector('#topbar-start-investigation');
    if (start) {
      start.textContent = 'Nova investigação';
      start.setAttribute('aria-label', 'Nova investigação');
      keepNewInvestigationVisible(start);
    }
  }

  function restoreSimpleAnalysisForm() {
    const wizard = document.querySelector('#investigation-wizard');
    const form = document.querySelector('#analysis-form');
    if (!wizard || !form) return;
    const panels = [...wizard.querySelectorAll('.wizard-panel')];
    const movable = [];
    panels.forEach((panel) => {
      [...panel.children].forEach((child) => {
        if (child.classList.contains('wizard-step-heading') || child.classList.contains('wizard-recommended') || child.id === 'wizard-review') return;
        movable.push(child);
      });
    });
    movable.forEach((child) => form.insertBefore(child, wizard));
    wizard.remove();
    form.classList.remove('investigation-wizard-active');
  }

  function modalShell(id, title, eyebrow) {
    const aside = document.createElement('aside');
    aside.className = 'utility-modal';
    aside.id = id;
    aside.setAttribute('aria-hidden', 'true');
    aside.innerHTML = `<div class="utility-modal-backdrop" data-close-utility></div><div class="utility-modal-panel"><header><div><p class="eyebrow">${eyebrow}</p><h2>${title}</h2></div><button type="button" class="icon-button" data-close-utility aria-label="Fechar">×</button></header><div class="utility-modal-content"></div></div>`;
    document.body.appendChild(aside);
    aside.querySelectorAll('[data-close-utility]').forEach((item) => item.addEventListener('click', () => closeUtility(aside)));
    return aside;
  }

  function openUtility(modal) {
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
  }

  function closeUtility(modal) {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  function addHeaderButton(viewId, id, label, onClick) {
    const view = document.querySelector(viewId);
    const header = view?.querySelector('.panel-header');
    if (!header || document.querySelector(`#${id}`)) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary-button embedded-tool-button';
    button.id = id;
    button.textContent = label;
    header.appendChild(button);
    button.addEventListener('click', onClick);
  }

  function setupPlaybooksModal() {
    const source = document.querySelector('#view-playbooks .panel');
    if (!source) return;
    const modal = modalShell('playbooks-utility-modal', 'Playbooks', 'INVENTÁRIO E AUTOMAÇÃO');
    modal.querySelector('.utility-modal-content').appendChild(source);
    document.querySelector('#view-playbooks')?.setAttribute('hidden', 'hidden');
    addHeaderButton('#view-inventory', 'inventory-open-playbooks', 'Playbooks', () => {
      if (typeof loadPlaybooks === 'function') void loadPlaybooks();
      openUtility(modal);
    });
  }

  async function loadObservabilityIntoHealth(modal) {
    if (modal.querySelector('.observability-card')) return;
    try {
      const response = await fetch('/ui/api/observability');
      if (!response.ok) return;
      const data = await response.json();
      const holder = document.createElement('section');
      holder.className = 'observability-card';
      holder.innerHTML = `<div><p class="eyebrow">PERFORMANCE DO AGENT</p><h3>Execução e limites</h3></div><div class="observability-grid"><article><span>Eventos</span><strong>${data.sse_enabled ? 'SSE em tempo real' : 'Polling'}</strong><small>Store: ${data.execution_store || '—'}</small></article><article><span>Comandos</span><strong>${data.budgets?.commands ?? '—'}</strong><small>por investigação</small></article><article><span>IA</span><strong>${data.budgets?.ai_calls ?? '—'}</strong><small>chamadas máximas</small></article></div>`;
      modal.querySelector('.utility-modal-content')?.appendChild(holder);
    } catch { /* saúde principal continua disponível */ }
  }

  function setupHealthModal() {
    const source = document.querySelector('#view-health .panel');
    if (!source) return;
    const modal = modalShell('health-utility-modal', 'Saúde da aplicação', 'CONFIGURAÇÕES');
    modal.querySelector('.utility-modal-content').appendChild(source);
    document.querySelector('#view-health')?.setAttribute('hidden', 'hidden');
    addHeaderButton('#view-settings', 'settings-open-health', 'Saúde', () => {
      if (typeof loadHealth === 'function') void loadHealth();
      void loadObservabilityIntoHealth(modal);
      openUtility(modal);
    });
  }

  function setup() {
    document.body.classList.add('top-navigation-layout');
    decorateNav();
    promoteGlobalHeaderActions();
    restoreSimpleAnalysisForm();
    setupPlaybooksModal();
    setupHealthModal();

    const nav = document.querySelector('.nav');
    if (nav) {
      new MutationObserver(() => {
        decorateNav();
        promoteGlobalHeaderActions();
      }).observe(nav, { childList: true, subtree: true });
    }
  }

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.utility-modal.open').forEach(closeUtility);
  });

  document.addEventListener('DOMContentLoaded', () => setTimeout(setup, 0));
})();
