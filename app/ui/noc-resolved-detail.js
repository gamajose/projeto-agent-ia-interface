(() => {
  function adjust() {
    const root = document.getElementById('noc-detail');
    if (!root) return;
    const status = root.querySelector('.noc-status')?.textContent?.trim().toLocaleLowerCase('pt-BR') || '';
    if (!status.includes('resolvido')) return;
    const analysis = [...root.querySelectorAll('.noc-analysis')].find((block) => block.querySelector('h4')?.textContent?.includes('Causa provável'));
    if (!analysis) return;
    const paragraphs = analysis.querySelectorAll('p');
    if (paragraphs[0]?.textContent.includes('ainda não produziu uma causa provável')) {
      paragraphs[0].textContent = 'O Checkmk confirmou a recuperação antes de existir evidência suficiente para atribuir uma causa raiz com segurança.';
    }
    if (paragraphs[1]?.textContent.includes('Aguardando processamento')) {
      paragraphs[1].textContent = 'Incidente normalizado automaticamente após recuperação observada pelo Checkmk.';
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', adjust); else adjust();
  setInterval(() => { if (!document.hidden) adjust(); }, 1200);
})();
