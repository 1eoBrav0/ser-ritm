const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');

const source = readFileSync(join(__dirname, '../static/app.js'), 'utf8');
const code = source.slice(source.indexOf('function createFullscreenControl('), source.indexOf('function setupFullscreen('));
const create = vm.runInNewContext(code + '\ncreateFullscreenControl');
function fixture({native = true, version = true, desktop = true, allowed = true} = {}) {
  const events = {}, notices = [], attrs = {}, label = {};
  let entered = 0, exited = 0;
  const button = {hidden: true, setAttribute: (k, v) => attrs[k] = v, querySelector: () => label};
  const tg = native ? {initData: 'signed', isFullscreen: false,
    isVersionAtLeast: () => version, onEvent: (k, f) => events[k] = f,
    requestFullscreen: () => entered++, exitFullscreen: () => exited++} : null;
  const doc = {fullscreenEnabled: allowed, fullscreenElement: null,
    documentElement: {requestFullscreen: async () => entered++},
    exitFullscreen: async () => exited++, addEventListener: (k, f) => events[k] = f};
  const control = create({tg, doc, button, desktop, notify: text => notices.push(text)});
  return {tg, doc, button, control, events, notices, attrs, label,
    get entered() {return entered;}, get exited() {return exited;}};
}
test('desktop Telegram can automatically enter and explicitly leave', async () => {
  const f = fixture();
  assert.equal(f.button.hidden, false);
  await f.control.enter(); assert.equal(f.entered, 1);
  f.tg.isFullscreen = true; f.events.fullscreenChanged();
  assert.equal(f.attrs['aria-pressed'], 'true');
  assert.equal(f.label.textContent, 'Свернуть');
  await f.button.onclick(); assert.equal(f.exited, 1);
  f.tg.isFullscreen = false; f.events.fullscreenChanged();
  assert.equal(f.entered, 1, 'exit does not immediately force fullscreen again');
});
test('old clients and mobile devices never receive unsupported requests', async () => {
  for (const config of [{version: false}, {desktop: false}]) {
    const f = fixture(config); await f.control.enter();
    assert.equal(f.entered, 0); assert.equal(f.button.hidden, true);
  }
});
test('platform rejection of auto request is quiet and hides unavailable control', async () => {
  const f = fixture(); await f.control.enter();
  f.events.fullscreenFailed({error: 'UNSUPPORTED'});
  assert.equal(f.notices.length, 0); assert.equal(f.button.hidden, true);
  await f.control.enter(); assert.equal(f.entered, 1);
});
test('manual platform rejection explains the limitation', async () => {
  const f = fixture(); await f.button.onclick();
  f.events.fullscreenFailed({error: 'UNSUPPORTED'});
  assert.equal(f.notices.length, 1);
});
test('already fullscreen response is not reported as an error', async () => {
  const f = fixture(); await f.button.onclick();
  f.events.fullscreenFailed({error: 'ALREADY_FULLSCREEN'});
  assert.equal(f.notices.length, 0); assert.equal(f.button.hidden, false);
});
test('regular browser requests fullscreen only after button click', async () => {
  const f = fixture({native: false});
  assert.equal(f.entered, 0);
  await f.button.onclick(); assert.equal(f.entered, 1);
  f.doc.fullscreenElement = f.doc.documentElement; f.events.fullscreenchange();
  assert.equal(f.attrs['aria-pressed'], 'true');
  await f.button.onclick(); assert.equal(f.exited, 1);
});
test('browser denial is handled without an unhandled rejection', async () => {
  const f = fixture({native: false});
  f.doc.documentElement.requestFullscreen = async () => {throw new Error('NotAllowedError');};
  await f.button.onclick(); assert.equal(f.notices.length, 1);
});
test('browser without fullscreen capability hides control', async () => {
  const f = fixture({native: false, allowed: false});
  await f.button.onclick(); assert.equal(f.entered, 0); assert.equal(f.button.hidden, true);
});
test('startup requests native desktop fullscreen even in a narrow window, never browser or phone', () => {
  const setup = source.slice(source.indexOf('function setupFullscreen('), source.indexOf('async function boot()'));
  for (const [tg, pointerDesktop, expected] of [
    [{initData: 'signed', platform: 'tdesktop'}, false, 1],
    [{initData: 'signed', platform: 'macos'}, false, 1],
    [{initData: 'signed', platform: 'weba'}, true, 1],
    [{initData: 'signed', platform: 'android'}, false, 0],
    [null, true, 0],
  ]) {
    let requests = 0;
    const context = {document: {}, $: () => ({}), toast: () => {},
      matchMedia: () => ({matches: pointerDesktop}),
      createFullscreenControl: () => ({enter: () => requests++}), tg};
    vm.runInNewContext(setup + '\nsetupFullscreen(tg)', context);
    assert.equal(requests, expected);
  }
});
