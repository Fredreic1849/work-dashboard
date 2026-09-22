import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { GitHubJournal, JournalError, eventSignature, resolveEvents, priorityActions, shanghaiDay, shiftDay } from '../docs/data.js';

const event = (fields = {}) => ({ schema_version: 1, id: 'first', kind: 'worklog', project_id: 'test', occurred_at: '2026-09-22T05:00:00Z', day: '2026-09-22', title: '工作', summary: '小结', result: '待测', next_action: '', priority: 'P2', evidence_level: 'unverified', source: { kind: 'codex', thread_id: 'thread', turn_id: 'turn', device_id: 'a' }, evidence: [], ...fields });
const sha = 'a'.repeat(40);
const credentials = { token: 'github_pat_testOnlySynthetic', owner: 'testowner', repo: 'work-journal', ownerId: '123' };
const snapshot = { schema_version: 1, device: { id: 'a', name: '测试设备' }, generated_at: '2026-09-22T05:00:00Z', days: ['2026-09-22'], events: [event()], issues: [], coverage_start: '2026-09-22' };
const response = (body, status = 200) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
const encoded = text => ({ encoding: 'base64', size: Buffer.byteLength(text), content: Buffer.from(text).toString('base64') });

function fixture(overrides = {}) {
  const calls = [];
  const routes = {
    '/user': response({ id: 123, login: 'testowner' }),
    '/repos/testowner/work-journal': response({ private: true, owner: { id: 123 }, permissions: { pull: true }, default_branch: 'main' }),
    '/repos/testowner/work-journal/commits/main': response({ sha }),
    [`/repos/testowner/work-journal/git/trees/${sha}?recursive=1`]: response({ truncated: false, tree: ['projects.json', 'devices/a/snapshot.json', 'devices/a/days/2026-09-22.jsonl'].map(path => ({ type: 'blob', path })) }),
    [`/repos/testowner/work-journal/contents/projects.json?ref=${sha}`]: response(encoded(JSON.stringify({ schema_version: 1, projects: [{ id: 'test', name: '测试项目' }] }))),
    [`/repos/testowner/work-journal/contents/devices/a/snapshot.json?ref=${sha}`]: response(encoded(JSON.stringify(snapshot))),
    [`/repos/testowner/work-journal/contents/devices/a/days/2026-09-22.jsonl?ref=${sha}`]: response(encoded(`${JSON.stringify(event())}\n`)),
    ...overrides,
  };
  const client = new GitHubJournal({ sleep: async () => {}, fetchImpl: async (url, options) => {
    calls.push({ url, options });
    const path = url.replace('https://api.github.com', '');
    const route = routes[path];
    if (!route) throw new Error(`Unexpected fixture route ${path}`);
    return typeof route === 'function' ? route() : route;
  } });
  return { client, calls, routes };
}

test('copies of the same logical turn merge across devices with stable key ordering', () => {
  const first = event(); const second = { ...event(), source: { turn_id: 'turn', thread_id: 'thread', kind: 'codex', device_id: 'b' } };
  assert.equal(eventSignature(first), eventSignature(second));
  const result = resolveEvents([first, second, first]);
  assert.equal(result.length, 1); assert.equal(result[0].conflict, false); assert.deepEqual(result[0].devices, ['a', 'b']);
  assert.equal(first.devices, undefined, 'resolver must not mutate input');
});

test('same ID with different content preserves both conflicting variants', () => {
  const result = resolveEvents([event(), event({ summary: '另一版本' })]);
  assert.equal(result.length, 2); assert.ok(result.every(item => item.conflict));
  assert.notEqual(eventSignature(event()), eventSignature(event({ evidence: [{ device_id: 'b' }] })), 'only source.device_id is ignored');
});

test('a structured summary resolves a metadata-only activity for the same project and turn', () => {
  const activity = event({ id: 'activity', kind: 'activity' });
  assert.equal(resolveEvents([activity, event()]).length, 1);
  assert.equal(resolveEvents([activity, event()])[0].kind, 'worklog');
  assert.equal(resolveEvents([activity, event({ project_id: 'another-project' })]).length, 2);
  assert.equal(resolveEvents([activity, event({ source: { kind: 'codex', device_id: 'a', turn_id: 'another-turn' } })]).length, 2);
});

test('sequential revisions show only latest and retain idea presentation', () => {
  const original = event({ kind: 'idea', idea_status: 'new' });
  const second = event({ id: 'second', kind: 'revision', supersedes: ['first'], idea_status: 'testing' });
  const third = event({ id: 'third', kind: 'revision', supersedes: ['second'], idea_status: 'validated' });
  const result = resolveEvents([original, second, third]);
  assert.equal(result.length, 1); assert.equal(result[0].id, 'third'); assert.equal(result[0].displayKind, 'idea'); assert.equal(result[0].conflict, false);
});

test('concurrent revision leaves are visible and excluded from next actions', () => {
  const result = resolveEvents([event(), event({ id: 'a', kind: 'revision', supersedes: ['first'], next_action: 'A' }), event({ id: 'b', kind: 'revision', supersedes: ['first'], next_action: 'B' })]);
  assert.deepEqual(result.map(item => item.id), ['a', 'b']); assert.ok(result.every(item => item.conflict)); assert.deepEqual(priorityActions(result), []);
  const merged = resolveEvents([event(), ...result.map(({ devices, conflict, displayKind, ...value }) => value), event({ id: 'merged', kind: 'revision', supersedes: ['a', 'b'] })]);
  assert.equal(merged.length, 1); assert.equal(merged[0].conflict, false);
});

test('tombstoned revisions do not resurrect old content and ancestor cycles terminate', () => {
  assert.deepEqual(resolveEvents([event(), event({ id: 'second', kind: 'revision', supersedes: ['first'] }), event({ id: 'deleted', kind: 'tombstone', supersedes: ['second'] })]), []);
  assert.deepEqual(resolveEvents([event({ supersedes: ['second'] }), event({ id: 'second', supersedes: ['first'] })]), []);
});

test('a missing or wrong-location evidence cannot substantiate a verified result', () => {
  assert.equal(resolveEvents([event({ evidence_level: 'remote', result: '训练完成' })])[0].evidence_level, 'unverified');
  assert.equal(resolveEvents([event({ evidence_level: 'remote', evidence: [{ kind: 'local', checked_at: '2026-09-22' }] })])[0].evidence_level, 'unverified');
  assert.equal(resolveEvents([event({ evidence_level: 'remote', evidence: [{ kind: 'remote', checked_at: '2026-09-22', digest: 'a'.repeat(64) }] })])[0].evidence_level, 'remote');
  assert.equal(resolveEvents([event({ evidence_level: 'remote', evidence: [{ kind: 'remote', checked_at: '2026-09-22' }] })])[0].evidence_level, 'unverified');
  assert.equal(resolveEvents([event({ evidence_level: 'local', evidence: [{ kind: 'local' }] })])[0].evidence_level, 'unverified');
});

test('priority actions are deduplicated, ordered and capped at seven', () => {
  const records = Array.from({ length: 10 }, (_, index) => event({ id: String(index), next_action: `行动${index}`, priority: index === 9 ? 'P0' : 'P2' }));
  records.push(event({ next_action: '行动9', priority: 'P0' }));
  const result = priorityActions(records); assert.equal(result.length, 7); assert.equal(result[0].next_action, '行动9');
});

test('day helpers consistently use Asia/Shanghai including midnight boundaries', () => {
  assert.equal(shanghaiDay(new Date('2026-09-21T16:00:00Z')), '2026-09-22'); assert.equal(shiftDay('2026-03-01', -1), '2026-02-28');
});

test('successful private connection checks owner identity and reads every file at one immutable SHA', async () => {
  const { client, calls } = fixture(); const model = await client.connect(credentials);
  assert.equal(model.projects[0].name, '测试项目'); assert.equal(model.sha, sha); assert.equal(model.events.length, 1);
  const historical = await client.loadDay(model, '2026-09-22'); assert.equal(historical[0].title, '工作');
  assert.equal(calls[0].url, 'https://api.github.com/user');
  for (const { url, options } of calls) {
    assert.ok(url.startsWith('https://api.github.com/')); assert.equal(options.method, 'GET'); assert.equal(options.cache, 'no-store'); assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error'); assert.equal(options.referrerPolicy, 'no-referrer');
    assert.equal(options.headers.Authorization, `Bearer ${credentials.token}`); assert.ok(!url.includes(credentials.token));
    if (url.includes('/contents/')) assert.ok(url.endsWith(`?ref=${sha}`));
  }
  assert.ok(!JSON.stringify(client).includes(credentials.token)); client.disconnect(); await assert.rejects(() => client.load(), error => error.revoked);
});

test('classic tokens and invalid names are rejected before any request', async () => {
  const { client, calls } = fixture();
  await assert.rejects(() => client.connect({ ...credentials, token: 'ghp_fake' }));
  await assert.rejects(() => client.connect({ ...credentials, repo: '../../other' })); assert.equal(calls.length, 0);
});

test('wrong user numeric ID, public repo, wrong repo owner or missing pull permission fail closed', async () => {
  for (const [path, body] of [
    ['/user', { id: 999, login: 'testowner' }],
    ['/repos/testowner/work-journal', { private: false, owner: { id: 123 }, permissions: { pull: true } }],
    ['/repos/testowner/work-journal', { private: true, owner: { id: 999 }, permissions: { pull: true } }],
    ['/repos/testowner/work-journal', { private: true, owner: { id: 123 }, permissions: { pull: false } }],
  ]) {
    const { client } = fixture({ [path]: response(body) }); await assert.rejects(() => client.connect(credentials), JournalError); assert.equal(client.identity, null);
  }
});

test('401 and 403 invalidate the in-memory client; 429 preserves it for a later retry', async () => {
  for (const status of [401, 403, 429]) {
    const { client, routes } = fixture(); const model = await client.connect(credentials);
    routes[`/repos/testowner/work-journal/contents/devices/a/days/2026-09-22.jsonl?ref=${sha}`] = response({}, status);
    await assert.rejects(() => client.loadDay(model, '2026-09-22'), error => error.status === status && error.revoked === (status !== 429));
    assert.equal(client.identity === null, status !== 429);
  }
});

test('network failure has bounded retries and retains an already connected identity', async () => {
  const { client, routes, calls } = fixture(); await client.connect(credentials); const count = calls.length;
  routes['/repos/testowner/work-journal/commits/main'] = () => { throw new Error('network'); };
  await assert.rejects(() => client.load(), error => !error.revoked); assert.equal(calls.length - count, 3); assert.ok(client.identity);
});

test('malformed schemas, oversized content and incomplete history fail visibly', async () => {
  const invalidCatalog = fixture({ [`/repos/testowner/work-journal/contents/projects.json?ref=${sha}`]: response(encoded('{"schema_version":2,"projects":[]}')) });
  await assert.rejects(() => invalidCatalog.client.connect(credentials), /格式版本/);
  const large = fixture({ [`/repos/testowner/work-journal/contents/projects.json?ref=${sha}`]: response({ encoding: 'base64', size: 9 * 1024 * 1024, content: 'e30=' }) });
  await assert.rejects(() => large.client.connect(credentials), /过大/);
  const { client, routes } = fixture(); const model = await client.connect(credentials);
  routes[`/repos/testowner/work-journal/contents/devices/a/days/2026-09-22.jsonl?ref=${sha}`] = response(encoded('{"id":'));
  await assert.rejects(() => client.loadDay(model, '2026-09-22'), /不完整/);
});

test('large files use the immutable blob endpoint when Contents omits base64', async () => {
  const blobSha = 'b'.repeat(40);
  const { client, calls } = fixture({
    [`/repos/testowner/work-journal/contents/projects.json?ref=${sha}`]: response({ encoding: 'none', size: 1024 * 1024 + 1, sha: blobSha }),
    [`/repos/testowner/work-journal/git/blobs/${blobSha}`]: response(encoded(JSON.stringify({ schema_version: 1, projects: [{ id: 'test', name: 'large file' }] }))),
  });
  const model = await client.connect(credentials); assert.equal(model.projects[0].name, 'large file'); assert.ok(calls.some(call => call.url.endsWith(`/git/blobs/${blobSha}`)));
});

test('logout during response JSON parsing cannot restore private state', async () => {
  let release;
  const { client } = fixture({ '/user': { ok: true, status: 200, json: () => new Promise(resolve => { release = resolve; }) } });
  const pending = client.connect(credentials);
  await new Promise(resolve => setImmediate(resolve)); client.disconnect(); release({ id: 123, login: 'testowner' });
  await assert.rejects(() => pending, error => error.revoked); assert.equal(client.identity, null);
});

test('public assets contain no persistent storage, HTML interpolation or outside scripts', async () => {
  const [app, html, demo] = await Promise.all(['../docs/app.js', '../docs/index.html', '../docs/demo.json'].map(path => readFile(new URL(path, import.meta.url), 'utf8')));
  assert.ok(!/localStorage|sessionStorage|indexedDB|serviceWorker|innerHTML|outerHTML|insertAdjacentHTML/.test(app));
  assert.ok(!/<script[^>]+src="https?:/.test(html)); assert.ok(html.includes("connect-src 'self' https://api.github.com"));
  const parsed = JSON.parse(demo); assert.ok(parsed.projects.every(project => project.id.startsWith('demo-'))); assert.ok(!demo.includes('github_pat_'));
});
