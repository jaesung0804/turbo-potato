/* Checkbox filters: no selection means unrestricted; choices within a field use OR. */
const FilterSelect = (() => {
  const widgets = new Map();
  const values = selection => selection === 'all' || selection == null ? []
    : [...new Set((Array.isArray(selection) ? selection : [selection]).filter(v => v !== 'all'))];
  const has = selection => values(selection).length > 0;
  const matches = (selection, value) => !has(selection) || values(selection).includes(value);
  const retain = (selection, options) => values(selection).filter(v => options.some(o => o.value === v));
  const label = (selection, name = v => v, empty = '전체') => values(selection).map(name).join(' · ') || empty;
  const read = id => [...document.getElementById(id).selectedOptions].map(o => o.value).filter(v => v !== 'all');

  function mount(select) {
    if (widgets.has(select.id)) return widgets.get(select.id);
    const title = document.getElementById(select.getAttribute('aria-labelledby')).textContent;
    const details = document.createElement('details');
    details.className = 'multi-filter';
    details.dataset.filter = select.id;
    const esc = ResultPages.escape;
    details.innerHTML = `<summary aria-labelledby="${esc(select.getAttribute('aria-labelledby'))} ${select.id}-value"><span id="${select.id}-value" class="multi-filter-value">전체</span><span class="multi-filter-arrow" aria-hidden="true">⌄</span></summary>
      <div class="multi-filter-panel"><div class="multi-filter-toolbar"><button type="button" class="multi-filter-clear">전체 보기</button><span class="multi-filter-count" role="status"></span><button type="button" class="multi-filter-close" aria-label="${esc(title)} 선택 닫기">닫기</button></div>
      <input type="search" class="multi-filter-search" aria-label="${esc(title)} 선택지 검색" placeholder="${esc(title)} 검색">
      <div class="multi-filter-options" role="group" aria-label="${esc(title)} 복수 선택"></div><p class="multi-filter-empty" hidden>검색 결과가 없습니다.</p>
      <p class="multi-filter-help">여러 개 선택 가능 · 선택하지 않으면 전체</p></div>`;
    select.multiple = true;
    select.hidden = true;
    select.after(details);
    const summary = details.querySelector('summary');
    const search = details.querySelector('.multi-filter-search');
    const options = details.querySelector('.multi-filter-options');
    const widget = { select, details, summary, search, options, signature: null };
    widgets.set(select.id, widget);
    const close = () => { details.open = false; summary.focus(); };
    const commit = selection => {
      set(select.id, selection);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    };
    details.querySelector('.multi-filter-clear').addEventListener('click', () => commit([]));
    details.querySelector('.multi-filter-close').addEventListener('click', close);
    options.addEventListener('change', event => {
      if (event.target.type !== 'checkbox') return;
      event.stopPropagation();
      commit([...options.querySelectorAll('input:checked')].map(input => input.value));
    });
    search.addEventListener('input', () => filterOptions(widget));
    details.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    });
    details.addEventListener('toggle', () => {
      if (!details.open) return;
      for (const other of widgets.values()) if (other !== widget) other.details.open = false;
      search.value = '';
      filterOptions(widget);
      position(widget);
    });
    return widget;
  }

  function position(widget) {
    const rect = widget.summary.getBoundingClientRect();
    const panel = widget.details.querySelector('.multi-filter-panel');
    const width = Math.min(Math.max(rect.width, 320), window.innerWidth - 32);
    panel.style.width = `${width}px`;
    panel.style.left = `${Math.max(16, Math.min(rect.left, window.innerWidth - width - 16)) - rect.left}px`;
    const below = window.innerHeight - rect.bottom - 12, above = rect.top - 12;
    const upward = below < 300 && above > below;
    panel.style.top = upward ? 'auto' : 'calc(100% + 6px)';
    panel.style.bottom = upward ? 'calc(100% + 6px)' : 'auto';
    panel.style.maxHeight = `${Math.max(150, Math.min(400, upward ? above : below))}px`;
  }

  function filterOptions(widget) {
    const query = widget.search.value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const option of widget.options.children) {
      option.hidden = !option.textContent.toLocaleLowerCase().includes(query);
      if (!option.hidden) visible++;
    }
    widget.details.querySelector('.multi-filter-empty').hidden = visible > 0;
  }

  function set(id, selection, choices) {
    const select = document.getElementById(id);
    select.multiple = true;
    if (choices) {
      select.replaceChildren(...choices.map(choice => new Option(choice.label, choice.value)));
    }
    const selected = new Set(values(selection));
    for (const option of select.options) option.selected = selected.has(option.value);
    const widget = mount(select);
    const options = [...select.options].filter(o => o.value !== 'all');
    const signature = JSON.stringify(options.map(o => [o.value, o.textContent]));
    if (widget.signature !== signature) {
      widget.options.innerHTML = options.map(o => `<label class="multi-filter-option"><input type="checkbox" value="${ResultPages.escape(o.value)}"><span>${ResultPages.escape(o.textContent)}</span></label>`).join('');
      widget.signature = signature;
    }
    for (const input of widget.options.querySelectorAll('input')) input.checked = selected.has(input.value);
    const names = options.filter(o => selected.has(o.value)).map(o => o.textContent);
    const text = names.length > 2 ? `${names.slice(0, 2).join(' · ')} 외 ${names.length - 2}개` : names.join(' · ') || '전체';
    widget.details.querySelector('.multi-filter-value').textContent = text;
    widget.summary.title = names.join(' · ') || '전체';
    widget.details.classList.toggle('has-selection', names.length > 0);
    widget.details.querySelector('.multi-filter-count').textContent = names.length ? `${names.length}개 선택` : '전체 선택지 포함';
    widget.search.hidden = options.length <= 8;
    filterOptions(widget);
    return read(id);
  }

  function init() {
    document.querySelectorAll('select[data-multi-filter]').forEach(select => set(select.id, read(select.id)));
    document.addEventListener('click', event => {
      for (const widget of widgets.values()) if (!widget.details.contains(event.target)) widget.details.open = false;
    });
    const reposition = () => { for (const widget of widgets.values()) if (widget.details.open) position(widget); };
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
  }
  return { values, has, matches, retain, label, read, set, init };
})();
