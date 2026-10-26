// page/shield.js against a stub page: no browser, no DOM library. Run: node --test tests/
// The live check (a real YouTube pre-roll and mid-roll) is in README "Verify"; this pins the logic between runs.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'page', 'shield.js'), 'utf8');

function makePage(href, { player = null, enforcement = false } = {}) {
  const u = new URL(href);
  const listeners = {};
  const intervals = [];
  const calls = { open: [], fetch: [], beacon: [] };
  const appended = [];
  const el = (tag) => ({ tagName: tag, id: '', textContent: '', remove() { this.removed = true; } });
  const document = {
    head: { appendChild: (n) => appended.push(n) },
    documentElement: { appendChild: (n) => appended.push(n) },
    createElement: el,
    getElementById: (id) => (id === 'movie_player' ? player : null),
    querySelector: (sel) => {
      if (sel === 'ytd-enforcement-message-view-model') return enforcement ? {} : null;
      if (sel.includes('#movie_player.ad-showing')) return player && player.ads ? player : null;
      if (sel.includes('html5-video-player')) return player;
      return null;
    },
    addEventListener: () => {},
  };
  const window = {
    location: { hostname: u.hostname, href, origin: u.origin },
    document,
    addEventListener: (type, fn, capture) => { (listeners[type] = listeners[type] || []).push({ fn, capture }); },
    open: (...a) => { calls.open.push(a); return { opened: a[0] }; },
    fetch: (url) => { calls.fetch.push(url); return Promise.resolve('fetched'); },
    alert: () => { throw new Error('real alert'); },
    confirm: () => { throw new Error('real confirm'); },
    prompt: () => { throw new Error('real prompt'); },
    Notification: { requestPermission: () => Promise.resolve('granted') },
  };
  class XMLHttpRequest { open() {} send() { calls.fetch.push('xhr-sent'); } abort() { calls.fetch.push('xhr-aborted'); } }
  const navigator = {
    geolocation: { getCurrentPosition: () => 'real', watchPosition: () => 'real' },
    mediaDevices: { getUserMedia: () => Promise.resolve('stream') },
    sendBeacon: (url) => { calls.beacon.push(url); return true; },
  };
  const context = {
    window, document, location: window.location, navigator, XMLHttpRequest, URL, DOMException, Promise,
    setInterval: (fn) => intervals.push(fn), setTimeout: (fn) => fn(),
    MutationObserver: class { observe() {} }, Number, String, Boolean, Math, Object, Array, JSON, Set, TypeError, Error,
  };
  Object.assign(window, { XMLHttpRequest, navigator });
  context.globalThis = context;
  vm.createContext(context);
  // The script reads `window` as `w`; give the context's own globals the window's methods.
  return {
    run: () => vm.runInContext(SOURCE, context),
    window, calls, listeners, intervals, appended, context,
    tickAll: () => intervals.forEach((fn) => fn()),
  };
}

function makePlayer() {
  const classes = new Set(['html5-video-player']);
  const video = { muted: false, duration: 600, currentTime: 12, src: 'blob:content', addEventListener() {} };
  const skip = { clicks: 0, click() { this.clicks++; } };
  return {
    video, skip,
    classList: { contains: (c) => classes.has(c), add: (c) => classes.add(c), remove: (c) => classes.delete(c) },
    get ads() { return classes.has('ad-showing'); },
    querySelector: (sel) => (sel.startsWith('video') ? video : sel.includes('skip') && classes.has('ad-showing') ? skip : null),
  };
}

test('local dev hosts are left alone', () => {
  for (const href of ['http://localhost:5173/', 'http://127.0.0.1:8000/', 'http://app.localhost/', 'http://shop.test/']) {
    const page = makePage(href);
    const status = page.run();
    assert.match(status.skipped, /local dev host/);
    assert.equal(page.window.__browserShield, undefined);
    assert.equal(page.window.confirm.name, 'confirm');
  }
});

test('a pre-roll is muted, skipped and seeked to its end; sound comes back after', () => {
  const player = makePlayer();
  const page = makePage('https://www.youtube.com/watch?v=x', { player });
  page.run();
  player.classList.add('ad-showing');
  Object.assign(player.video, { duration: 135.4, currentTime: 3, src: 'blob:ad' });
  page.tickAll();
  assert.equal(player.video.currentTime, 135.3);
  assert.equal(player.video.muted, true);
  assert.equal(player.skip.clicks, 1);
  assert.equal(page.window.browserShield().adsSkipped, 1);
  player.classList.remove('ad-showing');
  Object.assign(player.video, { duration: 600, currentTime: 0, src: 'blob:content' });
  page.tickAll();
  assert.equal(player.video.muted, false);
  page.tickAll();
  assert.equal(player.video.currentTime, 0, 'content must never be seeked');
});

test('a second ad in the same pod counts once more, a repeated tick does not', () => {
  const player = makePlayer();
  const page = makePage('https://www.youtube.com/watch?v=x', { player });
  page.run();
  player.classList.add('ad-showing');
  Object.assign(player.video, { duration: 15, currentTime: 0.2, src: 'blob:a' });
  page.tickAll();
  Object.assign(player.video, { currentTime: 0.2 });
  page.tickAll();
  Object.assign(player.video, { duration: 30, currentTime: 0.1 });
  page.tickAll();
  assert.equal(page.window.browserShield().adsSkipped, 2);
});

test('the enforcement notice stops the skipper and is reported', () => {
  const player = makePlayer();
  const page = makePage('https://www.youtube.com/watch?v=x', { player, enforcement: true });
  page.run();
  player.classList.add('ad-showing');
  Object.assign(player.video, { duration: 20, currentTime: 1 });
  page.tickAll();
  const s = page.window.browserShield();
  assert.equal(s.enforcement, true);
  assert.equal(player.video.currentTime, 1);
  assert.equal(s.adsSkipped, 0);
});

function click(page, href, { download = false } = {}) {
  let prevented = false;
  let stopped = false;
  const a = { href, hasAttribute: (n) => n === 'download' && download };
  const e = {
    target: { closest: () => a },
    preventDefault() { prevented = true; },
    stopImmediatePropagation() { stopped = true; },
  };
  for (const { fn, capture } of page.listeners.click) { assert.equal(capture, true); fn(e); }
  return prevented && stopped;
}

test('ad, app-launch and download links are cancelled; ordinary links are not', () => {
  const page = makePage('https://growagarden.fandom.com/wiki/Seeds');
  page.run();
  assert.equal(click(page, 'https://www.googleadservices.com/pagead/aclk?x=1'), true);
  assert.equal(click(page, 'https://ad.doubleclick.net/clk'), true);
  assert.equal(click(page, 'zoommtg://zoom.us/join?confno=1'), true);
  assert.equal(click(page, 'https://example.org/setup.dmg'), true);
  assert.equal(click(page, 'https://example.org/page', { download: true }), true);
  assert.equal(click(page, 'https://growagarden.fandom.com/wiki/Pets'), false);
  assert.equal(click(page, 'javascript:void(0)'), false);
  assert.equal(click(page, 'https://notdoubleclick.net/'), false);
  assert.equal(page.window.browserShield().clicksBlocked, 5);
});

test('cross-origin pop-ups are refused, same-origin ones open', () => {
  const page = makePage('https://growagarden.fandom.com/wiki/Seeds');
  page.run();
  assert.equal(page.window.open('https://scam.example/win'), null);
  assert.deepEqual(page.window.open('/wiki/Pets'), { opened: '/wiki/Pets' });
  assert.equal(page.calls.open.length, 1);
  assert.equal(page.window.browserShield().popupsBlocked, 1);
});

test('dialogs and permission prompts are answered without a prompt', async () => {
  const page = makePage('https://growagarden.fandom.com/');
  page.run();
  assert.equal(page.window.confirm('Leave?'), false);
  assert.equal(page.window.prompt('Name?'), null);
  assert.equal(page.window.alert('hi'), undefined);
  assert.equal(await page.window.Notification.requestPermission(), 'denied');
  let failed = null;
  page.context.navigator.geolocation.getCurrentPosition(() => {}, (err) => { failed = err.code; });
  assert.equal(failed, 1);
  await assert.rejects(page.context.navigator.mediaDevices.getUserMedia({ video: true }), { name: 'NotAllowedError' });
  const s = page.window.browserShield();
  assert.equal(s.dialogsAnswered, 3);
  assert.equal(s.permissionsDenied, 3);
});

test('requests to ad networks are dropped off YouTube and left alone on it', async () => {
  const page = makePage('https://growagarden.fandom.com/');
  page.run();
  await assert.rejects(page.window.fetch('https://securepubads.g.doubleclick.net/gampad/ads'), TypeError);
  assert.equal(await page.window.fetch('https://static.wikia.nocookie.net/x.png'), 'fetched');
  assert.equal(page.context.navigator.sendBeacon('https://ib.adnxs.com/ping'), true);
  assert.deepEqual(page.calls.beacon, []);

  const yt = makePage('https://www.youtube.com/watch?v=x', { player: makePlayer() });
  yt.run();
  assert.equal(await yt.window.fetch('https://googleads.g.doubleclick.net/pagead/id'), 'fetched');
});

test('running it twice keeps one copy and returns the status', () => {
  const page = makePage('https://growagarden.fandom.com/');
  const first = page.run();
  const wrappedOpen = page.window.open;
  const second = page.run();
  assert.equal(page.window.open, wrappedOpen);
  assert.equal(page.listeners.click.length, 1);
  assert.equal(first.version, second.version);
  assert.equal(page.appended.length, 1);
});

test('off() restores pass-through and removes the hiding style', () => {
  const page = makePage('https://growagarden.fandom.com/');
  page.run();
  page.window.__browserShield.off();
  assert.deepEqual(page.window.open('https://scam.example/'), { opened: 'https://scam.example/' });
  assert.equal(click(page, 'https://ad.doubleclick.net/clk'), false);
  assert.equal(page.appended[0].removed, true);
});
