import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
test('closing the zoom dialog releases the full size image, including Escape closure', () => {
  const handlers = {};
  const img = {src:'/large.png', removeAttribute(name) {delete this[name];}};
  const context = {imageZoomDialog:{close(){}, addEventListener:(name, fn)=>handlers[name]=fn}, zoomImageEl:img,
    document:{querySelector:()=>({addEventListener(){}})}};
  vm.createContext(context);
  const start = source.indexOf('document.querySelector("#closeZoom")');
  vm.runInContext(source.slice(start, source.indexOf('\n', source.indexOf(';', start)) + 1), context);
  // Evaluate the dedicated close listener, if present.
  const listener = source.match(/imageZoomDialog\.addEventListener\("close",[^\n]+/);
  if (listener) vm.runInContext(listener[0], context);
  assert.equal(typeof handlers.close, 'function');
  handlers.close();
  assert.equal(img.src, undefined);
});

test('gallery uses stable native image sources for Safari copy and defers offscreen loads', () => {
  const render = source.slice(source.indexOf('function renderImages('), source.indexOf('function enableImageSorting('));
  assert.match(render, /<img src="[^"\n]+" loading="lazy"/);
  assert.doesNotMatch(render, /data-src|observeImagePreviews|releaseImagePreviews|data-copy|下载原图/);
});
