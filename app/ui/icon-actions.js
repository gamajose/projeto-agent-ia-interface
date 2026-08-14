(() => {
  const ICONS = {
    edit: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/></svg>',
    delete: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 14h10l1-14M9 7V4h6v3"/></svg>',
    filter: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16l-6 7v5l-4 2v-7Z"/></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="6"/><path d="m16 16 5 5"/></svg>',
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  };

  const EXACT_ACTIONS = new Map([
    ['editar', ['edit', 'Editar']],
    ['remover', ['delete', 'Remover']],
    ['excluir', ['delete', 'Excluir']],
    ['filtrar', ['filter', 'Filtrar']],
    ['filtro', ['filter', 'Filtro']],
  ]);

  function iconButton(button, iconName, label) {
    if (!button || button.dataset.iconAction === '1') return;
    button.dataset.iconAction = '1';
    button.dataset.originalLabel = label;
    button.classList.add('icon-action-button');
    button.innerHTML = ICONS[iconName] || '';
    if (!button.getAttribute('aria-label')) button.setAttribute('aria-label', label);
    if (!button.getAttribute('title')) button.setAttribute('title', label);
  }

  function decorate(root = document) {
    root.querySelectorAll('button:not([data-icon-action]):not([data-keep-text-action])').forEach((button) => {
      const label = String(button.textContent || '').trim();
      const match = EXACT_ACTIONS.get(label.toLocaleLowerCase('pt-BR'));
      if (match) iconButton(button, match[0], match[1]);
    });
  }

  window.AgentActionIcons = {
    icons: ICONS,
    apply: iconButton,
    decorate,
  };

  const boot = () => {
    decorate();
    new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        mutation.addedNodes.forEach((node) => {
          if (!(node instanceof Element)) return;
          if (node.matches('button')) decorate(node.parentElement || document);
          else decorate(node);
        });
      }
    }).observe(document.body, { childList: true, subtree: true });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
