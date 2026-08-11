(() => {
  let installed = false;

  async function request(path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const headers = { ...(options.headers || {}) };
    if (method !== 'GET') headers['X-Agent-UI'] = '1';
    let body = options.body;
    if (body && typeof body === 'object' && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, method, headers, body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  function installScopeControl() {
    const panel = document.querySelector('#noc-fleet-panel');
    const actions = panel?.querySelector('.fleet-actions');
    const oldButton = panel?.querySelector('#fleet-start');
    if (!panel || !actions || !oldButton || installed) return false;

    installed = true;
    const holder = document.createElement('label');
    holder.className = 'fleet-scope-field';
    holder.innerHTML = '<span>Faixa da nova descoberta</span><input id="fleet-scope" autocomplete="off" placeholder="172.27.* ou 172.27.1"><small>Ex.: 172.27.* = /16 · 172.27.1 = /24 · CIDR também é aceito.</small>';
    actions.insertBefore(holder, oldButton);

    const button = oldButton.cloneNode(true);
    oldButton.replaceWith(button);
    button.addEventListener('click', async () => {
      const input = document.querySelector('#fleet-scope');
      const scope = input?.value.trim() || null;
      button.disabled = true;
      try {
        await request('/ui/api/noc/fleet/start', { method: 'POST', body: { scope } });
        if (typeof window.loadFleet === 'function') await window.loadFleet(true);
        else window.setTimeout(() => window.location.reload(), 350);
      } catch (error) {
        window.alert(error.message);
      } finally {
        button.disabled = false;
      }
    });

    const status = panel.querySelector('#fleet-status');
    if (status) {
      new MutationObserver(() => syncScopeState()).observe(status, { childList: true, subtree: true, characterData: true });
    }
    syncScopeState();
    return true;
  }

  async function syncScopeState() {
    const input = document.querySelector('#fleet-scope');
    const button = document.querySelector('#fleet-start');
    if (!input || !button) return;
    try {
      const data = await request('/ui/api/noc/fleet');
      const run = data.run || {};
      const running = data.phase === 'running';
      input.disabled = running;
      const cidrs = Array.isArray(run.cidrs) ? run.cidrs : [];
      if (running && cidrs.length) {
        input.value = cidrs.join(', ');
        input.title = 'A faixa desta execução está persistida e não será alterada até ela terminar.';
      } else if (!running && input.disabled) {
        input.disabled = false;
      }
      const card = document.querySelector('#fleet-status .fleet-state-copy');
      if (card && cidrs.length && !card.querySelector('.fleet-active-scope')) {
        card.insertAdjacentHTML('beforeend', `<small class="fleet-active-scope">Faixa: ${cidrs.join(', ')}</small>`);
      }
    } catch { /* painel original continua funcionando */ }
  }

  function boot() {
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (installScopeControl() || attempts > 240) window.clearInterval(timer);
    }, 250);
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
