(() => {
  const n2Icon = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h11l3 3v15H5z"/><path d="M16 3v4h4M8 11h8M8 15h8M8 19h5"/></svg>';

  function nodeContainsNavItem(node) {
    if (!(node instanceof Element)) return false;
    return node.matches('.nav-item') || Boolean(node.querySelector('.nav-item'));
  }

  function applyNavigationPolicy() {
    const nav = document.querySelector('.nav');
    if (!nav) return;

    nav.querySelectorAll('.nav-item[data-view="customers"]').forEach((item) => item.remove());
    document.querySelector('#view-customers')?.remove();

    const n2 = nav.querySelector('.nav-item[data-view="n2"]');
    if (n2) {
      n2.classList.add('top-nav-item');
      const holder = n2.querySelector('.nav-icon');
      if (holder && (holder.dataset.navigationPolicyIcon !== 'n2' || !holder.querySelector('svg'))) {
        holder.dataset.navigationPolicyIcon = 'n2';
        holder.innerHTML = n2Icon;
      }
      const projects = nav.querySelector('.nav-item[data-view="projects"]');
      if (projects && projects.nextElementSibling !== n2) projects.insertAdjacentElement('afterend', n2);
    }
  }

  function boot() {
    applyNavigationPolicy();
    const nav = document.querySelector('.nav');
    if (!nav) return;
    new MutationObserver((mutations) => {
      const navigationChanged = mutations.some((mutation) =>
        [...mutation.addedNodes, ...mutation.removedNodes].some(nodeContainsNavItem)
      );
      if (navigationChanged) applyNavigationPolicy();
    }).observe(nav, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
