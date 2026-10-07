/* Presentation-only behavior shared by the public pages. */
(() => {
  try {
    document.body.classList.toggle('dark-mode', localStorage.getItem('realEstateDashboardDarkMode') === '1');
  } catch (_) { /* The site also works when browser storage is unavailable. */ }

  const popovers = [...document.querySelectorAll('[data-popover]')];
  function closeOthers(except) {
    popovers.forEach(panel => { if (panel !== except) panel.open = false; });
  }
  popovers.forEach(panel => panel.addEventListener('toggle', () => {
    if (panel.open) closeOthers(panel);
  }));
  const filterPanels = popovers.filter(panel => panel.closest('.filter-toolbar') && !panel.classList.contains('score-help'));
  function showActiveConditions() {
    filterPanels.forEach(panel => {
      const count = [...panel.querySelectorAll('input[type="number"], input[type="checkbox"], select')].filter(input => {
        if (input.disabled) return false;
        if (input.type === 'checkbox') return input.checked;
        if (input.multiple) return [...input.selectedOptions].some(option => option.value !== 'all');
        if (input.id === 'recent-evidence-select') return input.value === 'all';
        return input.value !== '' && input.value !== 'all';
      }).length;
      const summary = panel.querySelector('summary');
      let badge = summary.querySelector('.condition-count');
      if (count && !badge) {
        badge = document.createElement('span');
        badge.className = 'condition-count';
        summary.append(badge);
      }
      if (badge) { badge.textContent = String(count); badge.hidden = !count; }
      summary.classList.toggle('has-conditions', count > 0);
    });
  }
  ['input', 'change', 'click'].forEach(type => document.addEventListener(type, showActiveConditions));
  const evidenceNote = document.getElementById('recent-evidence-note');
  if (evidenceNote) new MutationObserver(showActiveConditions).observe(evidenceNote, {childList: true});
  showActiveConditions();
  const candidateFilters = document.querySelector('.potential-filter-disclosure');
  if (candidateFilters) {
    const compact = window.matchMedia('(max-width:760px)');
    const setFilterView = () => { candidateFilters.open = !compact.matches; };
    setFilterView();
    compact.addEventListener('change', setFilterView);
  }
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-popover]')) closeOthers();
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const open = popovers.find(panel => panel.open);
    if (open) {
      open.open = false;
      open.querySelector('summary').focus();
    }
  });

  const viewButtons = [...document.querySelectorAll('[data-workspace-view]')];
  viewButtons.forEach(button => button.addEventListener('click', () => {
    document.body.dataset.workspaceView = button.dataset.workspaceView;
    viewButtons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    // Leaflet already responds to resize; reveal the map before measuring it.
    window.dispatchEvent(new Event('resize'));
    window.dispatchEvent(new Event('workspaceviewchange'));
  }));
})();
