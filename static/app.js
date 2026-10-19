const paths = {
  fullscreen: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-13 4h2m4 0h2m-8 3h2"/>',
  check: '<rect x="3" y="3" width="18" height="18" rx="5"/><path d="m7 12 3 3 7-7"/>',
  tick: '<path d="m5 12 4 4L19 6"/>',
  note: '<path d="M13 3H6a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3h12a3 3 0 0 0 3-3v-7M8 16l1-4L18 3l3 3-9 9-4 1Z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  left: '<path d="m14 6-6 6 6 6"/>',
  right: '<path d="m10 6 6 6-6 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.note}</svg>`;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
} [char]));
const dayKey = date => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
const parseDay = key => new Date(`${key}T12:00:00`);
const todayKey = () => dayKey(new Date());
const formatDate = (date, options) => new Intl.DateTimeFormat('ru-RU', options).format(date);
const timeOf = entry => entry.starts_at ? formatDate(new Date(entry.starts_at), {
  hour: '2-digit',
  minute: '2-digit'
}) : '';
const categories = {
  personal: 'Личное',
  study: 'Учёба',
  work: 'Работа'
};
const state = {
  view: 'today',
  entries: [],
  selected: todayKey(),
  month: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  filter: 'active',
  search: '',
  priorityFilter: 'all',
  chartsOpen: false,
  completedOpen: false,
  sort: 'time',
  session: null,
  editing: null,
  kind: 'task',
  busy: false,
  ready: false
};
let toastTimer;

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').hidden = true, 3500);
}
async function api(path, method = 'GET', data) {
  const response = await fetch(`/api${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Ritm-Request': '1'
    },
    credentials: 'same-origin',
    ...(data ? {
      body: JSON.stringify(data)
    } : {})
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Не удалось выполнить запрос.');
  return result;
}

function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
}

function empty(title, description, kind = 'task') {
  const action = kind === 'note' ? 'Создать заметку' : 'Добавить дело';
  return `
    <div class="empty-state">
        <strong>${esc(title)}</strong>
      <p>${esc(description)}</p>
      <button class="button secondary" data-new="${kind}">${action}</button>
    </div>`;
}

function switchView(view) {
  state.view = view;
  state.search = '';
  render();
  window.scrollTo({
    top: 0,
    behavior: 'auto'
  });
}

function endOf(entry) {
  if (entry.ends_at) return new Date(entry.ends_at);
  if (entry.kind === 'event' && entry.starts_at) {
    return new Date(new Date(entry.starts_at).getTime() + entry.duration * 60000);
  }
  return null;
}

function timeRangeOf(entry) {
  if (!entry.starts_at) return '';
  const end = endOf(entry);
  return timeOf(entry) + (end ? '–' + formatDate(end, {hour: '2-digit', minute: '2-digit'}) : '');
}

const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
function repeatLabel(rule) {
  if (!rule) return '';
  if (rule.frequency === 'daily') return 'Каждый день';
  if (rule.weekdays.join(',') === '0,1,2,3,4') return 'По будням';
  return rule.weekdays.map(d => weekdays[d]).join(', ');
}
function updateRepeatForm() {
  const form = $('#entry-form');
  const single = Boolean(state.editing?.series_id && form.elements.scope.value === 'one');
  $('#repeat-settings').hidden = single;
  const mode = form.elements.repeat.value;
  if (mode === 'none' && form.elements.day.value) {
    const weekday = (parseDay(form.elements.day.value).getDay() + 6) % 7;
    $$('input[name="weekdays"]').forEach(el => el.checked = Number(el.value) === weekday);
  }
  $('#weekday-field').hidden = mode !== 'weekly';
  $('#repeat-until-label').hidden = mode === 'none';
  form.elements.repeat_until.min = form.elements.day.value;
  const days = $$('input[name="weekdays"]:checked').map(el => Number(el.value));
  $('#repeat-preview').textContent = mode === 'none' ? '' :
    `${mode === 'daily' ? 'Каждый день' : mode === 'workdays' ? 'Понедельник — пятница' : days.length ? days.map(d => weekdays[d]).join(', ') : 'Выбери дни недели'} · ${form.elements.repeat_until.value ? 'до ' + formatDate(parseDay(form.elements.repeat_until.value), {day:'numeric', month:'long', year:'numeric'}) : 'без окончания'}.`;
}

function workloadCharts() {
  const stats = Planner.summarize(state.entries, state.selected);
  const monday = parseDay(state.selected);
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  const week = Array.from({length:7}, (_, i) => {
    const day = new Date(monday); day.setDate(day.getDate() + i);
    const key = dayKey(day);
    return {key, day, stats:Planner.summarize(state.entries, key)};
  });
  const scale = Math.ceil(Math.max(480, ...week.map(d => d.stats.busy)) / 60) * 60;
  return `<div class="workload-grid">
    <section class="workload-card" aria-label="Загрузка выбранного дня">
      <div class="chart-heading"><h2>Время дня</h2><span>${esc(Planner.minutesText(stats.busy))} занято</span></div>
      <p class="chart-caption">По расписанию, включая выполненное</p>
      <div class="timeline-axis"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>
      <div class="day-timeline">${stats.intervals.length ? stats.intervals.map(i => {
        const conflict = stats.intervals.some(other => other !== i && Math.max(other.start, i.start) < Math.min(other.end, i.end));
        return `<button class="timeline-row" data-edit="${i.entry.id}" aria-label="${esc(i.entry.title + ', ' + timeRangeOf(i.entry) + (conflict ? ', пересечение' : ''))}"><span class="timeline-name">${esc(i.entry.title)}<small>${timeRangeOf(i.entry)}</small></span><span class="timeline-track"><span class="timeline-block ${conflict ? 'conflict' : ''} ${i.entry.done ? 'done' : ''}" data-interval-start="${i.start/stats.length*100}" data-interval-width="${(i.end-i.start)/stats.length*100}"></span></span></button>`;
      }).join('') : '<p class="chart-empty">Интервалы появятся, когда укажешь время «С» и «До».</p>'}</div>
      <div class="day-facts"><span>Важных в планах: <b>${stats.important}</b></span><span><b>${stats.untimed}</b> без полного интервала</span></div>
      ${stats.overlap ? `<p class="conflict-notice">Пересечения: ${Planner.minutesText(stats.overlap)}. Нажми на полосу, чтобы изменить запись.</p>` : '<p class="chart-caption">Пересечений нет. Загрузка учитывает только записи с началом и окончанием.</p>'}
    </section>
    <section class="workload-card" aria-label="График загрузки недели">
      <div class="chart-heading"><h2>Неделя</h2><span>${esc(Planner.minutesText(week.reduce((sum,d)=>sum+d.stats.busy,0)))} занято</span></div>
      <p class="chart-caption">Занятые часы · шкала до ${scale/60} ч · нажми на день</p>
      <div class="week-chart">${week.map((d,i) => `<button class="week-bar ${d.key === state.selected ? 'selected' : ''}" data-date="${d.key}" aria-label="${formatDate(d.day,{weekday:'long',day:'numeric',month:'long'})}: занято ${Planner.minutesText(d.stats.busy)}, выполнено ${d.stats.done} из ${d.stats.items.length}" aria-pressed="${d.key === state.selected}"><span class="bar-value">${d.stats.busy ? esc(Planner.minutesText(d.stats.busy)) : '—'}</span><span class="bar-column"><span class="bar-fill ${d.stats.overlap ? 'conflict' : ''}" data-bar-height="${d.stats.busy/scale*100}"></span></span><span class="bar-day">${weekdays[i]} <small>${d.day.getDate()}</small></span></button>`).join('')}</div>
      <p class="chart-caption week-total">Выполнено ${week.reduce((sum,d)=>sum+d.stats.done,0)} из ${week.reduce((sum,d)=>sum+d.stats.items.length,0)} записей за неделю</p>
    </section>
  </div>`;
}

function entryRow(entry) {
  const end = endOf(entry);
  const endLabel = end ? formatDate(end, {hour:'2-digit', minute:'2-digit'}) : '';
  return `<div class="entry ${entry.done ? 'completed' : ''} ${entry.priority === 'high' ? 'is-important' : ''}">
    <button class="check-button ${entry.done ? 'done' : ''}" data-toggle="${entry.id}"
      aria-label="${entry.done ? 'Вернуть' : 'Завершить'}: ${esc(entry.title)}" aria-pressed="${Boolean(entry.done)}"><span>${entry.done ? icon('tick') : ''}</span></button>
    <span class="entry-time">${entry.starts_at ? `<b>${timeOf(entry)}</b>${endLabel ? `<small>до ${endLabel}</small>` : ''}` : '<small>Без<br>времени</small>'}</span>
    <button class="entry-main" data-edit="${entry.id}">
      <span class="entry-title">${esc(entry.title)}</span>
      <span class="entry-meta"><span class="tag ${entry.category}">${categories[entry.category]}</span>
        ${entry.priority === 'high' ? '<span class="importance high">Важное</span>' : entry.priority === 'low' ? '<span>Не срочно</span>' : ''}
        ${entry.series_id ? `<span class="repeat-tag">${esc(repeatLabel(entry.recurrence))}</span>` : ''}
        ${entry.reminder >= 0 ? `<span class="reminder-tag">${entry.reminder ? 'За '+entry.reminder+' мин' : 'К началу'}</span>` : ''}
      </span>
    </button>
    <button class="icon-button entry-action" data-edit="${entry.id}" aria-label="Изменить: ${esc(entry.title)}">${icon('right')}</button>
  </div>`;
}

function weekStrip() {
  const selected = parseDay(state.selected);
  const monday = new Date(selected);
  monday.setDate(selected.getDate() - ((selected.getDay() + 6) % 7));
  const days = Array.from({length: 7}, (_, index) => {
    const date = new Date(monday);
    date.setDate(date.getDate() + index);
    const key = dayKey(date);
    const active = key === state.selected;
    const hasItems = state.entries.some(entry => entry.day === key && entry.kind !== 'note');
    const label = formatDate(date, {day: 'numeric', month: 'long'});
    return `
      <button class="date-cell ${active ? 'active' : ''}" data-date="${key}"
              aria-label="${label}" aria-pressed="${active}">
        <span class="weekday">${formatDate(date, {weekday: 'short'})}</span>
        <span class="number">${date.getDate()}</span>
        <span class="day-indicator ${hasItems ? 'has-items' : ''}"></span>
      </button>`;
  });
  return `<div class="date-strip">${days.join('')}</div>`;
}

function calendarDays(month) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const start = new Date(first);
  start.setDate(1 - ((first.getDay() + 6) % 7));
  return Array.from({
    length: 42
  }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
}

function miniCalendar() {
  return `<section class="rail-card calendar-rail"><div class="mini-cal-head"><h3>${esc(formatDate(state.month,{month:'long',year:'numeric'}))}</h3><div class="calendar-arrows"><button class="icon-button" data-month="-1" aria-label="Предыдущий месяц">${icon('left')}</button><button class="icon-button" data-month="1" aria-label="Следующий месяц">${icon('right')}</button></div></div><div class="mini-calendar">${['пн','вт','ср','чт','пт','сб','вс'].map(d=>`<span class="mini-weekday">${d}</span>`).join('')}${calendarDays(state.month).map(d=>{const key=dayKey(d);return `<button class="mini-day ${d.getMonth()!==state.month.getMonth()?'other':''} ${key===state.selected?'selected':''} ${key===todayKey()?'today':''}" data-date="${key}" aria-label="${formatDate(d,{day:'numeric',month:'long'})}">${d.getDate()}</button>`;}).join('')}</div></section>`;
}

function recordWord(count) {
  if (count % 100 >= 11 && count % 100 <= 14) return 'записей';
  return count % 10 === 1 ? 'запись' : count % 10 >= 2 && count % 10 <= 4 ? 'записи' : 'записей';
}

function renderToday() {
  const entries = state.entries.filter(e => e.day === state.selected && e.kind !== 'note')
    .sort((a,b) => (a.starts_at || 'z').localeCompare(b.starts_at || 'z'));
  const done = entries.filter(e => e.done);
  const timed = entries.filter(e => !e.done && e.starts_at);
  const untimed = entries.filter(e => !e.done && !e.starts_at);
  const stats = Planner.summarize(state.entries, state.selected);
  const date = parseDay(state.selected);
  const sections = [
    timed.length ? `<div class="agenda-group"><h3>По времени <span>${timed.length}</span></h3>${timed.map(entryRow).join('')}</div>` : '',
    untimed.length ? `<div class="agenda-group"><h3>Без времени <span>${untimed.length}</span></h3>${untimed.map(entryRow).join('')}</div>` : '',
    done.length ? `<details class="completed-group" ${state.completedOpen ? 'open' : ''}><summary>Выполнено <span>${done.length}</span></summary>${done.map(entryRow).join('')}</details>` : ''
  ].join('');
  return `<div class="day-layout">
    <section class="agenda-column">
      <div class="day-toolbar"><div class="week-controls"><button class="icon-button" data-week="-1" aria-label="Предыдущая неделя">${icon('left')}</button><button class="button quiet" id="go-today">Сегодня</button><button class="icon-button" data-week="1" aria-label="Следующая неделя">${icon('right')}</button></div><label class="jump-date">Выбрать дату<input type="date" id="jump-date" value="${state.selected}" aria-label="Перейти к дате"></label></div>
      ${weekStrip()}
      <div class="day-summary" aria-label="Итоги выбранного дня"><span><b>${entries.length-done.length}</b> в планах</span><span><b>${done.length}</b> выполнено</span><span title="Всё расписание, включая выполненные дела"><b>${Planner.minutesText(stats.busy)}</b> занято</span></div>
      <div class="section-heading"><h2>Расписание</h2><span>${entries.length} ${recordWord(entries.length)}</span></div>
      <div class="schedule">${entries.length ? sections : empty('День пока свободен', 'Добавь первое дело — со временем или просто на этот день.')}</div>
      ${stats.overlap ? `<button class="overlap-link" id="show-workload">В расписании есть пересечения: ${Planner.minutesText(stats.overlap)} <span>Посмотреть →</span></button>` : ''}
    </section>
    <aside class="right-rail">${miniCalendar()}<div class="rail-note"><h3>${esc(formatDate(date,{weekday:'long'}))}</h3><p>${stats.important ? `${stats.important} ${recordWord(stats.important)} с высокой важностью` : 'Важных дел на эту дату нет'}</p><button class="text-button" data-new="note">Записать мысль <span>↗</span></button></div></aside>
  </div>
  <details class="workload-details" ${state.chartsOpen ? 'open' : ''}><summary><span class="disclosure-title">Загрузка дня и недели</span><span class="disclosure-note">${stats.busy ? Planner.minutesText(stats.busy)+' по расписанию' : 'Время, пересечения и итоги'}</span></summary>${workloadCharts()}</details>`;
}

function renderCalendar() {
  return `<div class="view-toolbar"><h2 class="month-title">${esc(formatDate(state.month,{month:'long',year:'numeric'}))}</h2><div class="month-controls"><button class="icon-button" data-month="-1" aria-label="Предыдущий месяц">${icon('left')}</button><button class="button secondary" id="calendar-today">Сегодня</button><button class="icon-button" data-month="1" aria-label="Следующий месяц">${icon('right')}</button></div></div><div class="calendar-panel"><div class="month-header">${['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(x=>`<span>${x}</span>`).join('')}</div><div class="month-grid">${calendarDays(state.month).map(d=>{const key=dayKey(d);const entries=state.entries.filter(e=>e.day===key&&e.kind!=='note');return `<div class="month-cell ${key===state.selected?'selected':''} ${d.getMonth()!==state.month.getMonth()?'other':''}" data-calendar-date="${key}"><button class="month-number ${key===todayKey()?'today':''}" data-date="${key}" aria-label="Открыть ${formatDate(d,{day:'numeric',month:'long'})}">${d.getDate()}</button>${entries.slice(0,3).map(e=>`<button class="month-item" data-edit="${e.id}" title="${esc(timeRangeOf(e) + ' ' + e.title)}">${e.done?'✓ ':''}${timeRangeOf(e)?timeRangeOf(e)+' ':''}${esc(e.title)}</button>`).join('')}${entries.length>3?`<span class="month-more">ещё ${entries.length-3}</span>`:''}${entries.length ? `<span class="month-count">${entries.length} ${recordWord(entries.length)}</span>` : ''}</div>`;}).join('')}</div></div><p class="form-hint calendar-hint">Нажми на дату, чтобы открыть день. На компьютере свободное место в ячейке добавляет событие.</p>`;
}

function renderTasks() {
  const entries = state.entries.filter(e => e.kind !== 'note' && (state.filter === 'all' || Boolean(e.done) === (state.filter === 'done')) && (state.priorityFilter === 'all' || e.priority === state.priorityFilter) && `${e.title} ${e.details}`.toLowerCase().includes(state.search.toLowerCase())).sort((a,b) => a.day.localeCompare(b.day) || (state.sort === 'priority' ? ({high:0,normal:1,low:2}[a.priority] - {high:0,normal:1,low:2}[b.priority]) : (a.starts_at || 'z').localeCompare(b.starts_at || 'z')));
  const groups = new Map();
  entries.forEach(e => {
    if (!groups.has(e.day)) groups.set(e.day, []);
    groups.get(e.day).push(e);
  });
  return `<div class="view-toolbar"><div class="segmented">${[['active','В планах'],['done','Готово'],['all','Все']].map(([k,v])=>`<button data-filter="${k}" aria-pressed="${state.filter===k}" class="${state.filter===k?'active':''}">${v}</button>`).join('')}</div><input class="search-input" id="search-entries" placeholder="Найти задачу…" aria-label="Поиск задач" value="${esc(state.search)}"></div><div class="task-controls"><label>Важность<select id="priority-filter" aria-label="Фильтр по важности">${[['all','Любая'],['high','Высокая'],['normal','Обычная'],['low','Низкая']].map(([k,v])=>`<option value="${k}" ${state.priorityFilter===k?'selected':''}>${v}</option>`).join('')}</select></label><label>Порядок<select id="task-sort" aria-label="Сортировка задач"><option value="time" ${state.sort==='time'?'selected':''}>По времени</option><option value="priority" ${state.sort==='priority'?'selected':''}>Сначала важные</option></select></label><span class="form-hint">Повторения: ${esc(formatDate(state.month,{month:'long'}))} и выбранная неделя.</span></div><div id="task-results">${groups.size?[...groups.entries()].map(([day,list])=>`<h2 class="tasks-group-title">${day===todayKey()?'Сегодня':esc(formatDate(parseDay(day),{day:'numeric',month:'long',year:'numeric'}))}${day<todayKey()?' · ранее':''}</h2><div class="schedule">${list.map(entryRow).join('')}</div>`).join(''):empty('Задачи не найдены','Попробуй другой фильтр или добавь новую задачу.')}</div>`;
}

function renderNotes() {
  const notes = state.entries.filter(e => e.kind === 'note').sort((a, b) => b.created_at - a.created_at);
  return notes.length ? `<div class="note-grid">${notes.map(e=>`<button class="note-card" data-edit="${e.id}"><span class="tag ${e.category}">${categories[e.category]}</span><h3>${esc(e.title)}</h3><p>${esc(e.details.slice(0,250))}${e.details.length>250?'…':''}</p><small>${esc(formatDate(parseDay(e.day),{day:'numeric',month:'long'}))} · нажми, чтобы изменить</small></button>`).join('')}</div>` : empty('Заметок пока нет', 'Создай заметку. Её можно изменить в любое время.', 'note');
}

function syncTelegramBack() {
  const tg = window.Telegram?.WebApp;
  if (!tg?.initData || !tg.BackButton) return;
  const visible = state.view !== 'today' || Boolean(document.querySelector('dialog[open]'));
  if (visible) tg.BackButton.show(); else tg.BackButton.hide();
}

function render() {
  const labels = {
    today: [state.selected === todayKey() ? 'Сегодня' : formatDate(parseDay(state.selected), {day:'numeric',month:'long'}), formatDate(parseDay(state.selected), {weekday:'long',day:'numeric',month:'long',year:'numeric'})],
    calendar: ['Календарь', 'Расписание на месяц.'],
    tasks: ['Задачи', 'Все дела по датам.'],
    notes: ['Заметки', 'То, что хочется сохранить.']
  };
  const [title, subtitle] = labels[state.view];
  $('#page-title').textContent = title;
  $('#page-subtitle').textContent = subtitle;
  $('#breadcrumb').textContent = title;
  $('#heading-date').textContent = state.view === 'today' ? 'Мой день' : 'Ser / Ритм';
  $$('.nav-button').forEach(el => {
    el.classList.toggle('active', el.dataset.view === state.view);
    if (el.dataset.view === state.view) el.setAttribute('aria-current', 'page');
    else el.removeAttribute('aria-current');
  });
  $('#nav-count').textContent = state.entries.filter(e => e.day === todayKey() && !e.done && e.kind !== 'note').length;
  $('#content').innerHTML = state.view === 'today' ? renderToday() : state.view === 'calendar' ? renderCalendar() : state.view === 'tasks' ? renderTasks() : renderNotes();
  syncTelegramBack();
  // Dynamic values are assigned as properties so the CSP can forbid inline scripts.
  $$('[data-bar-height]').forEach(el => el.style.height = `${el.dataset.barHeight}%`);
  $$('[data-interval-start]').forEach(el => {
    el.style.left = `${el.dataset.intervalStart}%`;
    el.style.width = `${el.dataset.intervalWidth}%`;
  });
  const fill = $('.progress-fill');
  if (fill) {
    const all = state.entries.filter(e => e.day === state.selected && e.kind !== 'note');
    fill.style.width = `${all.length?all.filter(e=>e.done).length/all.length*100:0}%`;
  }
}

function updateDurationPreview() {
  const form = $('#entry-form');
  const start = form.elements.time.value;
  const end = form.elements.end_time.value;
  const output = $('#duration-summary');
  output.hidden = state.kind === 'note' || (!start && !end);
  if (output.hidden) return;
  if (!start) { output.textContent = 'Сначала укажи время начала.'; return; }
  if (!end) { output.textContent = 'Можно указать время окончания.'; return; }
  const minutesOf = value => Number(value.slice(0, 2)) * 60 + Number(value.slice(3, 5));
  const duration = minutesOf(end) - minutesOf(start);
  if (duration <= 0) {
    output.textContent = 'Окончание должно быть позже начала в выбранный день.';
    return;
  }
  const hours = Math.floor(duration / 60);
  const minutes = duration % 60;
  output.textContent = 'Длительность: ' + [hours ? `${hours} ч` : '', minutes ? `${minutes} мин` : ''].filter(Boolean).join(' ');
}

function setKind(kind) {
  state.kind = kind;
  $$('[data-kind]').forEach(b => {
    b.classList.toggle('active', b.dataset.kind === kind);
    b.setAttribute('aria-pressed', String(b.dataset.kind === kind));
  });
  $('#time-range-row').hidden = kind === 'note';
  $('#priority-label').hidden = kind === 'note';
  $('#reminder-label').hidden = kind === 'note';
  $('#entry-form').elements.time.required = kind === 'event';
  $('#entry-form').elements.end_time.required = kind === 'event';
  $('#repeat-section').hidden = kind === 'note';
  $('#reminder-hint').hidden = kind === 'note';
  if (kind === 'note') $('#extra-settings').open = true;
  $('#entry-title').placeholder = kind === 'note' ? 'О чём эта заметка?' : kind === 'event' ? 'Название события' : 'Что нужно сделать?';
  const names = {task:'задачу',event:'событие',note:'заметку'};
  $('#dialog-title').textContent = state.editing ? 'Изменить запись' : 'Добавить ' + names[kind];
  $('#save-entry').textContent = state.editing ? 'Сохранить' : 'Добавить';
  updateRepeatForm();
  updateDurationPreview();
}

function openEntry(id = null, kind = 'task', day = state.selected) {
  if (!state.ready) return;
  const entry = id ? state.entries.find(e => e.id === id) : null;
  state.editing = entry || null;
  const form = $('#entry-form');
  form.reset();
  $('#extra-settings').open = Boolean(entry?.details || entry?.reminder >= 0);
  $('#form-error').textContent = '';
  $('#dialog-title').textContent = entry ? 'Изменить запись' : 'Добавить запись';
  $('#delete-entry').hidden = !entry;
  form.elements.day.value = entry ? entry.day : day;
  if (entry) {
    for (const key of ['title', 'details', 'category', 'priority', 'reminder']) form.elements[key].value = entry[key];
    const toInput = date => date ? `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}` : '';
    form.elements.time.value = toInput(entry.starts_at ? new Date(entry.starts_at) : null);
    form.elements.end_time.value = toInput(endOf(entry));
  }
  if (entry?.recurrence) {
    const rule = entry.recurrence;
    form.elements.repeat.value = rule.frequency === 'daily' ? 'daily' : 'weekly';
    $$('input[name="weekdays"]').forEach(el => el.checked = rule.weekdays.includes(Number(el.value)));
    form.elements.repeat_until.value = rule.until || '';
  } else {
    const weekday = (parseDay(form.elements.day.value).getDay() + 6) % 7;
    $$('input[name="weekdays"]').forEach(el => el.checked = Number(el.value) === weekday);
  }
  $('#scope-label').hidden = !entry?.series_id;
  $('#series-description').hidden = !entry?.series_id;
  $('#series-description').textContent = entry?.series_id ? `${repeatLabel(entry.recurrence)} · ${entry.recurrence.timezone}. Выбранный режим действует на изменения и удаление.` : '';
  setKind(entry ? entry.kind : kind);
  $('#entry-dialog').showModal();
  $('#entry-dialog').scrollTop = 0;
  syncTelegramBack();
  setTimeout(() => $('#entry-title').focus(), 50);
}
let refreshVersion = 0;
async function refresh() {
  const version = ++refreshVersion;
  const days = calendarDays(state.month);
  const first = dayKey(days[0]), last = dayKey(days[41]);
  const requests = [api(`/entries?from=${first}&to=${last}`)];
  if (state.selected < first || state.selected > last) {
    const start = parseDay(state.selected), end = parseDay(state.selected);
    start.setDate(start.getDate() - 7); end.setDate(end.getDate() + 7);
    requests.push(api(`/entries?from=${dayKey(start)}&to=${dayKey(end)}`));
  }
  const results = await Promise.all(requests);
  if (version !== refreshVersion) return;
  state.entries = [...new Map(results.flatMap(r => r.entries).map(e => [e.id, e])).values()];
  render();
}
function refreshView() { render(); refresh().catch(e => toast(e.message)); }
async function saveEntry(event) {
  event.preventDefault();
  if (state.busy) return;
  const form = event.target;
  const values = Object.fromEntries(new FormData(form));
  const starts = state.kind !== 'note' && values.time ? new Date(`${values.day}T${values.time}:00`) : null;
  const ends = state.kind !== 'note' && values.end_time ? new Date(`${values.day}T${values.end_time}:00`) : null;
  if (ends && (!starts || Number.isNaN(ends.getTime()) || ends <= starts)) {
    $('#form-error').textContent = 'Укажи начало и конец: окончание должно быть позже начала в выбранный день.';
    return;
  }
  if (starts && Number.isNaN(starts.getTime())) {
    $('#form-error').textContent = 'Проверь дату и время.';
    return;
  }
  if (Number(values.reminder) >= 0 && !starts && state.kind !== 'note') {
    $('#form-error').textContent = 'Для напоминания укажи время.';
    return;
  }
  const payload = {
    title: values.title,
    details: values.details,
    day: values.day,
    category: values.category,
    priority: values.priority,
    kind: state.kind,
    starts_at: starts ? starts.toISOString() : null,
    ends_at: ends ? ends.toISOString() : null,
    reminder: state.kind === 'note' ? -1 : Number(values.reminder),
    duration: starts && ends ? Math.round((ends - starts) / 60000) : 60,
    done: state.editing?.done || false,
    scope: values.scope || 'one'
  };
  if (state.kind !== 'note' && (!state.editing?.series_id || values.scope === 'future')) {
    if (values.repeat !== 'none') {
      const days = values.repeat === 'workdays' ? [0,1,2,3,4] : $$('input[name="weekdays"]:checked').map(el => Number(el.value));
      if (values.repeat === 'weekly' && !days.length) { $('#form-error').textContent = 'Выбери хотя бы один день недели.'; return; }
      payload.recurrence = {
        frequency: values.repeat === 'daily' ? 'daily' : 'weekly', weekdays: days,
        timezone: state.editing?.recurrence?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone,
        until: values.repeat_until || null
      };
    } else payload.recurrence = null;
  }
  if (state.editing?.series_id && values.scope === 'future' && values.day !== state.editing.occurrence_day) {
    $('#form-error').textContent = 'Для изменения серии оставь дату выбранного повторения.'; return;
  }
  state.busy = true;
  $('#save-entry').disabled = true;
  try {
    const editing = state.editing;
    const saved = await api(editing ? `/entries/${editing.id}` : '/entries', editing ? 'PUT' : 'POST', payload);
    if (saved.day !== payload.day) { state.selected = saved.day; state.month = new Date(parseDay(saved.day).getFullYear(), parseDay(saved.day).getMonth(), 1); }
    $('#entry-dialog').close();
    await refresh();
    toast(editing ? 'Изменения сохранены' : 'Запись добавлена');
  } catch (error) {
    $('#form-error').textContent = error.message;
  } finally {
    state.busy = false;
    $('#save-entry').disabled = false;
  }
}
async function toggleEntry(id) {
  const entry = state.entries.find(e => e.id === id);
  if (!entry || state.busy) return;
  state.busy = true;
  try {
    await api(`/entries/${id}`, 'PUT', {
      ...entry,
      done: !entry.done
    });
    await refresh();
    toast(entry.done ? 'Вернули в планы' : 'Запись завершена');
    if (window.Telegram?.WebApp?.initData) window.Telegram.WebApp.HapticFeedback?.notificationOccurred('success');
  } catch (e) {
    toast(e.message);
  } finally {
    state.busy = false;
  }
}
async function deleteEntry() {
  if (!state.editing || state.busy) return;
  const scope = state.editing.series_id ? $('#entry-form').elements.scope.value : 'one';
  if (!confirm(scope === 'future' ? 'Удалить эту и следующие записи серии? История выполнения сохранится.' : 'Удалить только эту запись?')) return;
  state.busy = true;
  try {
    await api(`/entries/${state.editing.id}?scope=${scope}`, 'DELETE', {});
    $('#entry-dialog').close();
    await refresh();
    toast('Запись удалена');
  } catch (e) {
    $('#form-error').textContent = e.message;
  } finally {
    state.busy = false;
  }
}


function showInfo() {
  const s = state.session;
  $('#info-status').className = 'info-status';
  $('#info-status').textContent = s?.demo ? 'Локальная версия. Записи остаются на этом компьютере. Напоминания появляются в открытом приложении.' : s?.user.chat_id ? 'Telegram подключён. Напоминания приходят в личные сообщения, пока сервер работает.' : 'Вход через Telegram. Для напоминаний открой личный чат с ботом и отправь /start, затем переоткрой приложение.';
  $('#info-dialog').showModal();
  syncTelegramBack();
}

function localReminders() {
  if (!state.session?.demo) return;
  const now = Date.now() / 1000;
  for (const e of state.entries) {
    if (e.done || e.reminder_at == null || e.reminder_at > now || now - e.reminder_at > 86400) continue;
    const key = `ritm-seen:${e.id}:${e.reminder_at}`;
    if (sessionStorage.getItem(key)) continue;
    sessionStorage.setItem(key, '1');
    toast(`Пора: ${e.title}`);
  }
}
document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (button) {
    if (button.dataset.view) switchView(button.dataset.view);
    else if (button.dataset.new) openEntry(null, button.dataset.new);
    else if (button.dataset.edit) openEntry(button.dataset.edit);
    else if (button.dataset.toggle) toggleEntry(button.dataset.toggle);
    else if (button.dataset.kind) setKind(button.dataset.kind);
    else if (button.dataset.date) {
      state.selected = button.dataset.date;
      const selectedDate = parseDay(state.selected);
      state.month = new Date(selectedDate.getFullYear(), selectedDate.getMonth(), 1);
      state.view = 'today';
      refreshView();
    } else if (button.dataset.week) {
      const day = parseDay(state.selected); day.setDate(day.getDate() + Number(button.dataset.week) * 7);
      state.selected = dayKey(day); state.month = new Date(day.getFullYear(), day.getMonth(), 1);
      refreshView();
    } else if (button.dataset.formDay !== undefined) {
      const day = new Date(); day.setDate(day.getDate() + Number(button.dataset.formDay));
      $('#entry-form').elements.day.value = dayKey(day); updateRepeatForm();
    } else if (button.id === 'show-workload') {
      state.chartsOpen = true; const details = $('.workload-details'); details.open = true;
      details.scrollIntoView({behavior:'smooth',block:'start'});
    } else if (button.dataset.month) {
      state.month = new Date(state.month.getFullYear(), state.month.getMonth() + Number(button.dataset.month), 1);
      refreshView();
    } else if (button.dataset.filter) {
      state.filter = button.dataset.filter;
      render();
    } else if (button.id === 'go-today') {
      state.selected = todayKey();
      state.month = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
      refreshView();
    } else if (button.id === 'calendar-today') {
      state.month = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
      refreshView();
    }
  }
  if (!button) {
    const cell = event.target.closest('[data-calendar-date]');
    if (cell) {
      if (matchMedia('(max-width: 600px)').matches) {
        state.selected = cell.dataset.calendarDate; state.view = 'today'; refreshView(); window.scrollTo({top:0});
      } else openEntry(null, 'event', cell.dataset.calendarDate);
    }
  }
});
document.addEventListener('input', event => {
  if (event.target.closest('#time-range-row')) updateDurationPreview();
  if (event.target.id === 'search-entries') {
    state.search = event.target.value;
    const focus = event.target.selectionStart;
    render();
    $('#search-entries').focus();
    $('#search-entries').setSelectionRange(focus, focus);
  }
});
document.addEventListener('toggle', event => {
  if (event.target.matches('.workload-details') && event.target.isConnected) state.chartsOpen = event.target.open;
  if (event.target.matches('.completed-group') && event.target.isConnected) state.completedOpen = event.target.open;
}, true);
document.addEventListener('change', event => {
  if (event.target.id === 'jump-date' && event.target.value) {
    state.selected = event.target.value;
    const day = parseDay(state.selected); state.month = new Date(day.getFullYear(),day.getMonth(),1);
    refreshView();
  }
  if (event.target.closest('#repeat-section') || event.target.name === 'day') updateRepeatForm();
  if (event.target.id === 'priority-filter') { state.priorityFilter = event.target.value; render(); }
  if (event.target.id === 'task-sort') { state.sort = event.target.value; render(); }
});
$$('dialog').forEach(dialog => dialog.addEventListener('close', syncTelegramBack));
$('#entry-form').addEventListener('submit', saveEntry);
$('#add-entry').onclick = () => openEntry(null, state.view === 'notes' ? 'note' : 'task');
$('#delete-entry').onclick = deleteEntry;
$('#close-dialog').onclick = $('#cancel-dialog').onclick = () => $('#entry-dialog').close();
$('#close-info').onclick = () => $('#info-dialog').close();
$('#help-button').onclick = $('#profile-button').onclick = showInfo;

$('#export-data').onclick = async () => {
  try {
    const data = await api('/export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:'application/json'}));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = `ritm-${todayKey()}.json`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message); }
};
document.addEventListener('keydown', event => {
  if (event.key.toLowerCase() === 'n' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.closest('input,textarea,select') && !document.querySelector('dialog[open]')) {
    event.preventDefault();
    openEntry();
  }
});

const tourSteps = [
  {
    title: 'Четыре раздела ежедневника',
    description: '«Мой день» показывает расписание на выбранную дату. Календарь, список задач и заметки открываются через меню.',
    detail: 'Новый профиль всегда пустой. Записи появляются только после добавления.',
    view: 'today',
    preview: '<div class="tour-preview-label">Разделы</div><div class="tour-sections"><span><b>01</b>Мой день</span><span><b>02</b>Календарь</span><span><b>03</b>Задачи</span><span><b>04</b>Заметки</span></div>'
  },
  {
    title: 'Задачи и события',
    description: 'Нажми «Добавить». Для задачи достаточно названия и даты. Время задаётся полями «С» и «До» — например, с 15:00 до 17:00. У задачи оно необязательно, у события нужны обе границы. Важность и повторение выбираются в форме. Категория, описание и напоминание — в «Дополнительных настройках». Повторять можно каждый день, будни или конкретные дни недели. Серию можно изменить целиком с выбранной даты, а выполнение отмечается отдельно для каждого дня.',
    detail: 'Нажатие на запись открывает редактирование. Квадрат слева отмечает её выполненной. Пример ниже ничего не создаёт.',
    view: 'tasks',
    preview: '<div class="tour-preview-label">Пример интерфейса · не настоящие записи</div><div class="tour-sample-row"><span class="tour-check"></span><span>Название задачи</span><small>Весь день</small></div><div class="tour-sample-row"><span class="tour-check checked">✓</span><span>Название события</span><small>14:00–15:00</small></div>'
  },
  {
    title: 'Выбирай дату в календаре',
    description: 'Нажми на дату, чтобы посмотреть день. Стрелки переключают месяцы, а кнопка «Сегодня» возвращает к текущей дате. Под расписанием раскрой «Загрузка дня и недели»: график показывает занятые часы, пересечения и загрузку недели. Записи без полного интервала учитываются отдельно.',
    detail: 'На компьютере нажатие на свободное место ячейки сразу открывает создание события. На телефоне можно выбрать день и нажать «Добавить».',
    view: 'calendar',
    preview: '<div class="tour-preview-label">Пример недели</div><div class="tour-week"><span><small>ПН</small>12</span><span><small>ВТ</small>13</span><span><small>СР</small>14</span><span class="selected"><small>ЧТ</small>15</span><span><small>ПТ</small>16</span><span><small>СБ</small>17</span><span><small>ВС</small>18</span></div>'
  },
  {
    title: 'Заметки отдельно от дел',
    description: 'Здесь можно хранить идеи и списки без времени и напоминаний. Нажми на карточку, чтобы дополнить текст или удалить запись.',
    detail: 'Пример — только иллюстрация. Он не появится в твоих заметках.',
    view: 'notes',
    preview: '<div class="tour-preview-label">Пример заметки</div><div class="tour-note"><strong>Заголовок заметки</strong><p>Текст, список или идея.<br>Содержимое можно изменить позже.</p></div>'
  },
  {
    title: 'Напоминания ко времени',
    description: 'При создании записи укажи время и выбери, когда напомнить: в момент начала или заранее.',
    detail: '',
    view: 'today',
    preview: '<div class="tour-preview-label">Настройка напоминания</div><div class="tour-options"><span>В момент начала</span><span class="selected">За 15 минут</span><span>За час</span></div>'
  },
  {
    title: 'Поиск и твои данные',
    description: 'В «Задачи» есть поиск, фильтры по статусу и важности, а также порядок «Сначала важные». Повторения видны за открытый месяц и выбранную неделю. В «Помощи» можно скачать свои записи в JSON и повторить это знакомство.',
    detail: 'Нажми «Начать», чтобы перейти к ежедневнику. Обучение не добавляет записи и не меняет существующие.',
    view: 'tasks',
    preview: '<div class="tour-preview-label">Все задачи</div><div class="tour-search">Поиск по названию и описанию</div><div class="tour-options"><span class="selected">В планах</span><span>Готово</span><span>Все</span></div>'
  }
];
let tourIndex = 0;
let tourBusy = false;
let tourReturnView = 'today';
let tourReturnScroll = 0;

function renderTour() {
  const step = tourSteps[tourIndex];
  state.view = step.view;
  render();
  $('#tour-counter').textContent = `${String(tourIndex + 1).padStart(2, '0')} / 06`;
  $('#tour-title').textContent = step.title;
  $('#tour-description').textContent = step.description;
  $('#tour-detail').textContent = tourIndex === 4
    ? state.session.demo
      ? 'Сейчас напоминания видны только в открытой локальной версии. Для личных сообщений в Telegram нужно подключить бота. Сервер должен работать.'
      : 'Отправь /start боту в личном чате. Напоминания будут приходить туда, пока сервер приложения работает.'
    : step.detail;
  $('#tour-preview').innerHTML = step.preview;
  $('#tour-back').hidden = tourIndex === 0;
  $('#tour-next').textContent = tourIndex === tourSteps.length - 1 ? 'Начать' : 'Дальше';
  $('#tour-dots').innerHTML = tourSteps.map((item, index) => `
    <button data-tour-step="${index}" class="${index === tourIndex ? 'active' : ''}"
      aria-label="Шаг ${index + 1}: ${esc(item.title)}" aria-pressed="${index === tourIndex}"></button>`).join('');
  $('#tour-error').textContent = '';
}

function showOnboarding() {
  if (!state.ready || tourBusy) return;
  if ($('#info-dialog').open) $('#info-dialog').close();
  tourReturnView = state.view;
  tourReturnScroll = window.scrollY;
  tourIndex = 0;
  renderTour();
  if (!$('#onboarding-dialog').open) $('#onboarding-dialog').showModal();
  syncTelegramBack();
}

async function finishOnboarding() {
  if (tourBusy) return;
  tourBusy = true;
  for (const button of $$('#onboarding-dialog button')) button.disabled = true;
  try {
    await api('/onboarding', 'POST', {seen: true});
    state.session.user.onboarding_seen = 1;
    $('#onboarding-dialog').close();
    state.view = tourReturnView;
    render();
    window.scrollTo({top: tourReturnScroll, behavior: 'auto'});
  } catch (error) {
    $('#tour-error').textContent = `${error.message} Не удалось сохранить просмотр. Попробуй ещё раз.`;
  } finally {
    tourBusy = false;
    for (const button of $$('#onboarding-dialog button')) button.disabled = false;
  }
}

$('#skip-tour').onclick = finishOnboarding;
$('#tour-next').onclick = () => {
  if (tourIndex === tourSteps.length - 1) finishOnboarding();
  else { tourIndex += 1; renderTour(); }
};
$('#tour-back').onclick = () => { if (tourIndex > 0) { tourIndex -= 1; renderTour(); } };
$('#tour-dots').onclick = event => {
  const button = event.target.closest('[data-tour-step]');
  if (button && !tourBusy) { tourIndex = Number(button.dataset.tourStep); renderTour(); }
};
$('#onboarding-dialog').addEventListener('cancel', event => {
  event.preventDefault();
  finishOnboarding();
});
$('#restart-tour').onclick = $('#tour-sidebar').onclick = showOnboarding;

// Telegram manages its own window; a regular browser requires a user click.
function createFullscreenControl({tg, doc, button, desktop, notify}) {
  const native = Boolean(tg?.initData);
  const supported = desktop && (native
    ? Boolean(tg.isVersionAtLeast?.('8.0') && tg.requestFullscreen && tg.exitFullscreen)
    : Boolean(doc.fullscreenEnabled && doc.documentElement.requestFullscreen));
  let manualRequest = false;
  let unavailable = false;
  const active = () => native ? Boolean(tg.isFullscreen) : Boolean(doc.fullscreenElement);
  function sync() {
    button.hidden = !supported || unavailable;
    const label = active() ? 'Выйти из полного экрана' : 'На весь экран';
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-pressed', String(active()));
    button.title = label;
    button.querySelector('.fullscreen-label').textContent = active() ? 'Свернуть' : label;
  }
  function failed(error) {
    if (error?.error === 'ALREADY_FULLSCREEN') { manualRequest = false; sync(); return; }
    if (native && error?.error === 'UNSUPPORTED') unavailable = true;
    if (manualRequest) notify(native
      ? 'Эта версия Telegram не поддерживает полный экран. Попробуй развернуть окно приложения.'
      : 'Браузер не разрешил полный экран. Его можно включить клавишей F11.');
    manualRequest = false;
    sync();
  }
  async function enter(manual = false) {
    if (!supported || unavailable || active()) return;
    manualRequest = manual;
    try {
      if (native) tg.requestFullscreen();
      else await doc.documentElement.requestFullscreen();
    } catch (error) { failed(error); }
  }
  button.onclick = async () => {
    if (!supported || unavailable) return;
    if (!active()) return enter(true);
    manualRequest = true;
    try {
      if (native) tg.exitFullscreen();
      else await doc.exitFullscreen();
    } catch (error) { failed(error); }
  };
  if (supported) {
    if (native) {
      tg.onEvent?.('fullscreenChanged', () => { manualRequest = false; sync(); });
      tg.onEvent?.('fullscreenFailed', failed);
    } else doc.addEventListener('fullscreenchange', sync);
  }
  sync();
  return {enter, sync};
}

function setupFullscreen(tg) {
  const nativeDesktop = Boolean(tg?.initData && ['tdesktop', 'macos'].includes(tg.platform));
  const desktop = nativeDesktop || matchMedia('(min-width: 601px) and (pointer: fine)').matches;
  const control = createFullscreenControl({tg, doc: document,
    button: $('#fullscreen-button'), desktop, notify: toast});
  if (tg?.initData && desktop) {
    // The native desktop window can start narrower than the desktop breakpoint.
    control.enter();
  }
}

async function boot() {
  hydrateIcons();
  $('#add-entry').disabled = true;
  $('#timezone-label').textContent = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const tg = window.Telegram?.WebApp;
  if (tg?.initData) {
    tg.ready();
    tg.expand();
    tg.setHeaderColor?.('#111a16');
    tg.setBackgroundColor?.('#111a16');
    if (tg.isVersionAtLeast?.('7.10')) tg.setBottomBarColor?.('#17211c');
    tg.BackButton?.onClick(() => {
      if ($('#onboarding-dialog').open) finishOnboarding();
      else if ($('#entry-dialog').open) $('#entry-dialog').close();
      else if ($('#info-dialog').open) $('#info-dialog').close();
      else switchView('today');
      syncTelegramBack();
    });
  }
  setupFullscreen(tg);
  try {
    state.session = await api('/session', 'POST', {
      init_data: tg?.initData || ''
    });
    state.ready = true;
    const s = state.session;
    $('#profile-name').textContent = s.user.name;
    $('#avatar').textContent = s.user.name[0];
    $('#profile-mode').textContent = s.demo ? 'Личный ежедневник' : 'Telegram';
    $('#connection-label').textContent = s.demo ? 'На устройстве' : 'Telegram';
    $('#demo-banner').hidden = !s.demo;
    $('#reminder-hint').textContent = s.demo ? 'В локальном профиле напоминания видны внутри приложения, пока страница открыта. Для Telegram понадобится подключить бота.' : 'Напоминания придут в личный чат. Сначала отправь /start боту. Сервер должен оставаться запущенным.';
    await refresh();
    $('#add-entry').disabled = false;
    localReminders();
    setInterval(localReminders, 15000);
    setInterval(() => { if (!document.hidden && !document.querySelector('dialog[open]')) refresh().catch(() => {}); }, 60000);
    if (!s.user.onboarding_seen) showOnboarding();
  } catch (e) {
    $('#content').innerHTML = empty('Не удалось открыть ежедневник', 'Проверь, что Python-сервер запущен.');
    $('#error-banner').textContent = e.message;
    $('#error-banner').hidden = false;
  }
}
boot();
