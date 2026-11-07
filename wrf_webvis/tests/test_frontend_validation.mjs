import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
const source = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const code = source.slice(source.indexOf('function selectionFromText'), source.indexOf('function parseOptionalNumber'));
const helpers = new Function(`${code}; return { selectionFromText, parseLevels };`)();
test('invalid ranges raise a visible error instead of falling back to defaults', () => {
  for (const value of ['0:2:0', '0:2:-1', '0:2:hello', '0,NaN,2']) {
    assert.throws(() => helpers.selectionFromText(value), Error, value);
  }
});
test('long selections are complete up to the limit, and reject oversized ranges', () => {
  assert.equal(helpers.selectionFromText('0:2000:1').values.length, 2001);
  assert.throws(() => helpers.selectionFromText('0:10000:1'), /10000/);
});
test('fractional and descending ranges retain their endpoint conventions', () => {
  assert.deepEqual(helpers.selectionFromText('0:0.3:0.1').values, [0, .1, .2, .3]);
  assert.deepEqual(helpers.selectionFromText('3:0:-1').values, [3, 2, 1, 0]);
  assert.deepEqual(helpers.parseLevels('3:0:-1'), [3, 2, 1]);
});
test('validation errors are visible inside the open plotting dialog', () => {
  const start = source.indexOf('function setStatus(');
  const end = source.indexOf('\nfunction ', start + 1);
  const statusEl = {}, formStatusEl = {}, dialog = { open: true };
  const setStatus = new Function('statusEl', 'formStatusEl', 'dialog', `${source.slice(start, end)}; return setStatus;`)(statusEl, formStatusEl, dialog);
  setStatus('范围步长不能为 0');
  assert.equal(formStatusEl.textContent, '范围步长不能为 0');
});
