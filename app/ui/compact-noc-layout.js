(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const ensureButton = (container, label, id, handler) => {
    if (!container || document.getElementById(id)) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.id = id;
    button.className = 'secondary-button';
    button.textContent = label;
    button.addEventListener('click', handler);
    container.appendChild(button);
  };

  function compact() {
    const ui = window.AgentCompactUI;
    const fleet = document.getElementById('noc-fleet-panel');
    if (!ui || !fleet) return;
    const actions = $('.cmk-master-actions', fleet);
    if (!actions) return;

    ['#cmk-sync', '#cmk-poll', '#fleet-refresh'].forEach((selector) => $(selector, fleet)?.remove());

    const agents = document.getElementById('noc-agent-control');
    if (agents && agents.dataset.compactMoved !== '1') {
      agents.dataset.compactMoved = '1';
      const skillZone = $('.noc-skill-zone', agents);
      if (skillZone) skillZone.classList.add('compact-hidden');
      const modal = ui.modal('compact-agent-modal', 'Controle dos agentes', 'Ligue, desligue e escolha o escopo permitido.');
      $('.compact-modal-body', modal).appendChild(agents);
      ensureButton(actions, 'Agentes', 'compact-agent-button', () => ui.open(modal));
    }

    const policies = document.getElementById('cmk-policy-panel');
    if (policies && policies.dataset.compactMoved !== '1') {
      policies.dataset.compactMoved = '1';
      policies.open = true;
      const modal = ui.modal('compact-policy-modal', 'Correções automáticas', 'Defina quais categorias podem receber self-healing.');
      $('.compact-modal-body', modal).appendChild(policies);
      ensureButton(actions, 'Correções automáticas', 'compact-policy-button', () => ui.open(modal));
    }

    const discovery = $('.fleet-contingency', fleet);
    if (discovery && discovery.dataset.compactMoved !== '1') {
      discovery.dataset.compactMoved = '1';
      discovery.open = true;
      const modal = ui.modal('compact-discovery-modal', 'Descoberta de rede', 'Varredura manual de contingência.');
      $('.compact-modal-body', modal).appendChild(discovery);
      ensureButton(actions, 'Descoberta de rede', 'compact-discovery-button', () => ui.open(modal));
    }

    const common = document.getElementById('cmk-common-filter');
    const history = document.getElementById('cmk-history-filters');
    if ((common || history) && !document.getElementById('compact-filter-modal')) {
      const modal = ui.modal('compact-filter-modal', 'Filtro', 'Filtre cliente, site, host, IP, serviço, resultado ou categoria.');
      const body = $('.compact-modal-body', modal);
      if (common) body.appendChild(common);
      if (history) body.appendChild(history);
      ensureButton(actions, 'Filtro', 'compact-filter-button', () => ui.open(modal));
    }

    const master = document.getElementById('cmk-master-status');
    const result = document.getElementById('cmk-action-result');
    if (master && result && !master.parentElement?.classList.contains('cmk-status-pair')) {
      const pair = document.createElement('div');
      pair.className = 'cmk-status-pair';
      master.insertAdjacentElement('beforebegin', pair);
      pair.append(master, result);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', compact); else compact();
  setInterval(() => { if (!document.hidden) compact(); }, 1000);
})();
