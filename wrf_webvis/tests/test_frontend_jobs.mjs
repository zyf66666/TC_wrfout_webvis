import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const code = source.slice(source.indexOf('async function watchJob('), source.indexOf('async function openRecentLogs('));
const tick = () => new Promise(resolve => setImmediate(resolve));
function node() { return { innerHTML: '', textContent: '', disabled: true, value: '', appendChild() {}, classList: { add() {} } }; }
function harness() {
  const calls = [], requests = [], sleeps = [], saved = new Map(), finished = [];
  const context = {
    currentProgressJob: null, progressJobs: new Map(), progressStorageKey: 'jobs',
    stopJobButton: node(), cancelProgressButton: node(), progressJobSelect: node(),
    progressPanel: node(), progressMessages: node(), document: { createElement: node },
    setStatus() {}, console,
    sessionStorage: { setItem: (k, v) => saved.set(k, v), getItem: k => saved.get(k) ?? null },
    api(url) { calls.push(url); return new Promise((resolve, reject) => requests.push({ url, resolve, reject })); },
    sleep(ms) { return new Promise(resolve => sleeps.push({ ms, resolve })); },
    handleJobFinished: async result => finished.push(result.id),
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  return { context, calls, requests, sleeps, saved, finished };
}
function response(id, status) { return { id, title: id, status, messages: [], result: null, error: null }; }

test('an older completion cannot stop monitoring a newer job', async () => {
  const h = harness(), completed = [];
  const a = h.context.watchJob('A', () => completed.push('A'));
  const b = h.context.watchJob('B', () => completed.push('B'));
  h.requests[0].resolve(response('A', 'complete'));
  await a;
  assert.equal(h.context.currentProgressJob, 'B');
  assert.equal(h.context.stopJobButton.disabled, false);
  h.requests[1].resolve(response('B', 'running'));
  await tick();
  h.sleeps.shift().resolve();
  await tick();
  assert.equal(h.calls.at(-1), '/api/jobs/B/progress');
  h.requests.at(-1).resolve(response('B', 'complete'));
  await b;
  assert.deepEqual(completed, ['A', 'B']);
});

test('each concurrent job continues polling until its own completion', async () => {
  const h = harness(), completed = [];
  const a = h.context.watchJob('A', () => completed.push('A'));
  const b = h.context.watchJob('B', () => completed.push('B'));
  h.requests[0].resolve(response('A', 'running'));
  h.requests[1].resolve(response('B', 'running'));
  await tick();
  for (const sleep of h.sleeps.splice(0)) sleep.resolve();
  await tick();
  assert.equal(h.calls.filter(url => url === '/api/jobs/A/progress').length, 2);
  assert.equal(h.calls.filter(url => url === '/api/jobs/B/progress').length, 2);
  for (const request of h.requests.slice(2)) request.resolve(response(request.url.includes('/A/') ? 'A' : 'B', 'complete'));
  await Promise.all([a, b]);
  assert.deepEqual(completed.sort(), ['A', 'B']);
});

test('transient network failures retry and do not lose the job', async () => {
  const h = harness();
  const resultPromise = h.context.watchJob('A', () => {});
  // Observe rejection too so the old implementation fails as an assertion.
  const observed = resultPromise.then(() => 'done', () => 'rejected');
  h.requests[0].reject(new TypeError('offline'));
  await tick();
  assert.equal(h.sleeps.length, 1, 'a failed poll should schedule a retry');
  h.sleeps.shift().resolve();
  await tick();
  h.requests.at(-1).resolve(response('A', 'complete'));
  assert.equal(await observed, 'done');
});

test('missing jobs finish with an actionable error and clear saved tracking', async () => {
  const h = harness();
  let final;
  const resultPromise = h.context.watchJob('A', result => { final = result; });
  const observed = resultPromise.then(() => 'done', () => 'rejected');
  h.requests[0].reject(Object.assign(new Error('Not found'), { status: 404 }));
  assert.equal(await observed, 'done');
  assert.equal(final.status, 'failed');
  assert.match(final.error, /重启|不存在/);
  assert.equal(h.context.stopJobButton.disabled, true);
  assert.deepEqual(JSON.parse(h.saved.get('jobs')), []);
});

test('active jobs are saved and resume after refresh', async () => {
  const h = harness();
  const first = h.context.watchJob('A', () => {}, { dashboardId: 'dashboard', title: 'temperature' });
  const saved = h.saved.get('jobs');
  assert.ok(saved, 'active job IDs should be saved for refresh recovery');
  const restored = harness();
  restored.saved.set('jobs', saved);
  restored.context.restoreProgressJobs();
  assert.deepEqual(restored.calls, ['/api/jobs/A/progress']);
  restored.requests[0].resolve(response('A', 'complete'));
  h.requests[0].resolve(response('A', 'complete'));
  await first;
  await tick();
  assert.deepEqual(restored.finished, ['A']);
});

test('cancel targets the selected job without canceling others', async () => {
  const h = harness();
  const a = h.context.watchJob('A', () => {});
  const b = h.context.watchJob('B', () => {});
  h.context.currentProgressJob = 'A';
  const cancel = h.context.cancelCurrentJob();
  assert.equal(h.calls.at(-1), '/api/jobs/A/cancel');
  h.requests.at(-1).resolve(response('A', 'running'));
  await cancel;
  h.requests[0].resolve(response('A', 'canceled'));
  h.requests[1].resolve(response('B', 'complete'));
  await Promise.all([a, b]);
});

test('completed job details remain selectable while other jobs run', async () => {
  const h = harness();
  const a = h.context.watchJob('A', () => {});
  const b = h.context.watchJob('B', () => {});
  h.requests[0].resolve(response('A', 'complete'));
  await a;
  h.context.currentProgressJob = 'A';
  h.context.renderJobList();
  assert.equal(h.context.currentProgressJob, 'A');
  assert.equal(h.context.stopJobButton.disabled, true);
  h.requests[1].resolve(response('B', 'complete'));
  await b;
});
