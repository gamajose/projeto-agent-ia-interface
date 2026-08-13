(() => {
  function setup() {
    const table = document.getElementById('noc-incidents-table');
    if (!table) return;
    const toolbar = table.closest('.panel')?.querySelector('.noc-toolbar');
    if (!toolbar || document.getElementById('noc-queue-filter')) return;
    const controls = document.createElement('div');
    controls.className = 'compact-queue-tools';
    controls.innerHTML = '<input id="noc-queue-filter" type="search" placeholder="Filtrar host, serviço ou estado"><select id="noc-queue-order"><option value="recent">Mais recentes</option><option value="oldest">Mais antigos</option><option value="host">Host A-Z</option></select><select id="noc-queue-size"><option value="15">15/página</option><option value="30">30/página</option><option value="50">50/página</option></select>';
    toolbar.appendChild(controls);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup); else setup();
  setInterval(setup, 1500);
})();
