(() => {
  const STORAGE_KEY = 'agent-ia:noc:problem-batch-run';
  const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
  const state = {
    groups: [],
    loading: false,
    runningProcedure: '',
    activeRunId: '',
    activeProcedure: '',
    activeTitle: '',
    activeTerminal: false,
    queueTimer: null,
    lastRun: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
  const MASTER_SKILL_HELP = 'A NOC Master Skill é a única fonte de conhecimento. O problema selecionado define o procedimento interno; o Playbook continua opcional para complementar a execução.';

  async function request(path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const headers = { ...(options.headers || {}) };
    let body = options.body;
    if (method !== 'GET') headers['X-Agent-UI'] = '1';
    if (body && typeof body === 'object') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, method, headers, body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Falha HTTP ${response.status}`);
    return data;
  }

  function normalizeManualSkill() {
    const modal = $('#noc-manual-execution-modal');
    const select = $('#noc-manual-skill', modal || document);
    if (!modal || !select) return false;
    const holder = select.closest('.noc-manual-guidance > div') || select.parentElement;
    if (!holder) return false;
    if (modal.dataset.masterSkillNormalized === '1' && $('.noc-master-skill-static', holder)) return true;

    select.value = '';
    select.hidden = true;
    holder.classList.add('noc-master-skill-holder');
    const title = $('strong', holder);
    if (title && title.textContent !== 'Skill única') title.textContent = 'Skill única';
    if (!$('.noc-master-skill-static', holder)) {
      const badge = document.createElement('div');
      badge.className = 'noc-master-skill-static';
      badge.innerHTML = '<b>NOC Master Skill</b><span>Procedimento identificado pelo sensor/erro</span>';
      holder.appendChild(badge);
    }
    const help = $('.noc-manual-guidance > small', modal);
    if (help && help.textContent !== MASTER_SKILL_HELP) help.textContent = MASTER_SKILL_HELP;
    modal.dataset.masterSkillNormalized = '1';
    return true;
  }

  function ensureMainButton() {
    const power = $('#noc-agent-control .noc-agent-power');
    if (!power) return false;
    if ($('#noc-problem-batch-button', power)) return true;

    const button = document.createElement('button');
    button.type = 'button';
    button.id = 'noc-problem-batch-button';
    button.className = 'secondary-button noc-problem-batch-button';
    button.textContent = 'Corrigir por problema';
    button.title = 'Agrupar todos os hosts pelo mesmo problema e executar o procedimento correspondente da NOC Master Skill';
    button.addEventListener('click', () => void openBatch());
    const manual = $('#noc-manual-button', power);
    if (manual) power.insertBefore(button, manual);
    else power.prepend(button);
    return true;
  }

  function ensureModal() {
    const ui = window.AgentCompactUI;
    if (!ui) return null;
    const modal = ui.modal(
      'noc-problem-batch-modal',
      'Correção em lote por problema',
      'A NOC Master Skill agrupa os alertas atuais e executa um único procedimento em todos os hosts correspondentes.',
    );
    if (modal.dataset.batchReady === '1') return modal;

    modal.dataset.batchReady = '1';
    modal.classList.add('noc-problem-batch-modal');
    const body = $('.compact-modal-body', modal);
    if (!body) return modal;
    body.innerHTML = `
      <section class="noc-problem-batch-head">
        <div>
          <span>SKILL ÚNICA</span>
          <strong>NOC Master Skill</strong>
          <small>A lista abre usando a última fotografia concluída. “Atualizar problemas” força uma nova ronda completa do Checkmk.</small>
        </div>
        <button type="button" class="ghost-button" id="noc-problem-batch-refresh">Atualizar problemas</button>
      </section>
      <div id="noc-problem-batch-summary" class="noc-problem-batch-summary"></div>
      <div id="noc-problem-batch-message" class="noc-problem-batch-message"></div>
      <section id="noc-problem-batch-progress" class="noc-problem-batch-progress" hidden></section>
      <section id="noc-problem-batch-groups" class="noc-problem-batch-groups"></section>`;
    $('#noc-problem-batch-refresh', modal)?.addEventListener('click', () => void loadGroups(true));
    renderQueueProgress(state.lastRun);
    return modal;
  }

  function ensureConfirmModal() {
    const ui = window.AgentCompactUI;
    if (!ui) return null;
    const modal = ui.modal('noc-problem-batch-confirm-modal', 'Confirmar correção em lote', 'Revise o procedimento antes de colocar os hosts na fila.');
    modal.classList.add('noc-problem-batch-confirm-modal');
    return modal;
  }

  function askBatchConfirmation(group) {
    const ui = window.AgentCompactUI;
    const modal = ensureConfirmModal();
    const body = modal ? $('.compact-modal-body', modal) : null;
    if (!ui || !modal || !body) return Promise.resolve(false);
    const hostCount = Number(group.host_count || 0);
    const problemCount = Number(group.problem_count || 0);
    body.innerHTML = `
      <div class="noc-batch-confirm-copy">
        <span>PROCEDIMENTO</span>
        <strong>${esc(group.title || group.procedure_id || 'Correção em lote')}</strong>
        <p>Serão preparados <b>${hostCount}</b> host(s), referentes a <b>${problemCount}</b> alerta(s) da última fotografia concluída.</p>
        <small>A aplicação evita criar um segundo lote do mesmo procedimento enquanto o primeiro ainda estiver em andamento. Cada host continua sendo validado durante a execução e só aparece como resolvido após confirmação do Checkmk.</small>
      </div>
      <div class="noc-batch-confirm-actions">
        <button type="button" class="secondary-button" data-batch-cancel>Cancelar</button>
        <button type="button" class="primary-button" data-batch-confirm>Enfileirar correção</button>
      </div>`;
    ui.open(modal);
    return new Promise((resolve) => {
      let done = false;
      const finish = (accepted) => {
        if (done) return;
        done = true;
        ui.close(modal);
        resolve(Boolean(accepted));
      };
      $('[data-batch-cancel]', modal)?.addEventListener('click', () => finish(false), { once: true });
      $('[data-batch-confirm]', modal)?.addEventListener('click', () => finish(true), { once: true });
      $('.compact-modal-close', modal)?.addEventListener('click', () => finish(false), { once: true });
      $('.compact-modal-backdrop', modal)?.addEventListener('click', () => finish(false), { once: true });
    });
  }

  async function openBatch() {
    const ui = window.AgentCompactUI;
    const modal = ensureModal();
    if (!ui || !modal) return;
    ui.open(modal);
    restoreTrackedRun();
    await loadGroups(false);
  }

  function setMessage(message = '', error = false) {
    const modal = ensureModal();
    const target = modal ? $('#noc-problem-batch-message', modal) : null;
    if (!target) return;
    target.textContent = String(message || '');
    target.classList.toggle('error', Boolean(error));
  }

  function statusLabel(job) {
    const status = String(job?.status || '').toLowerCase();
    const resolution = String(job?.resolution_status || job?.incident_status || '').toLowerCase();
    if (resolution === 'resolved') return 'Resolvido';
    if (resolution === 'watching' || resolution === 'validating') return 'Validando Checkmk';
    if (resolution === 'correcting') return 'Corrigindo';
    if (resolution === 'needs_attention' || resolution === 'unverified' || status === 'failed') return 'Não corrigido';
    if (status === 'queued') return 'Aguardando worker';
    if (status === 'running' || status === 'cancelling') return 'IA trabalhando';
    if (status === 'completed') return 'Concluído';
    if (status === 'cancelled') return 'Cancelado';
    return status || 'Preparando';
  }

  function statusClass(job) {
    const label = statusLabel(job);
    if (label === 'Resolvido' || label === 'Concluído') return 'ok';
    if (label === 'Não corrigido' || label === 'Cancelado') return 'error';
    if (label === 'Corrigindo' || label === 'Validando Checkmk' || label === 'IA trabalhando') return 'running';
    return 'queued';
  }

  function progressBar(percent) {
    const value = Math.max(0, Math.min(100, Number(percent || 0)));
    return `<div class="noc-batch-progress-bar" aria-label="${value}% concluído"><i style="width:${value}%"></i></div>`;
  }

  function renderQueueProgress(run) {
    const modal = ensureModal();
    const root = modal ? $('#noc-problem-batch-progress', modal) : null;
    if (!root) return;
    if (!run || !run.id) {
      root.hidden = true;
      root.innerHTML = '';
      return;
    }
    root.hidden = false;
    const jobs = Array.isArray(run.jobs) ? run.jobs : [];
    const progress = run.progress || {};
    const total = Number(progress.total || jobs.length || 0);
    const completed = Number(progress.completed || 0);
    const failed = Number(progress.failed || 0);
    const cancelled = Number(progress.cancelled || 0);
    const finished = completed + failed + cancelled;
    const runStatus = String(run.status || 'queued');
    const batch = run.batch || {};
    const title = batch.title || state.activeTitle || state.activeProcedure || 'Correção em lote';
    const runLabel = TERMINAL.has(runStatus)
      ? (failed ? 'Finalizado com pendências' : runStatus === 'cancelled' ? 'Cancelado' : 'Processamento concluído')
      : runStatus === 'running' ? 'Processando fila' : 'Preparando fila';

    let body = '';
    if (!jobs.length) {
      const position = Number(run.queue_position || 0);
      body = `<div class="noc-batch-queue-wait"><strong>${position > 0 ? `Posição ${position} na fila de lotes` : 'Preparando os jobs do lote'}</strong><span>Assim que os jobs forem criados, cada host aparecerá aqui com etapa, percentual e resultado.</span></div>`;
    } else {
      body = `<div class="noc-batch-job-list">${jobs.map((job, index) => {
        const queuePosition = Number(job.queue_position || 0);
        const detail = job.error || job.detail || 'Aguardando atualização da execução.';
        return `<article class="noc-batch-job ${statusClass(job)}">
          <b class="noc-batch-job-number">${index + 1}</b>
          <div class="noc-batch-job-copy">
            <div><strong>${esc(job.client_alias || job.site_id || 'Cliente')}</strong><span>${esc(job.host || 'Host')} · ${esc(job.host_address || 'sem IP')}</span></div>
            <small>${esc(job.service || 'Sensor')}</small>
            <p>${esc(detail)}</p>
            ${progressBar(job.percent)}
          </div>
          <div class="noc-batch-job-state"><em>${esc(statusLabel(job))}</em><span>${queuePosition && String(job.status) === 'queued' ? `fila ${queuePosition}` : `${Number(job.percent || 0)}%`}</span></div>
        </article>`;
      }).join('')}</div>`;
    }

    const processingErrors = Array.isArray(run.result?.processing_errors) ? run.result.processing_errors : [];
    root.innerHTML = `
      <div class="noc-batch-progress-head">
        <div><span>EXECUÇÃO EM LOTE</span><strong>${esc(title)}</strong><small>run ${esc(String(run.id).slice(0, 8))}</small></div>
        <div class="noc-batch-progress-state"><em class="${TERMINAL.has(runStatus) && failed ? 'error' : TERMINAL.has(runStatus) ? 'ok' : 'running'}">${esc(runLabel)}</em><b>${finished}/${total || jobs.length || 0}</b></div>
      </div>
      ${progressBar(progress.percent || 0)}
      <div class="noc-batch-progress-counts">
        <span><b>${Number(progress.queued || 0)}</b> aguardando</span>
        <span><b>${Number(progress.running || 0)}</b> executando/validando</span>
        <span><b>${completed}</b> resolvidos/concluídos</span>
        <span><b>${failed}</b> não corrigidos</span>
      </div>
      ${processingErrors.length ? `<div class="noc-batch-processing-errors"><strong>${processingErrors.length} item(ns) não puderam entrar na fila</strong><small>${esc(processingErrors.slice(0, 3).join(' | '))}</small></div>` : ''}
      ${body}`;
  }

  function saveTrackedRun() {
    if (!state.activeRunId) {
      sessionStorage.removeItem(STORAGE_KEY);
      return;
    }
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      runId: state.activeRunId,
      procedureId: state.activeProcedure,
      title: state.activeTitle,
    }));
  }

  function restoreTrackedRun() {
    if (state.activeRunId) return;
    try {
      const raw = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}');
      state.activeRunId = String(raw.runId || '');
      state.activeProcedure = String(raw.procedureId || '');
      state.activeTitle = String(raw.title || '');
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
    }
    if (state.activeRunId) startQueuePolling();
  }

  async function pollQueue() {
    if (!state.activeRunId) return;
    try {
      const run = await request(`/ui/api/noc/autonomy/runs/${encodeURIComponent(state.activeRunId)}`);
      state.lastRun = run;
      const runStatus = String(run.status || 'queued');
      state.activeTerminal = TERMINAL.has(runStatus);
      renderQueueProgress(run);
      render();
      if (state.activeTerminal && state.queueTimer) {
        window.clearInterval(state.queueTimer);
        state.queueTimer = null;
      }
    } catch (error) {
      setMessage(`Não foi possível atualizar a fila: ${error.message}`, true);
    }
  }

  function startQueuePolling() {
    if (!state.activeRunId) return;
    if (state.queueTimer) window.clearInterval(state.queueTimer);
    void pollQueue();
    state.queueTimer = window.setInterval(() => void pollQueue(), 1000);
  }

  function trackRun(run, group) {
    state.activeRunId = String(run?.id || '');
    state.activeProcedure = String(group?.procedure_id || run?.batch?.procedure_id || '');
    state.activeTitle = String(group?.title || run?.batch?.title || state.activeProcedure);
    state.activeTerminal = false;
    state.lastRun = run;
    saveTrackedRun();
    renderQueueProgress(run);
    startQueuePolling();
  }

  function render() {
    const modal = ensureModal();
    if (!modal) return;
    const target = $('#noc-problem-batch-groups', modal);
    const summary = $('#noc-problem-batch-summary', modal);
    if (!target || !summary) return;

    const totalProblems = state.groups.reduce((acc, item) => acc + Number(item.problem_count || 0), 0);
    const totalHosts = new Set(state.groups.flatMap((item) => Array.isArray(item.hosts) ? item.hosts : [])).size;
    summary.innerHTML = `
      <div><b>${state.groups.length}</b><span>tipos de problema</span></div>
      <div><b>${totalProblems}</b><span>alertas classificados</span></div>
      <div><b>${totalHosts}</b><span>hosts envolvidos</span></div>`;

    if (state.loading) {
      target.innerHTML = '<div class="noc-problem-batch-empty">Atualizando a fotografia do Checkmk…</div>';
      return;
    }
    if (!state.groups.length) {
      target.innerHTML = '<div class="noc-problem-batch-empty">Nenhum problema ativo foi classificado pela NOC Master Skill neste momento.</div>';
      return;
    }

    target.innerHTML = state.groups.map((item) => {
      const procedureId = String(item.procedure_id || '');
      const submitting = state.runningProcedure === procedureId;
      const active = !state.activeTerminal && state.activeRunId && state.activeProcedure === procedureId;
      const samples = Array.isArray(item.sample) ? item.sample : [];
      const sampleText = samples.slice(0, 3).map((sample) => `${sample.host || '-'} · ${sample.service || '-'}`).join(' | ');
      return `<article class="noc-problem-batch-card" data-procedure="${esc(procedureId)}">
        <div class="noc-problem-batch-card-main">
          <span>PROCEDIMENTO</span>
          <strong>${esc(item.title || procedureId)}</strong>
          <small>${esc(sampleText || 'Problemas atuais identificados pelo Checkmk')}</small>
          <code>${esc(procedureId)}</code>
        </div>
        <div class="noc-problem-batch-metrics">
          <div><b>${Number(item.problem_count || 0)}</b><span>alertas</span></div>
          <div><b>${Number(item.host_count || 0)}</b><span>hosts</span></div>
          <div><b>${Number(item.site_count || 0)}</b><span>clientes/sites</span></div>
        </div>
        <button type="button" class="primary-button noc-problem-batch-run" data-procedure="${esc(procedureId)}" ${submitting || active ? 'disabled' : ''}>
          ${submitting ? 'Enfileirando…' : active ? 'Em andamento…' : `Arrumar todos (${Number(item.host_count || 0)})`}
        </button>
      </article>`;
    }).join('');

    target.querySelectorAll('.noc-problem-batch-run').forEach((button) => {
      button.addEventListener('click', () => void runProcedure(String(button.dataset.procedure || '')));
    });
  }

  async function loadGroups(force = false) {
    if (state.loading) return;
    state.loading = true;
    setMessage(force ? 'Atualizando todos os sites do Checkmk. Esta é a operação mais demorada da tela…' : '');
    render();
    try {
      const data = await request(`/ui/api/noc/problem-groups${force ? '?refresh=1' : ''}`);
      state.groups = Array.isArray(data.groups) ? data.groups : [];
      if (String(data.status || '') !== 'completed') {
        setMessage(data.error || 'A fotografia do Checkmk não pôde ser concluída.', true);
      } else if (data.warning) {
        setMessage(data.warning, false);
      } else if (force) {
        setMessage('Fotografia do Checkmk atualizada.', false);
      }
    } catch (error) {
      if (!state.groups.length) state.groups = [];
      setMessage(error.message, true);
    } finally {
      state.loading = false;
      render();
    }
  }

  async function runProcedure(procedureId) {
    const group = state.groups.find((item) => String(item.procedure_id || '') === procedureId);
    if (!group || state.runningProcedure) return;
    const accepted = await askBatchConfirmation(group);
    if (!accepted) return;

    state.runningProcedure = procedureId;
    setMessage('Preparando o lote a partir da fotografia operacional mais recente…');
    render();
    try {
      const run = await request(`/ui/api/noc/problem-groups/${encodeURIComponent(procedureId)}/run`, {
        method: 'POST',
        body: { sites: Array.isArray(group.sites) ? group.sites : [] },
      });
      if (run?.id) {
        trackRun(run, group);
        window.AgentNocSelectedProgress?.start?.(run.id);
        if (run.batch?.reused) {
          setMessage('Já existia uma execução deste procedimento em andamento. Retomando o acompanhamento da mesma fila.');
        } else {
          setMessage(`Lote aceito. A fila abaixo mostra o andamento dos ${run.batch?.host_count || group.host_count || 0} host(s).`);
        }
      } else {
        setMessage('O lote foi aceito, mas o identificador da execução não foi retornado.', true);
      }
    } catch (error) {
      setMessage(error.message, true);
    } finally {
      state.runningProcedure = '';
      render();
    }
  }

  let retryTimer = null;
  let retryCount = 0;

  function stopRetry() {
    if (retryTimer) window.clearTimeout(retryTimer);
    retryTimer = null;
    retryCount = 0;
  }

  function wireOnce() {
    const buttonReady = ensureMainButton();
    const skillReady = normalizeManualSkill();
    return buttonReady && skillReady;
  }

  function scheduleWiring() {
    stopRetry();
    const tick = () => {
      retryTimer = null;
      if (wireOnce()) {
        retryCount = 0;
        return;
      }
      retryCount += 1;
      if (retryCount < 20) retryTimer = window.setTimeout(tick, 250);
    };
    retryTimer = window.setTimeout(tick, 0);
  }

  document.addEventListener('DOMContentLoaded', () => {
    scheduleWiring();
    restoreTrackedRun();
  });
  document.addEventListener('click', (event) => {
    if (event.target.closest('#compact-agent-button, #noc-manual-button')) {
      window.setTimeout(scheduleWiring, 40);
    }
  });
  window.addEventListener('beforeunload', () => {
    stopRetry();
    if (state.queueTimer) window.clearInterval(state.queueTimer);
  });
  if (document.readyState !== 'loading') {
    scheduleWiring();
    restoreTrackedRun();
  }
})();
