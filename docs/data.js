// Data is kept in this tab's memory. This module makes GET requests to GitHub only.
export const SCHEMA_VERSION = 1;
const API = 'https://api.github.com';
const MAX_FILE_BYTES = 8 * 1024 * 1024;

export class JournalError extends Error {
  constructor(message, { status = 0, revoked = false } = {}) {
    super(message);
    this.name = 'JournalError';
    this.status = status;
    this.revoked = revoked;
  }
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  }
  return value;
}

export function eventSignature(event) {
  const copy = { ...event, source: { ...event.source } };
  delete copy.source.device_id;
  return JSON.stringify(canonical(copy));
}

export function resolveEvents(input) {
  const groups = new Map();
  for (const event of input) {
    if (!event || typeof event.id !== 'string') continue;
    if (!groups.has(event.id)) groups.set(event.id, new Map());
    const variants = groups.get(event.id);
    const signature = eventSignature(event);
    if (!variants.has(signature)) variants.set(signature, { ...event, devices: [], conflict: false });
    const variant = variants.get(signature);
    if (event.source?.device_id && !variant.devices.includes(event.source.device_id)) {
      variant.devices.push(event.source.device_id);
    }
  }
  const all = [...groups.values()].flatMap(variants => [...variants.values()]);
  const parents = new Map();
  const hidden = new Set();
  for (const event of all) {
    const refs = Array.isArray(event.supersedes) ? event.supersedes.filter(id => typeof id === 'string') : [];
    if (!parents.has(event.id)) parents.set(event.id, new Set());
    for (const id of refs) {
      parents.get(event.id).add(id);
      hidden.add(id);
    }
  }
  const ancestors = id => {
    const seen = new Set();
    const queue = [id];
    while (queue.length) {
      const current = queue.pop();
      if (seen.has(current)) continue;
      seen.add(current);
      for (const parent of parents.get(current) || []) queue.push(parent);
    }
    return seen;
  };
  const leaves = all.filter(event => event.kind !== 'tombstone' && !hidden.has(event.id));
  const summarizedTurns = new Set(leaves.filter(event => ['worklog', 'checkpoint', 'revision'].includes(event.kind) && event.source?.turn_id).map(event => `${event.project_id}\0${event.source.turn_id}`));
  const visible = leaves.filter(event => event.kind !== 'activity' || !event.source?.turn_id || !summarizedTurns.has(`${event.project_id}\0${event.source.turn_id}`));
  const byAncestor = new Map();
  for (const event of visible) {
    event.conflict = groups.get(event.id).size > 1;
    event.displayKind = event.kind;
    for (const ancestor of ancestors(event.id)) {
      if (!byAncestor.has(ancestor)) byAncestor.set(ancestor, new Set());
      byAncestor.get(ancestor).add(event.id);
      if (event.kind === 'revision') {
        const original = [...(groups.get(ancestor)?.values() || [])].find(item => !['revision', 'tombstone'].includes(item.kind));
        if (original) event.displayKind = original.kind;
      }
    }
    if (event.displayKind === 'revision') event.displayKind = event.idea_status ? 'idea' : 'worklog';
    // A statement in a conversation is never evidence of a verified outcome.
    const evidence = Array.isArray(event.evidence) ? event.evidence : [];
    if (!evidence.length || !['local', 'remote'].includes(event.evidence_level)) event.evidence_level = 'unverified';
    if (event.evidence_level === 'remote' && !evidence.some(item => item.kind === 'remote' && item.checked_at && (item.digest || item.url))) event.evidence_level = 'unverified';
    if (event.evidence_level === 'local' && !evidence.some(item => item.kind === 'local' && item.checked_at && (item.digest || item.url))) event.evidence_level = 'unverified';
  }
  for (const event of visible) {
    if ([...ancestors(event.id)].some(id => byAncestor.get(id)?.size > 1)) event.conflict = true;
  }
  return visible.sort((a, b) => String(b.occurred_at).localeCompare(String(a.occurred_at)) || a.id.localeCompare(b.id));
}

export function shanghaiDay(date = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
}

export function shiftDay(day, offset) {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

export function priorityActions(events, limit = 7) {
  const seen = new Set();
  return events.filter(event => event.next_action && !event.conflict).sort((a, b) =>
    (['P0', 'P1', 'P2'].indexOf(a.priority) < 0 ? 2 : ['P0', 'P1', 'P2'].indexOf(a.priority)) -
    (['P0', 'P1', 'P2'].indexOf(b.priority) < 0 ? 2 : ['P0', 'P1', 'P2'].indexOf(b.priority)) ||
    String(b.occurred_at).localeCompare(String(a.occurred_at))
  ).filter(event => {
    const key = `${event.project_id}\0${event.next_action}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, limit);
}

function assertSchema(value, label) {
  if (!value || value.schema_version !== SCHEMA_VERSION) throw new JournalError(`${label} 的格式版本不受支持，请更新本地记录器或网页。`);
}

function assertEvent(event) {
  assertSchema(event, '工作记录');
  const fields = ['schema_version', 'id', 'kind', 'project_id', 'occurred_at', 'day', 'title', 'summary', 'result', 'next_action', 'priority', 'evidence_level', 'source', 'evidence', 'idea_status', 'hypothesis', 'validation', 'supersedes'];
  const kinds = ['activity', 'worklog', 'idea', 'checkpoint', 'revision', 'tombstone'];
  if (Object.keys(event).some(key => !fields.includes(key)) || !kinds.includes(event.kind) ||
      !['id', 'project_id', 'occurred_at', 'day', 'title', 'summary', 'result', 'next_action'].every(key => typeof event[key] === 'string') ||
      !event.id || !/^\d{4}-\d{2}-\d{2}$/.test(event.day) || Number.isNaN(Date.parse(event.occurred_at)) ||
      !event.source || typeof event.source.device_id !== 'string' || !['manual', 'codex', 'git'].includes(event.source.kind) ||
      !Array.isArray(event.evidence) || event.evidence.some(item => !item || typeof item.label !== 'string' || typeof item.device_id !== 'string' || !['local', 'remote', 'reference'].includes(item.kind)) ||
      (event.supersedes !== undefined && (!Array.isArray(event.supersedes) || event.supersedes.some(id => typeof id !== 'string')))) {
    throw new JournalError('工作记录格式不完整或含未知字段，请更新记录器后重新同步。');
  }
}

function decodeContent(payload) {
  if (payload?.encoding !== 'base64' || typeof payload.content !== 'string') throw new JournalError('GitHub 文件格式不受支持。');
  if (payload.size > MAX_FILE_BYTES || payload.content.length > MAX_FILE_BYTES * 1.5) throw new JournalError('单个文件过大，请在本地检查记录索引。');
  try {
    const bytes = Uint8Array.from(atob(payload.content.replace(/\s/g, '')), char => char.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch { throw new JournalError('无法读取文件编码。'); }
}

export class GitHubJournal {
  #token = null;
  #generation = 0;
  #controllers = new Set();
  #fetch;
  #sleep;
  constructor({ fetchImpl = globalThis.fetch, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) } = {}) {
    this.#fetch = fetchImpl;
    this.#sleep = sleep;
    this.owner = '';
    this.repo = '';
    this.identity = null;
  }
  disconnect() {
    this.#generation += 1;
    this.#token = null;
    this.identity = null;
    for (const controller of this.#controllers) controller.abort();
    this.#controllers.clear();
  }
  async #request(path) {
    if (!this.#token) throw new JournalError('请先解锁工作记录。', { revoked: true });
    const generation = this.#token && this.#generation;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (!this.#token || generation !== this.#generation) throw new JournalError('当前连接已关闭。', { revoked: true });
      const controller = new AbortController();
      this.#controllers.add(controller);
      const timeout = setTimeout(() => controller.abort(), 15000);
      let response;
      try {
        response = await this.#fetch(`${API}${path}`, {
          method: 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer',
          headers: { Authorization: `Bearer ${this.#token}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
          signal: controller.signal,
        });
      } catch {
        if (!this.#token || generation !== this.#generation) throw new JournalError('当前连接已关闭。', { revoked: true });
        if (attempt < 2) { await this.#sleep(500 * (attempt + 1)); continue; }
        throw new JournalError('GitHub 暂时无法连接，保留当前视图；请检查网络后刷新。');
      } finally {
        clearTimeout(timeout);
        this.#controllers.delete(controller);
      }
      if (!this.#token || generation !== this.#generation) throw new JournalError('当前连接已关闭。', { revoked: true });
      if ([401, 403].includes(response.status)) {
        this.disconnect();
        throw new JournalError('GitHub 拒绝访问，已清除当前记录。请检查 Token、仓库权限或 API 额度。', { status: response.status, revoked: true });
      }
      if (response.status === 429) throw new JournalError('GitHub 请求次数已达限制，保留当前视图；请稍后手动刷新。', { status: 429 });
      if (response.status >= 500 && attempt < 2) { await this.#sleep(500 * (attempt + 1)); continue; }
      if (!response.ok) throw new JournalError(response.status === 404 ? '找不到仓库、分支或记录文件，请检查设置与同步状态。' : `GitHub 返回错误（${response.status}），请稍后重试。`, { status: response.status });
      let payload;
      try { payload = await response.json(); }
      catch { throw new JournalError('GitHub 返回的数据无法解析，请稍后重试。'); }
      if (!this.#token || generation !== this.#generation) throw new JournalError('当前连接已关闭。', { revoked: true });
      return payload;
    }
  }
  async connect({ token, owner, repo, ownerId }) {
    this.disconnect();
    if (!/^github_pat_[A-Za-z0-9_]+$/.test(token)) throw new JournalError('请使用以 github_pat_ 开头、仅授权此仓库的 fine-grained Token。');
    if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(owner) || !/^[A-Za-z0-9_.-]{1,100}$/.test(repo) || !/^\d+$/.test(String(ownerId))) throw new JournalError('请填写有效的 GitHub 用户、仓库名与账号数值 ID。');
    this.owner = owner;
    this.repo = repo;
    this.#token = token;
    try {
      const user = await this.#request('/user');
      if (String(user.id) !== String(ownerId) || String(user.login).toLowerCase() !== owner.toLowerCase()) throw new JournalError('Token 不属于指定的个人 GitHub 账号。');
      const repository = await this.#request(this.#repoPath());
      if (!repository.private || String(repository.owner?.id) !== String(ownerId) || repository.permissions?.pull !== true) throw new JournalError('仅支持由指定账号拥有、且当前 Token 可读取的私有仓库。');
      this.identity = { login: user.login, id: user.id, branch: repository.default_branch };
      return await this.load();
    } catch (error) { this.disconnect(); throw error; }
  }
  #repoPath() { return `/repos/${encodeURIComponent(this.owner)}/${encodeURIComponent(this.repo)}`; }
  async #read(path, sha) {
    const encoded = path.split('/').map(encodeURIComponent).join('/');
    let payload = await this.#request(`${this.#repoPath()}/contents/${encoded}?ref=${encodeURIComponent(sha)}`);
    // GitHub omits Contents payloads above 1 MB; the immutable blob API retains them.
    if (payload.encoding === 'none' && /^[a-f0-9]{40}$/.test(payload.sha)) {
      if (payload.size > MAX_FILE_BYTES) throw new JournalError('单个文件过大，请在本地检查记录索引。');
      payload = await this.#request(`${this.#repoPath()}/git/blobs/${payload.sha}`);
    }
    return decodeContent(payload);
  }
  async #json(path, sha) {
    try { return JSON.parse(await this.#read(path, sha)); }
    catch (error) { if (error instanceof JournalError) throw error; throw new JournalError(`${path} 不是有效的 JSON。`); }
  }
  async load() {
    if (!this.identity) throw new JournalError('请重新解锁。', { revoked: true });
    const commit = await this.#request(`${this.#repoPath()}/commits/${encodeURIComponent(this.identity.branch)}`);
    const sha = commit.sha;
    if (!/^[a-f0-9]{40}$/.test(sha)) throw new JournalError('无法确认仓库版本。');
    const tree = await this.#request(`${this.#repoPath()}/git/trees/${sha}?recursive=1`);
    if (tree.truncated || !Array.isArray(tree.tree)) throw new JournalError('仓库目录过大或不完整，请在本地整理后重试。');
    const paths = tree.tree.filter(entry => entry.type === 'blob').map(entry => entry.path);
    const catalog = await this.#json('projects.json', sha);
    assertSchema(catalog, '项目目录');
    if (!Array.isArray(catalog.projects) || catalog.projects.some(project => !project || typeof project.id !== 'string' || typeof project.name !== 'string')) throw new JournalError('项目目录缺少有效的项目列表。');
    const snapshots = [];
    for (const path of paths.filter(path => /^devices\/[^/]+\/snapshot\.json$/.test(path)).sort()) {
      const snapshot = await this.#json(path, sha);
      assertSchema(snapshot, '设备索引');
      if (typeof snapshot.device?.id !== 'string' || typeof snapshot.generated_at !== 'string' || !Array.isArray(snapshot.events) || !Array.isArray(snapshot.days) || snapshot.days.some(day => typeof day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day))) throw new JournalError('设备索引不完整，请重新同步。');
      for (const event of snapshot.events) assertEvent(event);
      snapshots.push(snapshot);
    }
    return { sha, projects: catalog.projects, snapshots, paths, events: snapshots.flatMap(snapshot => snapshot.events), fetchedAt: new Date().toISOString(), demo: false };
  }
  async loadDay(model, day) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new JournalError('日期格式不正确。');
    const events = [];
    const suffix = `/days/${day}.jsonl`;
    for (const path of model.paths.filter(path => /^devices\/[^/]+\/days\//.test(path) && path.endsWith(suffix))) {
      const content = await this.#read(path, model.sha);
      for (const line of content.split('\n').filter(line => line.trim())) {
        let event;
        try { event = JSON.parse(line); } catch { throw new JournalError('历史文件存在不完整记录，请在本地检查同步。'); }
        assertEvent(event);
        events.push(event);
      }
    }
    return events;
  }
}
