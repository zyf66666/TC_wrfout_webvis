import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const read = (name) => fs.readFileSync(new URL(`../app/static/${name}`, import.meta.url), 'utf8');

// 输入法组字时按回车只是确认候选词：Safari 里这一下 keydown 的 isComposing 已是 false（keyCode 229，compositionend 先到）
for (const file of ['data_management.js']) {
  test(`${file}: Enter that confirms an IME candidate does not submit`, () => {
    const js = read(file);
    assert.match(js, /compositionstart/);
    assert.match(js, /compositionend/);
    assert.match(js, /keyCode === 229/);
    assert.match(js, /Date\.now\(\) - compositionEndedAt < 150/);
  });
}
