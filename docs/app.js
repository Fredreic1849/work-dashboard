import { GitHubJournal, resolveEvents, shanghaiDay, shiftDay, priorityActions } from './data.js';

const $ = id => document.getElementById(id);
const client = new GitHubJournal();
let model = null;
let epoch = 0;
let busy = false;
let view = 'today';
let ideaStatus = '';
let historyStart = shiftDay(shanghaiDay(), -6);
let historyEnd = shanghaiDay();
let loadedDays = new Set();
let lastCheck = 0;
const views = {
  today: ['今天', 'DAILY WORKSPACE', '今天，做了些什么？', '把进展、想法和下一步，放在一起。'],
  projects: ['项目', 'PROJECT OVERVIEW', '每个项目，都有下一步。', '看清目标、最近进展，以及从哪里接着做。'],
  ideas: ['Ideas', 'A PLACE FOR IDEAS', '先把好想法留下来。', '记下为什么值得试，再给它一个最小验证。'],
  history: ['历史', 'LOOK BACK & CONNECT', '回头看，走过的路。', '按日期回顾工作，串起每一次小小的推进。'],
  sync: ['同步情况', 'ACROSS YOUR DEVICES', '不同电脑，同一份脉络。', '了解每台设备已上传的记录，以及尚未解决的异常。'],
};
const kindLabels = { activity: '摘要待补', worklog: '工作记录', idea: 'Idea', checkpoint: '阶段记录', revision: '修订' };
const evidenceLabels = { unverified: '待核验', local: '本地核验', remote: '远端核验' };
const statusLabels = { proposed: '待探索', testing: '验证中', adopted: '已采用', parked: '暂存' };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function append(parent, ...children) { parent.append(...children.filter(Boolean)); return parent; }
function option(value, label) { const node = el('option', '', label); node.value = value; return node; }
function setBusy(value) { busy = value; $('refresh').disabled = value; $('unlock').disabled = value; $('demo-button').disabled = value; }
function projectName(id) { return model?.projects.find(project => project.id === id)?.name || id || '未归类项目'; }
function deviceName(id) { return model?.snapshots.find(snapshot => snapshot.device.id === id)?.device.name || id || '未知设备'; }
function formatTime(value, includeDay = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未记录';
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', ...(includeDay ? { month: '2-digit', day: '2-digit' } : {}), hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}
function events() { return resolveEvents(model?.events || []); }
function filtered(input) {
  const project = $('project-filter').value;
  const device = $('device-filter').value;
  const query = $('search').value.trim().toLowerCase();
  return input.filter(event => (!project || event.project_id === project) && (!device || event.devices.includes(device)) && (!query || [projectName(event.project_id), event.title, event.summary, event.result, event.next_action, event.hypothesis, event.validation].join(' ').toLowerCase().includes(query)));
}
function showMessage(message) { $('global-message').textContent = message; $('global-message').hidden = !message; }
function empty(title, description) { return append(el('div', 'empty'), el('span', 'empty-symbol', '—'), el('strong', '', title), el('span', '', description)); }
function panel(title, subtitle, content) { return append(el('section', 'panel'), append(el('div', 'panel-header'), el('h2', '', title), subtitle ? el('small', '', subtitle) : null), content); }
function tag(text, className = '') { return el('span', `tag ${className}`, text); }
function sourceLabel(event) {
  const kind = { manual: '手动记录', codex: 'Codex 任务', git: 'Git 活动' }[event.source?.kind] || '工作记录';
  const devices = event.devices.map(deviceName).join('、');
  return `${devices ? `${devices} · ` : ''}${kind}`;
}
function evidenceList(event) {
  if (!event.evidence?.length) return null;
  const details = append(el('details', 'event-evidence'), el('summary', '', `查看依据 · ${event.evidence.length} 项`));
  for (const item of event.evidence) {
    const row = el('div', 'evidence-item');
    row.append(el('div', '', item.label || '依据索引'));
    row.append(el('div', '', `${item.kind === 'local' ? '证据位于 ' : '记录于 '}${deviceName(item.device_id)}${item.checked_at ? ` · ${formatTime(item.checked_at, true)} 核验` : ' · 尚无核验时间'}`));
    if (item.url) {
      try {
        const url = new URL(item.url);
        if (url.protocol === 'https:' && !url.username && !url.password) {
          const link = el('a', '', '打开参考链接 ↗');
          link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.referrerPolicy = 'no-referrer'; row.append(link);
        }
      } catch { /* Invalid links remain plain evidence labels. */ }
    }
    if (item.id) row.append(el('div', '', `索引 ${item.id}`));
    details.append(row);
  }
  return details;
}
function eventCard(event) {
  const card = el('article', 'event-card');
  const meta = append(el('div', 'event-meta'), tag(projectName(event.project_id)), tag(kindLabels[event.displayKind] || '记录', event.displayKind === 'idea' ? 'idea' : ''), tag(evidenceLabels[event.evidence_level] || '待核验', event.evidence_level));
  if (event.conflict) meta.append(tag('存在并发修订', 'conflict'));
  if (event.displayKind === 'idea') meta.append(tag(statusLabels[event.idea_status] || event.idea_status || '待探索', 'idea'));
  meta.append(el('span', 'event-time', formatTime(event.occurred_at)));
  append(card, meta, el('h3', '', event.title || '未命名记录'));
  if (event.summary) card.append(el('p', 'event-text', event.summary));
  if (event.displayKind === 'idea') {
    for (const [label, text] of [['为什么值得试', event.hypothesis], ['最小验证', event.validation]]) {
      if (text) card.append(append(el('div', 'idea-section'), el('strong', '', label), el('p', '', text)));
    }
  }
  if (event.result) card.append(append(el('div', 'event-result'), el('strong', '', '产出 / 结果'), el('p', 'event-text', event.result)));
  if (event.next_action) card.append(append(el('div', 'event-result'), el('strong', '', '下一步'), el('p', 'event-text', event.next_action)));
  append(card, evidenceList(event), el('div', 'event-source', sourceLabel(event)));
  if (event.source?.thread_id || event.source?.commit) {
    card.append(el('div', 'event-source', event.source.thread_id ? `任务 ${event.source.thread_id}${event.source.turn_id ? ` · 轮次 ${event.source.turn_id}` : ''}` : `提交 ${event.source.commit}`));
  }
  if (event.conflict) card.append(el('p', 'event-source', '同一条记录有多个版本；保留各版本，请在本地确认后修订。'));
  return card;
}
function timeline(items) { const node = el('div', 'timeline'); for (const event of items) node.append(eventCard(event)); return node; }
function metric(label, number, note, icon) {
  return append(el('div', 'metric'), append(el('div', 'metric-top'), el('span', '', label), el('span', 'metric-icon', icon)), el('div', 'metric-number', number), el('div', 'metric-note', note));
}
function nextActions(items) {
  const list = el('ol', 'action-list');
  for (const event of priorityActions(items)) list.append(append(el('li', 'action'), tag(event.priority || 'P2', `priority ${event.priority || 'P2'}`), append(el('div'), el('p', 'action-title', event.next_action), el('span', 'action-project', `${projectName(event.project_id)} · ${event.day}`))));
  const section = panel('接下来，往前一步', '最多 7 项', list.children.length ? list : empty('下一步还没有记录', '在工作小结里，留下一件可以继续的事。'));
  section.append(el('div', 'action-hint', '来自最近七天的小结，按 P0 / P1 / P2 排序。以项目最新进展确认是否仍需执行。'));
  return section;
}
function renderToday(container) {
  const today = shanghaiDay();
  const all = filtered(events());
  const daily = all.filter(event => event.day === today);
  const completed = daily.filter(event => event.displayKind !== 'activity');
  const metrics = append(el('div', 'metrics'), metric('今日工作记录', completed.length, '有内容的进展与想法', '≡'), metric('推进中的项目', new Set(daily.map(event => event.project_id)).size, '今天有活动的项目', '▦'), metric('新想法', daily.filter(event => event.displayKind === 'idea').length, '值得继续探索的可能', '◇'), metric('摘要待补', daily.filter(event => event.displayKind === 'activity').length, '已发现活动，等待小结', '◌'));
  container.append(metrics);
  const main = panel('今天的工作脉络', `${today} · ${daily.length} 条`, daily.length ? timeline(daily) : empty('今天的故事，还等你记录', '在 Codex 里记录一次进展，或留下一个新想法。'));
  const recent = all.filter(event => event.day >= shiftDay(today, -6) && event.day <= today);
  const side = append(el('div', 'side-panels'), nextActions(recent));
  const latest = [...new Set(all.map(event => event.project_id))].slice(0, 4).map(id => all.find(event => event.project_id === id));
  const projectList = el('div');
  for (const event of latest) {
    const button = append(el('button', 'project-mini'), append(el('div', 'project-mini-top'), el('strong', '', projectName(event.project_id)), el('small', '', event.day)), el('p', '', event.title));
    button.type = 'button'; button.addEventListener('click', () => { $('project-filter').value = event.project_id; setView('projects'); }); projectList.append(button);
  }
  side.append(panel('项目近况', '最近有记录', latest.length ? projectList : empty('暂无项目近况', '项目记录同步后，会显示在这里。')));
  side.append(append(el('div', 'note-panel'), el('p', 'eyebrow', 'A SMALL NOTE'), el('p', '', '一次清楚的小结，是给下一次工作的自己留的一条路。')));
  container.append(append(el('div', 'two-column'), main, side));
}
function renderProjects(container) {
  const all = filtered(events());
  const selected = $('project-filter').value;
  const search = $('search').value.trim().toLowerCase();
  const projects = model.projects.filter(project => (!selected || project.id === selected) && (!search || [project.name, project.goal].join(' ').toLowerCase().includes(search) || all.some(event => event.project_id === project.id)) && (!$('device-filter').value || all.some(event => event.project_id === project.id)));
  container.append(el('p', 'section-subtitle', '阶段与里程碑来自保存的阶段记录。核验状态以记录依据为准；没有实验结果时保留待测。'));
  if (!projects.length) { container.append(empty('没有匹配的项目', '调整筛选，或先在主电脑登记项目。')); return; }
  const grid = el('div', 'project-grid');
  for (const project of projects) {
    const projectEvents = all.filter(event => event.project_id === project.id);
    const latest = projectEvents.find(event => event.displayKind !== 'activity' && !event.conflict);
    const checkpoint = projectEvents.find(event => event.displayKind === 'checkpoint' && !event.conflict);
    const card = append(el('article', 'project-card'), el('h2', '', project.name), el('p', 'goal', project.goal || '目标尚未记录'));
    const facts = el('dl', 'project-facts');
    for (const [label, value] of [['阶段记录', checkpoint?.title || '尚无已保存的阶段记录'], ['最近进展', latest?.summary || latest?.title || '等待第一次工作小结'], ['产出结果', latest?.result || '—（待记录）'], ['下一步', latest?.next_action || '尚未记录下一步']]) append(facts, el('dt', '', label), el('dd', '', value));
    append(card, facts, append(el('div', 'project-card-bottom'), el('span', '', latest ? `更新于 ${latest.day}` : '尚无工作记录'), el('span', '', `${projectEvents.length} 条已加载记录`)));
    if (projectEvents.some(event => event.conflict)) card.append(el('p', 'event-source', '此项目有并发修订待确认；上方暂不采用冲突版本。'));
    grid.append(card);
  }
  container.append(grid);
}
function renderIdeas(container) {
  const ideas = filtered(events()).filter(event => event.displayKind === 'idea');
  const statuses = [...new Set(ideas.map(event => event.idea_status || 'proposed'))];
  const filters = el('div', 'status-list');
  for (const status of ['', ...statuses]) {
    const button = el('button', `status-filter${status === ideaStatus ? ' active' : ''}`, status ? (statusLabels[status] || status) : `全部想法 · ${ideas.length}`);
    button.type = 'button'; button.setAttribute('aria-pressed', String(status === ideaStatus)); button.addEventListener('click', () => { ideaStatus = status; render(); }); filters.append(button);
  }
  container.append(filters);
  const shown = ideas.filter(event => !ideaStatus || (event.idea_status || 'proposed') === ideaStatus);
  if (!shown.length) { container.append(empty('给下一个想法留个位置', '在 Codex 里说“把这个想法记下来”，或运行 worklog idea。')); return; }
  const grid = el('div', 'idea-grid'); for (const event of shown) grid.append(eventCard(event)); container.append(grid);
}
async function fetchHistory(start = historyStart, end = historyEnd) {
  if (busy || !model) return;
  if (!start || !end || start > end || (Date.parse(end) - Date.parse(start)) / 86400000 > 30) { showMessage('请选择不超过 31 天的日期范围，再加载历史。'); return; }
  const generation = epoch;
  historyStart = start; historyEnd = end;
  if (model.demo) { showMessage(''); render(); return; }
  setBusy(true); showMessage('正在按日期读取历史记录…');
  try {
    const collected = [];
    const newlyLoaded = [];
    for (let day = start; day <= end; day = shiftDay(day, 1)) {
      if (!loadedDays.has(day)) { collected.push(...await client.loadDay(model, day)); newlyLoaded.push(day); }
      if (generation !== epoch) return;
    }
    model.events.push(...collected);
    for (const day of newlyLoaded) loadedDays.add(day);
    showMessage(''); render();
  } catch (error) { if (generation === epoch) handleError(error); }
  finally { if (generation === epoch) setBusy(false); }
}
function renderHistory(container) {
  const controls = el('div', 'history-controls');
  const start = el('input'); start.type = 'date'; start.value = historyStart; start.setAttribute('aria-label', '开始日期');
  const end = el('input'); end.type = 'date'; end.value = historyEnd; end.setAttribute('aria-label', '结束日期');
  const load = el('button', 'button secondary small', '加载这段记录'); load.type = 'button'; load.addEventListener('click', () => fetchHistory(start.value, end.value));
  append(controls, start, el('span', '', '至'), end, load); container.append(controls);
  const all = filtered(events());
  const today = shanghaiDay();
  const lastWeek = all.filter(event => event.day >= shiftDay(today, -6) && event.day <= today);
  const weekProjects = [...new Set(lastWeek.map(event => event.project_id))];
  const summary = [`最近七天 · ${shiftDay(today, -6)} — ${today}`, `共 ${lastWeek.filter(event => event.displayKind !== 'activity').length} 条工作与想法记录，涉及 ${weekProjects.length} 个项目，${lastWeek.filter(event => event.displayKind === 'activity').length} 条活动等待小结。`];
  if (weekProjects.length) summary.push(`本周有记录：${weekProjects.map(projectName).join('、')}。`);
  container.append(append(el('section', 'week-recap'), el('h2', '', '这一周，留下了什么'), el('p', '', summary.join('\n'))));
  const heatmap = el('div', 'heatmap');
  const knownDays = new Set(model.snapshots.flatMap(snapshot => snapshot.days));
  for (let index = -41; index <= 0; index += 1) {
    const day = shiftDay(today, index); const count = all.filter(event => event.day === day).length;
    const cell = el('button', 'heat-cell'); cell.type = 'button'; cell.dataset.level = count > 3 ? '3' : count > 1 ? '2' : count || knownDays.has(day) ? '1' : '0';
    cell.title = `${day} · ${count ? `${count} 条已加载记录` : knownDays.has(day) ? '有存档，点击加载' : '暂无记录'}`; cell.setAttribute('aria-label', cell.title);
    cell.addEventListener('click', () => fetchHistory(day, day)); heatmap.append(cell);
  }
  container.append(append(el('section', 'heatmap-panel'), append(el('div', 'heatmap-heading'), el('span', '', '近六周 · 点击日期查看'), el('span', '', '记录的多少，不代表工作的价值')), heatmap));
  const shown = all.filter(event => event.day >= historyStart && event.day <= historyEnd);
  if (!model.demo && [...knownDays].some(day => day >= historyStart && day <= historyEnd && !loadedDays.has(day))) container.append(el('p', 'section-subtitle', '当前展示设备索引中的记录。点击“加载这段记录”读取这个范围的完整日报。'));
  if (!shown.length) { container.append(empty('这个时间段还没有已加载的记录', '调整日期，或点击加载以读取存档。')); return; }
  for (const day of [...new Set(shown.map(event => event.day))].sort().reverse()) {
    const daily = shown.filter(event => event.day === day);
    container.append(append(el('section', 'history-day'), el('h2', '', `${day} · ${daily.length} 条记录`), timeline(daily)));
  }
}
function renderSync(container) {
  const snapshots = model.snapshots.filter(snapshot => !$('device-filter').value || snapshot.device.id === $('device-filter').value);
  const list = el('div', 'device-list');
  for (const snapshot of snapshots) {
    const age = Date.now() - Date.parse(snapshot.generated_at);
    const stale = age > 86400000;
    const card = el('article', 'device-card');
    const issues = Array.isArray(snapshot.issues) ? snapshot.issues : [];
    append(card, append(el('div', 'device-heading'), el('h2', '', snapshot.device.name || snapshot.device.id), tag(issues.length ? `${issues.length} 项采集异常` : stale ? '较久未上传' : '近期已上传', issues.length || stale ? 'unverified' : 'remote')));
    const facts = el('dl', 'device-grid');
    for (const [label, value] of [['最近上传索引', formatTime(snapshot.generated_at, true)], ['数据覆盖', snapshot.coverage_start ? `${snapshot.coverage_start} 至 ${[...snapshot.days].sort().at(-1) || '—'}` : '暂无已上传记录'], ['存档天数', `${snapshot.days.length} 天`], ['索引内记录', `${snapshot.events.length} 条（汇总后去重）`], ['设备标识', snapshot.device.id], ['确认版本', model.demo ? '虚构演示' : model.sha.slice(0, 10)]]) append(facts, append(el('div'), el('dt', '', label), el('dd', '', value)));
    card.append(facts);
    if (issues.length) { const ul = el('ul', 'device-issues'); for (const issue of issues) ul.append(el('li', '', `${issue.code} · ${issue.message}`)); card.append(ul); }
    list.append(card);
  }
  container.append(snapshots.length ? list : empty('还没有设备上传记录', '在一台 Mac 上安装记录器并完成第一次同步。'));
  container.append(el('p', 'sync-explanation', '这里展示 GitHub 上已经收到的设备索引。离线电脑的队列长度与当前状态，需在该电脑运行 worklog status 查看。较久未上传也可能只是电脑没有新工作。网页只同步记录；证据文件和运行环境仍在原设备。'));
  const button = el('button', 'button secondary small', '锁定并退出'); button.type = 'button'; button.addEventListener('click', () => lock()); container.append(button);
}
function render() {
  if (!model) return;
  const [label, eyebrow, title, description] = views[view];
  $('breadcrumb-view').textContent = label; $('page-eyebrow').textContent = eyebrow; $('page-title').textContent = title; $('page-description').textContent = description;
  $('header-weekday').textContent = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', weekday: 'long' }).format(new Date());
  $('header-date').textContent = shanghaiDay().replaceAll('-', ' . ');
  $('filter-bar').hidden = view === 'sync';
  $('data-freshness').textContent = model.demo ? '演示数据 · 不连接 GitHub' : `读取于 ${formatTime(model.fetchedAt)} · 版本 ${model.sha.slice(0, 7)}`;
  const container = $('view-content'); container.replaceChildren();
  ({ today: renderToday, projects: renderProjects, ideas: renderIdeas, history: renderHistory, sync: renderSync })[view](container);
}
function setView(next) {
  view = next;
  for (const button of document.querySelectorAll('[data-view]')) {
    button.classList.toggle('active', button.dataset.view === view);
    if (button.dataset.view === view) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  }
  render();
}
function updateFilters() {
  const project = $('project-filter').value; const device = $('device-filter').value;
  $('project-filter').replaceChildren(option('', '全部项目'), ...model.projects.map(item => option(item.id, item.name)));
  $('device-filter').replaceChildren(option('', '全部设备'), ...model.snapshots.map(item => option(item.device.id, item.device.name || item.device.id)));
  if ([...$('project-filter').options].some(item => item.value === project)) $('project-filter').value = project;
  if ([...$('device-filter').options].some(item => item.value === device)) $('device-filter').value = device;
}
function enter(data) {
  model = data; loadedDays = new Set(); lastCheck = Date.now(); updateFilters();
  $('login-screen').hidden = true; $('dashboard').hidden = false; $('demo-notice').hidden = !model.demo;
  $('workspace-label').textContent = model.demo ? '演示空间 · 虚构数据' : `${client.owner} / ${client.repo}`;
  $('session-badge').textContent = model.demo ? '演示 · 退出' : '私有连接 · 锁定';
  render();
}
function lock(message = '') {
  epoch += 1; client.disconnect(); model = null; loadedDays = new Set(); setBusy(false); view = 'today'; ideaStatus = '';
  $('view-content').replaceChildren(); $('search').value = ''; $('project-filter').replaceChildren(option('', '全部项目')); $('device-filter').replaceChildren(option('', '全部设备'));
  $('workspace-label').textContent = ''; $('data-freshness').textContent = ''; $('token').value = ''; showMessage('');
  $('dashboard').hidden = true; $('login-screen').hidden = false; $('login-error').textContent = message; $('login-error').hidden = !message;
  for (const button of document.querySelectorAll('[data-view]')) { button.classList.toggle('active', button.dataset.view === 'today'); if (button.dataset.view === 'today') button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); }
}
function handleError(error) { if (error.revoked) lock(error.message); else showMessage(error.message || '暂时无法读取记录，请稍后重试。'); }
async function refresh() {
  if (busy || !model || model.demo) return;
  const generation = epoch; setBusy(true); showMessage('');
  try { const data = await client.load(); if (generation === epoch) enter(data); }
  catch (error) { if (generation === epoch) handleError(error); }
  finally { if (generation === epoch) { setBusy(false); lastCheck = Date.now(); } }
}
$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return;
  const generation = ++epoch; setBusy(true); $('login-error').hidden = true;
  const token = $('token').value.trim(); $('token').value = '';
  try { const data = await client.connect({ token, owner: $('owner').value.trim(), repo: $('repository').value.trim(), ownerId: $('owner-id').value.trim() }); if (generation === epoch) enter(data); }
  catch (error) { if (generation === epoch) { $('login-error').textContent = error.message || '无法解锁，请检查连接设置。'; $('login-error').hidden = false; } }
  finally { if (generation === epoch) setBusy(false); }
});
$('demo-button').addEventListener('click', async () => {
  if (busy) return; const generation = ++epoch; setBusy(true); $('token').value = ''; client.disconnect();
  try {
    const response = await fetch('./demo.json', { cache: 'no-store', credentials: 'omit' }); if (!response.ok) throw new Error('演示数据暂时无法加载。');
    const data = await response.json();
    const offset = Math.round((Date.parse(shanghaiDay()) - Date.parse(data.base_day)) / 86400000);
    for (const snapshot of data.snapshots) {
      snapshot.generated_at = new Date(Date.now() - 600000).toISOString(); snapshot.days = snapshot.days.map(day => shiftDay(day, offset)); snapshot.coverage_start = shiftDay(snapshot.coverage_start, offset);
      for (const event of snapshot.events) {
        event.day = shiftDay(event.day, offset); event.occurred_at = new Date(Date.parse(event.occurred_at) + offset * 86400000).toISOString();
        for (const evidence of event.evidence || []) if (evidence.checked_at) evidence.checked_at = new Date(Date.parse(evidence.checked_at) + offset * 86400000).toISOString();
      }
    }
    if (generation === epoch) enter({ ...data, demo: true, paths: [], sha: 'demo', events: data.snapshots.flatMap(snapshot => snapshot.events), fetchedAt: new Date().toISOString() });
  } catch (error) { if (generation === epoch) { $('login-error').textContent = error.message || '无法加载演示。'; $('login-error').hidden = false; } }
  finally { if (generation === epoch) setBusy(false); }
});
for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click', () => setView(button.dataset.view));
for (const id of ['project-filter', 'device-filter']) $(id).addEventListener('change', render);
$('search').addEventListener('input', render);
$('refresh').addEventListener('click', refresh); $('logout').addEventListener('click', () => lock());
$('session-badge').setAttribute('role', 'button'); $('session-badge').tabIndex = 0; $('session-badge').setAttribute('aria-label', '锁定并退出');
$('session-badge').addEventListener('click', () => lock()); $('session-badge').addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); lock(); } });
function maybeRefresh() { if (document.visibilityState === 'visible' && Date.now() - lastCheck >= 300000) refresh(); }
setInterval(maybeRefresh, 300000); document.addEventListener('visibilitychange', maybeRefresh);
window.addEventListener('pagehide', () => lock()); window.addEventListener('pageshow', event => { if (event.persisted) lock(); });
