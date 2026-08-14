(() => {
  const originalFetch = window.fetch.bind(window);
  const storageKey = 'agent-ia:noc:selected-run';
  let activeRunId = sessionStorage.getItem(storageKey) || '';
  let lastRun = null;
  let timer = null;
  let observer = null;

  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  function statusLabel(status, resolutionStatus = '') {
    const value = String(status || '').toLowerCase();
    const resolution = String(resolutionStatus || '').toLowerCase();
    if (resolution === 'resolved') return 'Resolvido';
    if (resolution === 'correcting') return 'Corrigindo';
    if (resolution === 'watching' || resolution === 'validating') return 'Validando Checkmk';
    if (resolution === 'needs_attention') return 'Não corrigido';
    if (value === 'queued') return 'Aguardando worker';
    if (value === 'running' || value === 'cancelling') return 'IA trabalhando';
    if (value === 'completed') return 'Análise concluída';
    if (value === 'failed') return 'Não corrigido';
    if (value === 'cancelled') return 'Cancelado';
    return value || 'Preparando';
  }

  function statusClass(status, resolutionStatus = '') {
    const value = String(status || '').toLowerCase();
    const resolution = String(resolutionStatus || '').toLowerCase();
    if (resolution === 'resolved') return 'ok';
    if (resolution === 'needs_attention') return 'error';
    if (resolution === 'correcting' || resolution === 'watching' || resolution === 'validating') return 'running';
    if (value === 'completed') return 'ok';
    if (value === 'failed' || value === 'cancelled') return 'error';
    if (value === 'running' || value === 'cancelling') return 'running';
    return 'queued';
  }

  function root() {
    return document.querySelector('#noc-selected-run');
  }

  function progressBar(percent) {
    const value = Math.max(0, Math.min(100, Number(percent || 0)));
    return `<div class="noc-manual-progress-bar" aria-label="${value}% concluído"><i style="width:${value}%"></i></div>`;
  }

  function renderPending(run, target) {
    const position = Number(run.queue_position || 0);
    target.innerHTML = `<section class="noc-manual-progress single" aria-live="polite">
      <div class="noc-manual-progress-head">
        <div><span>EXECUÇÃO MANUAL</span><strong>Preparando correção</strong></div>
        <em class="queued">${position > 1 ? `posição ${position}` : 'iniciando'}</em>
      </div>
      ${progressBar(0)}
      <p>${position > 1 ? `Existe outra execução manual antes desta. Posição atual: ${position}.` : 'O escopo foi recebido. O processador manual está preparando a correção agora.'}</p>
    </section>`;
  }

  function renderSingle(run, job, target) {
    const status = String(job.status || run.status || 'queued');
    const resolution = String(job.resolution_status || job.incident_status || '');
    const position = Number(job.queue_position || 0);
    const detail = job.error || job.detail || (status === 'queued'
      ? (position ? `Aguardando o worker. Posição ${position}.` : 'Aguardando o worker ficar livre.')
      : 'Investigando, corrigindo e revalidando o problema.');
    target.innerHTML = `<section class="noc-manual-progress single" aria-live="polite">
      <div class="noc-manual-progress-head">
        <div><span>AJUSTE MANUAL</span><strong>${esc(job.host || 'Host')} · ${esc(job.service || 'Sensor')}</strong></div>
        <em class="${statusClass(status, resolution)}">${esc(statusLabel(status, resolution))}</em>
      </div>
      <div class="noc-manual-progress-meta">
        <span>${esc(job.host_address || job.site_id || '')}</span>
        <b>${esc(job.percent || 0)}%</b>
      </div>
      ${progressBar(job.percent)}
      <p>${esc(detail)}</p>
      ${position && status === 'queued' ? `<small>Posição na fila operacional: ${position}</small>` : ''}
    </section>`;
  }

  function renderQueue(run, jobs, target) {
    const progress = run.progress || {};
    const completed = Number(progress.completed || 0) + Number(progress.failed || 0) + Number(progress.cancelled || 0);
    target.innerHTML = `<section class="noc-manual-progress queue" aria-live="polite">
      <div class="noc-manual-progress-head">
        <div><span>FILA MANUAL</span><strong>${jobs.length} ajustes selecionados</strong></div>
        <em class="${statusClass(run.status)}">${completed}/${jobs.length} finalizados</em>
      </div>
      ${progressBar(progress.percent)}
      <div class="noc-manual-job-list">
        ${jobs.map((job, index) => {
          const status = String(job.status || 'queued');
          const resolution = String(job.resolution_status || job.incident_status || '');
          const position = Number(job.queue_position || 0);
          const detail = job.error || job.detail || (status === 'queued' ? 'Aguardando worker.' : 'Investigando, corrigindo e validando.');
          return `<article class="noc-manual-job ${statusClass(status, resolution)}">
            <b>${index + 1}</b>
            <div class="noc-manual-job-main">
              <strong>${esc(job.host || 'Host')} · ${esc(job.service || 'Sensor')}</strong>
              <small>${esc(detail)}</small>
              ${progressBar(job.percent)}
            </div>
            <div class="noc-manual-job-state">
              <em>${esc(statusLabel(status, resolution))}</em>
              <span>${position && status === 'queued' ? `fila ${position}` : `${esc(job.percent || 0)}%`}</span>
            </div>
          </article>`;
        }).join('')}
      </div>
    </section>`;
  }

  function render(run) {
    if (!run || !run.id) return;
    lastRun = run;
    const target = root();
    if (!target) return;
    target.hidden = false;
    const jobs = Array.isArray(run.jobs) ? run.jobs : [];
    if (!jobs.length) {
      const result = run.result || {};
      if (String(run.status || '') === 'completed' && Number(result.jobs_queued || 0) === 0) {
        target.innerHTML = `<section class="noc-manual-progress single"><div class="noc-manual-progress-head"><div><span>EXECUÇÃO MANUAL</span><strong>Nenhum ajuste necessário</strong></div><em class="ok">Sem problema ativo</em></div><p>Não há problema ativo correspondente ao escopo selecionado neste momento.</p></section>`;
      } else {
        renderPending(run, target);
      }
      ensureObserver();
      return;
    }
    if (jobs.length === 1) renderSingle(run, jobs[0], target);
    else renderQueue(run, jobs, target);
    ensureObserver();
  }

  function ensureObserver() {
    const target = root();
    if (!target || observer) return;
    observer = new MutationObserver(() => {
      if (!lastRun) return;
      if (!target.querySelector('.noc-manual-progress')) window.setTimeout(() => render(lastRun), 0);
    });
    observer.observe(target, { childList: true, subtree: true });
  }

  async function poll() {
    if (!activeRunId) return;
    try {
      const response = await originalFetch(`/ui/api/noc/autonomy/runs/${encodeURIComponent(activeRunId)}`);
      if (!response.ok) return;
      const run = await response.json();
      render(run);
      if (['completed', 'failed', 'cancelled'].includes(String(run.status || '')) && Array.isArray(run.jobs) && run.jobs.length) {
        window.clearInterval(timer);
        timer = null;
      }
    } catch {
      // O poll normal da tela continua funcionando; uma falha transitória não apaga o andamento.
    }
  }

  function start(runId) {
    activeRunId = String(runId || '');
    if (!activeRunId) return;
    sessionStorage.setItem(storageKey, activeRunId);
    if (timer) window.clearInterval(timer);
    void poll();
    timer = window.setInterval(poll, 900);
  }

  window.AgentNocSelectedProgress = Object.freeze({ start });

  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const input = args[0];
      const url = typeof input === 'string' ? input : String(input?.url || '');
      const method = String(args[1]?.method || input?.method || 'GET').toUpperCase();
      const selectedRun = url.includes('/ui/api/noc/autonomy/run-selected');
      const procedureBatchRun = /\/ui\/api\/noc\/problem-groups\/[^/]+\/run(?:[?#]|$)/.test(url);
      if ((selectedRun || procedureBatchRun) && method === 'POST' && response.ok) {
        response.clone().json().then((run) => {
          if (run?.id) start(run.id);
        }).catch(() => {});
      } else if (activeRunId && url.includes(`/ui/api/noc/autonomy/runs/${encodeURIComponent(activeRunId)}`) && response.ok) {
        response.clone().json().then((run) => window.setTimeout(() => render(run), 30)).catch(() => {});
      }
    } catch {
      // Não interfere no fetch original.
    }
    return response;
  };

  document.addEventListener('DOMContentLoaded', () => {
    ensureObserver();
    if (activeRunId) start(activeRunId);
  });
})();
