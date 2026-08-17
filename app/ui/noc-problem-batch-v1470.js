(() => {
  const state = {
    groups: [],
    loading: false,
    runningProcedure: '',
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
    body.innerHTML = `
      <section class="noc-problem-batch-head">
        <div>
          <span>SKILL ÚNICA</span>
          <strong>NOC Master Skill</strong>
          <small>Escolha o tipo do problema. A aplicação atualiza a fotografia do Checkmk antes de enfileirar o lote.</small>
        </div>
        <button type="button" class="ghost-button" id="noc-problem-batch-refresh">Atualizar problemas</button>
      </section>
      <div id="noc-problem-batch-summary" class="noc-problem-batch-summary"></div>
      <div id="noc-problem-batch-message" class="noc-problem-batch-message"></div>
      <section id="noc-problem-batch-groups" class="noc-problem-batch-groups"></section>`;
    $('#noc-problem-batch-refresh', modal)?.addEventListener('click', () => void loadGroups(true));
    return modal;
  }

  async function openBatch() {
    const ui = window.AgentCompactUI;
    const modal = ensureModal();
    if (!ui || !modal) return;
    ui.open(modal);
    await loadGroups(false);
  }

  function setMessage(message = '', error = false) {
    const target = $('#noc-problem-batch-message', ensureModal() || document);
    if (!target) return;
    target.textContent = String(message || '');
    target.classList.toggle('error', Boolean(error));
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
      const running = state.runningProcedure === procedureId;
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
        <button type="button" class="primary-button noc-problem-batch-run" data-procedure="${esc(procedureId)}" ${running ? 'disabled' : ''}>
          ${running ? 'Enfileirando…' : `Arrumar todos (${Number(item.host_count || 0)})`}
        </button>
      </article>`;
    }).join('');

    target.querySelectorAll('.noc-problem-batch-run').forEach((button) => {
      button.addEventListener('click', () => void runProcedure(String(button.dataset.procedure || '')));
    });
  }

  async function loadGroups(force = false) {
    if (state.loading && !force) return;
    state.loading = true;
    setMessage('');
    render();
    try {
      const data = await request('/ui/api/noc/problem-groups');
      state.groups = Array.isArray(data.groups) ? data.groups : [];
      if (String(data.status || '') !== 'completed') {
        setMessage(data.error || 'A fotografia do Checkmk não pôde ser concluída.', true);
      }
    } catch (error) {
      state.groups = [];
      setMessage(error.message, true);
    } finally {
      state.loading = false;
      render();
    }
  }

  async function runProcedure(procedureId) {
    const group = state.groups.find((item) => String(item.procedure_id || '') === procedureId);
    if (!group || state.runningProcedure) return;
    const hostCount = Number(group.host_count || 0);
    const problemCount = Number(group.problem_count || 0);
    const accepted = window.confirm(
      `Executar “${group.title || procedureId}” em todos os ${hostCount} host(s) que ainda apresentarem este problema?\n\n` +
      'A lista será atualizada novamente no Checkmk antes da execução. Alertas que já desapareceram não serão incluídos.',
    );
    if (!accepted) return;

    state.runningProcedure = procedureId;
    setMessage(`Atualizando o Checkmk e preparando ${problemCount} alerta(s) para o lote…`);
    render();
    try {
      const run = await request(`/ui/api/noc/problem-groups/${encodeURIComponent(procedureId)}/run`, {
        method: 'POST',
        body: { sites: [] },
      });
      if (run?.id) {
        window.AgentNocSelectedProgress?.start?.(run.id);
        setMessage(`Lote iniciado: ${run.batch?.problem_count || problemCount} problema(s) em ${run.batch?.host_count || hostCount} host(s). Acompanhe o progresso na tela do NOC.`);
      } else {
        setMessage('O lote foi aceito, mas o identificador da execução não foi retornado.', true);
      }
      await loadGroups(true);
    } catch (error) {
      setMessage(error.message, true);
    } finally {
      state.runningProcedure = '';
      render();
    }
  }

  let observer = null;
  let wiringTimer = null;

  function stopWiring() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    if (wiringTimer) {
      window.clearInterval(wiringTimer);
      wiringTimer = null;
    }
  }

  function wire() {
    const buttonReady = ensureMainButton();
    const skillReady = normalizeManualSkill();
    if (buttonReady && skillReady) stopWiring();
    return buttonReady && skillReady;
  }

  function startWiring() {
    if (wire()) return;
    if (!observer) {
      observer = new MutationObserver(() => {
        if (wire()) stopWiring();
      });
      observer.observe(document.documentElement, { childList: true, subtree: true });
    }
    if (!wiringTimer) {
      wiringTimer = window.setInterval(() => {
        if (!document.hidden && wire()) stopWiring();
      }, 1000);
    }
  }

  document.addEventListener('DOMContentLoaded', () => startWiring());
  document.addEventListener('click', (event) => {
    if (event.target.closest('#compact-agent-button, #noc-manual-button')) window.setTimeout(startWiring, 40);
  });
  window.addEventListener('beforeunload', stopWiring);
  startWiring();
})();
