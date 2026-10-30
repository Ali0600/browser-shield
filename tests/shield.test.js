// page/shield.js against a stub page: no browser, no DOM library. Run: node --test tests/shield.test.js
// The live check (a real YouTube pre-roll and mid-roll) is in README "Verify"; this pins the logic between runs.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'page', 'shield.js'), 'utf8');

// source: the script to run (default page/shield.js as it is); top: what window.top is (default the window itself,
// a sub-frame passes another object); timeouts 'run' calls setTimeout callbacks at once, 'record' keeps them.
function makePage(href, { player = null, signs = [], source = SOURCE, top, timeouts = 'run', elements = {} } = {}) {
  const u = new URL(href);
  const listeners = {};
  const navListeners = [];
  const intervals = [];
  const timers = [];
  const calls = { open: [], fetch: [], beacon: [], exec: [], console: [] };
  const appended = [];
  const el = (tag) => ({ tagName: tag, id: '', textContent: '', remove() { this.removed = true; } });
  const body = { localName: 'body' };
  const document = {
    head: { appendChild: (n) => appended.push(n) },
    documentElement: { appendChild: (n) => appended.push(n) },
    body,
    activeElement: body,
    createElement: el,
    getElementById: (id) => (id === 'movie_player' ? player : elements[id] || null),
    querySelector: (sel) => {
      if (signs.some((s) => sel.includes(s))) return {};
      if (sel.includes('html5-video-player')) return player;
      return null;
    },
    addEventListener: () => {},
    execCommand: (cmd) => { calls.exec.push(cmd); return true; },
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
    print: () => { throw new Error('real print'); },
    Notification: { requestPermission: () => Promise.resolve('granted') },
    navigation: { addEventListener: (type, fn) => navListeners.push(fn) },
    Element: { prototype: { requestFullscreen: () => Promise.resolve('fullscreen'), requestPointerLock: () => 'locked' } },
  };
  class XMLHttpRequest { open() {} send() { calls.fetch.push('xhr-sent'); } abort() { calls.fetch.push('xhr-aborted'); } }
  const navigator = {
    geolocation: { getCurrentPosition: () => 'real', watchPosition: () => 'real' },
    mediaDevices: { getUserMedia: () => Promise.resolve('stream') },
    clipboard: { writeText: () => Promise.resolve('copied'), write: () => Promise.resolve('copied') },
    keyboard: { lock: () => Promise.resolve('locked') },
    registerProtocolHandler: () => 'registered',
    sendBeacon: (url) => { calls.beacon.push(url); return true; },
  };
  // A console that records: the page's own console must stay silent whatever the cookie engine logs.
  const consoleSpy = new Proxy({}, { get: (_, name) => (...args) => calls.console.push([String(name), ...args]) });
  const context = {
    window, document, location: window.location, navigator, XMLHttpRequest, URL, DOMException, Promise,
    setInterval: (fn) => intervals.push(fn),
    setTimeout: (fn, ms) => { if (timeouts === 'run') fn(); else timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
    MutationObserver: class { observe() {} disconnect() {} }, Number, String, Boolean, Math, Object, Array, JSON, Set,
    TypeError, Error, Proxy, Map, WeakMap, Symbol, Reflect, RegExp, Date, Uint8Array, console: consoleSpy,
    crypto: globalThis.crypto, performance: { now: () => 0, mark() {}, measure() {} },
  };
  Object.assign(window, { XMLHttpRequest, navigator, top: top === undefined ? window : top });
  vm.createContext(context);
  const originals = { open: window.open, alert: window.alert, confirm: window.confirm, fetch: window.fetch, xhrOpen: XMLHttpRequest.prototype.open };
  return {
    run: () => vm.runInContext(source, context),
    window, document, calls, listeners, navListeners, intervals, timers, appended, context, originals,
    tickAll: () => intervals.forEach((fn) => fn()),
  };
}

function makePlayer({ lengthSeconds = 600, debugInfo = 'USDAI, SABR, s:4' } = {}) {
  const classes = new Set(['html5-video-player']);
  const video = { muted: false, duration: 600, currentTime: 12, src: 'blob:content', addEventListener() {} };
  const skip = { clicks: 0, click() { this.clicks++; } };
  const player = {
    video, skip, seeks: [], progress: { duration: 0, current: 0 }, debugInfo,
    classList: { contains: (c) => classes.has(c), add: (c) => classes.add(c), remove: (c) => classes.delete(c) },
    querySelector: (sel) => (sel.startsWith('video') ? video : sel.includes('skip') && classes.has('ad-showing') ? skip : null),
    getStatsForNerds: () => ({ debug_info: player.debugInfo, buffer_health_seconds: '12.00 s', resolution: '854x480' }),
    getProgressState: () => player.progress,
    getPlayerResponse: () => ({ videoDetails: { lengthSeconds: String(lengthSeconds) }, playabilityStatus: { status: 'OK' } }),
    seekTo: (t) => player.seeks.push(t),
  };
  return player;
}

const YT = 'https://www.youtube.com/watch?v=x';
const WIKI = 'https://growagarden.fandom.com/wiki/Seeds';

test('local dev hosts are left alone', () => {
  for (const href of ['http://localhost:5173/', 'http://127.0.0.1:8000/', 'http://app.localhost/', 'http://shop.test/']) {
    const page = makePage(href);
    const status = page.run();
    assert.match(status.skipped, /local dev host/);
    assert.equal(page.window.__browserShield, undefined);
    assert.equal(page.window.confirm, page.originals.confirm);
    assert.equal(page.window.open, page.originals.open);
  }
});

test('a pre-roll is muted and seeked to its end; sound comes back after; content is never seeked', () => {
  const player = makePlayer();
  const page = makePage(YT, { player });
  page.run();
  player.classList.add('ad-showing');
  Object.assign(player.video, { duration: 135.4, currentTime: 3, src: 'blob:ad' });
  page.tickAll();
  assert.equal(player.video.currentTime, 135.3);
  assert.equal(player.video.muted, true);
  assert.equal(page.window.browserShield().adsSkipped, 1);
  assert.equal(page.window.browserShield().adPlaying, true);
  player.classList.remove('ad-showing');
  Object.assign(player.video, { duration: 600, currentTime: 0, src: 'blob:content' });
  page.tickAll();
  assert.equal(player.video.muted, false);
  page.tickAll();
  assert.equal(player.video.currentTime, 0);
  assert.deepEqual(player.seeks, []);
});

test('a second ad in the same pod counts once more, a repeated tick does not', () => {
  const player = makePlayer();
  const page = makePage(YT, { player });
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

test('a server-stitched ad is skipped through the player, never by seeking the <video>', () => {
  const player = makePlayer({ debugInfo: 'SSAP, AD, s:4 t:3.1' });
  const page = makePage(YT, { player });
  page.run();
  Object.assign(player.video, { duration: 630, currentTime: 3 });  // ad + content on one timeline
  player.progress = { duration: 30, current: 3 };
  page.tickAll();
  assert.deepEqual(player.seeks.slice(0, 1), [30]);
  assert.equal(player.video.currentTime, 3, 'seeking the stitched <video> would skip the whole video');
  assert.equal(player.video.muted, true);
  assert.equal(page.window.browserShield().adsSkipped, 1);
});

test('an "ad" clip as long as the video itself is never seeked', () => {
  const player = makePlayer({ lengthSeconds: 600 });
  const page = makePage(YT, { player });
  page.run();
  player.classList.add('ad-showing');
  Object.assign(player.video, { duration: 600.4, currentTime: 5 });
  page.tickAll();
  assert.equal(player.video.currentTime, 5);
  assert.equal(player.skip.clicks > 0, true, 'falls back to the Skip button');
});

for (const [sign, label] of [['ytd-enforcement-message-view-model', 'ad-blocker notice'],
  ['support.google.com/youtube/answer/3037019', 'Experiencing interruptions']]) {
  test(`the enforcement sign (${label}) stops the skipper and is reported`, () => {
    const player = makePlayer();
    const page = makePage(YT, { player, signs: [sign] });
    page.run();
    player.classList.add('ad-showing');
    Object.assign(player.video, { duration: 20, currentTime: 1 });
    page.tickAll();
    const s = page.window.browserShield();
    assert.equal(s.enforcement, true);
    assert.match(s.enforcementSign, new RegExp(label));
    assert.equal(player.video.currentTime, 1);
    assert.equal(s.adsSkipped, 0);
  });
}

test('on YouTube no built-in function is replaced', () => {
  const page = makePage(YT, { player: makePlayer() });
  page.run();
  assert.equal(page.window.open, page.originals.open);
  assert.equal(page.window.alert, page.originals.alert);
  assert.equal(page.window.fetch, page.originals.fetch);
  assert.equal(page.context.XMLHttpRequest.prototype.open, page.originals.xhrOpen);
  assert.equal(typeof page.window.browserShield, 'function');
});

function click(page, href, { download = false, type = 'click' } = {}) {
  let prevented = false;
  let stopped = false;
  const a = { href, hasAttribute: (n) => n === 'download' && download };
  const e = {
    target: { closest: () => a },
    preventDefault() { prevented = true; },
    stopImmediatePropagation() { stopped = true; },
  };
  for (const { fn, capture } of page.listeners[type]) { assert.equal(capture, true); fn(e); }
  return prevented && stopped;
}

test('ad, app-launch and download links are cancelled; ordinary links are not', () => {
  const page = makePage(WIKI);
  page.run();
  assert.equal(click(page, 'https://www.googleadservices.com/pagead/aclk?x=1'), true);
  assert.equal(click(page, 'https://ad.doubleclick.net/clk', { type: 'auxclick' }), true);
  assert.equal(click(page, 'zoommtg://zoom.us/join?confno=1'), true);
  assert.equal(click(page, 'https://example.org/setup.dmg'), true);
  assert.equal(click(page, 'https://example.org/page', { download: true }), true);
  assert.equal(click(page, 'https://growagarden.fandom.com/wiki/Pets'), false);
  assert.equal(click(page, 'javascript:void(0)'), false);
  assert.equal(click(page, 'https://notdoubleclick.net/'), false);
  assert.equal(page.window.browserShield().clicksBlocked, 5);
});

test('form posts and page-started navigations to bad targets are cancelled', () => {
  const page = makePage(WIKI);
  page.run();
  let prevented = false;
  page.listeners.submit[0].fn({ target: { action: 'https://ad.doubleclick.net/collect' }, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  const nav = (destination, downloadRequest = null) => {
    let cancelled = false;
    for (const fn of page.navListeners) fn({ cancelable: true, destination: { url: destination }, downloadRequest, preventDefault() { cancelled = true; } });
    return cancelled;
  };
  assert.equal(nav('https://www.googleadservices.com/pagead/aclk?x'), true);
  assert.equal(nav('applescript://com.apple.scripteditor?action=new'), true);
  assert.equal(nav('https://growagarden.fandom.com/x', 'payload.zip'), true);
  assert.equal(nav('https://growagarden.fandom.com/wiki/Pets'), false);
  assert.equal(page.window.browserShield().navigationsBlocked, 3);
});

test('cross-origin pop-ups are refused, same-origin ones open', () => {
  const page = makePage(WIKI);
  page.run();
  assert.equal(page.window.open('https://scam.example/win'), null);
  assert.deepEqual(page.window.open('/wiki/Pets'), { opened: '/wiki/Pets' });
  assert.equal(page.calls.open.length, 1);
  assert.equal(page.window.browserShield().popupsBlocked, 1);
});

test('dialogs, permissions, clipboard, fullscreen and keyboard traps are answered without a prompt', async () => {
  const page = makePage(WIKI);
  page.run();
  const nav = page.context.navigator;
  assert.equal(page.window.confirm('Leave?'), false);
  assert.equal(page.window.prompt('Name?'), null);
  assert.equal(page.window.alert('hi'), undefined);
  assert.equal(page.window.print(), undefined);
  assert.equal(await page.window.Notification.requestPermission(), 'denied');
  let failed = null;
  nav.geolocation.getCurrentPosition(() => {}, (err) => { failed = err.code; });
  assert.equal(failed, 1);
  await assert.rejects(nav.mediaDevices.getUserMedia({ video: true }), { name: 'NotAllowedError' });
  await assert.rejects(nav.clipboard.writeText('curl https://x | sh'), { name: 'NotAllowedError' });
  assert.equal(page.context.document.execCommand('copy'), false);
  assert.equal(page.context.document.execCommand('bold'), true);
  await assert.rejects(page.window.Element.prototype.requestFullscreen(), { name: 'NotAllowedError' });
  await assert.rejects(nav.keyboard.lock(), { name: 'NotAllowedError' });
  assert.equal(nav.registerProtocolHandler('web+x', 'https://x/?%s'), undefined);
  const s = page.window.browserShield();
  assert.equal(s.dialogsAnswered, 4);
  assert.equal(s.permissionsDenied, 8);
});

test('requests to ad networks are dropped off YouTube', async () => {
  const page = makePage(WIKI);
  page.run();
  await assert.rejects(page.window.fetch('https://securepubads.g.doubleclick.net/gampad/ads'), TypeError);
  assert.equal(await page.window.fetch('https://static.wikia.nocookie.net/x.png'), 'fetched');
  assert.equal(page.context.navigator.sendBeacon('https://ib.adnxs.com/ping'), true);
  assert.deepEqual(page.calls.beacon, []);
});

test('running it twice keeps one copy and returns the status', () => {
  const page = makePage(WIKI);
  const first = page.run();
  const wrappedOpen = page.window.open;
  const second = page.run();
  assert.equal(page.window.open, wrappedOpen);
  assert.equal(page.listeners.click.length, 1);
  assert.equal(first.version, second.version);
  assert.equal(page.appended.length, 1);
});

test('off() restores pass-through and removes the hiding style', () => {
  const page = makePage(WIKI);
  page.run();
  page.window.__browserShield.off();
  assert.deepEqual(page.window.open('https://scam.example/'), { opened: 'https://scam.example/' });
  assert.equal(click(page, 'https://ad.doubleclick.net/clk'), false);
  assert.equal(page.appended[0].removed, true);
});

test('the hiding style never hides #player-ads (uBlock Origin un-hides it on purpose)', () => {
  const page = makePage(YT, { player: makePlayer() });
  page.run();
  assert.doesNotMatch(page.appended[0].textContent, /#player-ads/);
  assert.match(page.appended[0].textContent, /ytd-ad-slot-renderer/);
});

// ---- the 2026-10-26 review's findings (PS-nn) ----------------------------------------------------------------------

test('PS-01: an older version on the page stands down and the new one takes over', () => {
  const page = makePage(WIKI);
  let offCalled = 0;
  page.window.__browserShield = { status: () => ({ version: 2, enabled: true }), off() { offCalled++; } };
  const s = page.run();
  assert.equal(offCalled, 1);
  assert.equal(s.version, 4);
  assert.equal(s.upgradedFrom, 2);
});

test('PS-02: ad-click paths, link wrappers and mailto: are judged like the guard hook does', () => {
  const page = makePage('https://www.google.com/search?q=vpn');
  page.run();
  assert.equal(click(page, 'https://www.google.com/aclk?sa=l&ai=x&adurl=https://vpn.example/'), true);
  assert.equal(click(page, 'https://www.youtube.com/pagead/aclk?x=1'), true);
  assert.equal(click(page, 'https://www.youtube.com/redirect?q=https%3A%2F%2Fevil.example%2Fsetup.dmg'), true);
  assert.equal(click(page, 'https://www.google.com/url?q=https://ad.doubleclick.net/x'), true);
  assert.equal(click(page, 'mailto:someone@example.org'), true);
  assert.equal(click(page, 'https://www.youtube.com/redirect?q=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FRoblox'), false);
  assert.equal(click(page, 'https://www.google.com/search?q=https://ad.doubleclick.net'), false);
});

test('PS-08: a site\'s own pages named like scripts are pages; same-document navigations and plain forms pass', () => {
  const page = makePage('https://github.com/ohmyzsh/ohmyzsh/blob/master/oh-my-zsh.sh');
  page.run();
  assert.equal(click(page, 'https://github.com/acmesh-official/acme.sh'), false);
  assert.equal(click(page, 'https://github.com/ohmyzsh/ohmyzsh/blob/master/oh-my-zsh.sh#L10'), false);
  assert.equal(click(page, 'https://github.com/x/y/releases/download/v1/app.dmg'), true, 'an installer stays a download');
  assert.equal(click(page, 'https://codeload.github.com/x/y/zip/refs/heads/main.zip'), true, 'another site\'s archive too');
  let cancelled = false;
  for (const fn of page.navListeners) fn({ cancelable: true, destination: { url: 'https://github.com/x/y/blob/main/a.sh?plain=1', sameDocument: true }, downloadRequest: null, preventDefault() { cancelled = true; } });
  assert.equal(cancelled, false);
  // A page whose own address ends like an installer: moving to an anchor on it is not a download.
  for (const fn of page.navListeners) fn({ cancelable: true, destination: { url: 'https://github.com/x/y/raw/main/setup.dmg#notes', sameDocument: true }, downloadRequest: null, preventDefault() { cancelled = true; } });
  assert.equal(cancelled, false);
  let prevented = false;
  page.listeners.submit[0].fn({ target: { getAttribute: () => null, action: 'https://github.com/ohmyzsh/ohmyzsh/blob/master/oh-my-zsh.sh' }, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
});

test('PS-09: window.open into the same tab is a navigation, and a blank window needs a click', () => {
  const page = makePage(WIKI);
  page.run();
  assert.deepEqual(page.window.open('https://docs.example.org/next', '_self'), { opened: 'https://docs.example.org/next' });
  assert.equal(page.window.open('https://ad.doubleclick.net/x', '_self'), null);
  assert.equal(page.window.open(), null, 'no click, no blank window (the start of a pop-under)');
  page.context.navigator.userActivation = { isActive: true };
  assert.deepEqual(page.window.open(), { opened: undefined });
  assert.deepEqual(page.window.open('https://docs.example.org/print'), { opened: 'https://docs.example.org/print' });
});

test('PS-10: off() passes every replaced function through, and a later run switches the shield back on', () => {
  const page = makePage(WIKI);
  page.run();
  page.window.__browserShield.off();
  assert.throws(() => page.window.confirm('Close?'), /real confirm/);
  assert.throws(() => page.window.alert('hi'), /real alert/);
  const back = page.run();
  assert.equal(back.enabled, true);
  assert.equal(page.window.confirm('Close?'), false);
});

test('PS-05: standing down mid-ad gives the video its sound back', () => {
  const player = makePlayer();
  const signs = [];
  const page = makePage(YT, { player, signs });
  page.run();
  player.classList.add('ad-showing');
  Object.assign(player.video, { duration: 20, currentTime: 1, src: 'blob:ad' });
  page.tickAll();
  assert.equal(player.video.muted, true);
  signs.push('ytd-enforcement-message-view-model');
  page.tickAll();
  assert.equal(player.video.muted, false);
  assert.equal(page.window.browserShield().enforcement, true);
});

test('PS-04 and PS-11: with no video length the clip is never seeked, and content is never muted', () => {
  const player = makePlayer({ lengthSeconds: 0 });
  const page = makePage(YT, { player });
  page.run();
  player.classList.add('ad-showing');
  Object.assign(player.video, { duration: 600, currentTime: 0.5 });
  page.tickAll();
  assert.equal(player.video.currentTime, 0.5);
  assert.equal(player.video.muted, false);
});

test('PS-03: an ad longer than a short video is still skipped', () => {
  const player = makePlayer({ lengthSeconds: 60 });
  const page = makePage(YT, { player });
  page.run();
  player.classList.add('ad-showing');
  Object.assign(player.video, { duration: 135.4, currentTime: 3, src: 'blob:ad' });
  page.tickAll();
  assert.equal(player.video.currentTime, 135.3);
});

test('PS-06: a stitched mid-roll whose progress is not the video clock is muted and left alone', () => {
  const player = makePlayer({ debugInfo: 'SSAP, AD, s:4 t:303' });
  const page = makePage(YT, { player });
  page.run();
  Object.assign(player.video, { duration: 630, currentTime: 303 });
  player.progress = { duration: 30, current: 3 };
  page.tickAll();
  assert.deepEqual(player.seeks, []);
  assert.equal(player.video.currentTime, 303);
  assert.equal(player.video.muted, true);
  assert.match(page.window.browserShield().log.join(' '), /left to end/);
});

test('PS-13: links inside shadow roots and SVG links are seen', () => {
  const page = makePage(WIKI);
  page.run();
  const fire = (node) => {
    let prevented = false;
    const e = { target: { closest: () => null }, composedPath: () => [{ localName: 'span' }, node], preventDefault() { prevented = true; }, stopImmediatePropagation() {} };
    for (const { fn } of page.listeners.click) fn(e);
    return prevented;
  };
  assert.equal(fire({ localName: 'a', href: 'https://ad.doubleclick.net/clk', hasAttribute: () => false }), true);
  const svgHref = { baseVal: 'zoommtg://zoom.us/join' };
  assert.equal(fire({ localName: 'a', href: svgHref, getAttribute: () => null, getAttributeNS: () => 'zoommtg://zoom.us/join', hasAttribute: () => false }), true);
  assert.equal(fire({ localName: 'a', href: 'https://growagarden.fandom.com/wiki/Pets', hasAttribute: () => false }), false);
});

test('PS-14 and PS-15: fetch with a URL object is judged, and a page-built blob: link is named for what it is', async () => {
  const page = makePage(WIKI);
  page.run();
  await assert.rejects(page.window.fetch(new URL('https://securepubads.g.doubleclick.net/gampad/ads')), TypeError);
  assert.equal(click(page, 'blob:https://growagarden.fandom.com/3f2a'), true);
  assert.match(page.window.browserShield().log.join(' '), /built itself/);
});

test('PS-16: the replacements keep the shapes pages rely on', async () => {
  const page = makePage(WIKI);
  page.run();
  let err = null;
  page.context.navigator.geolocation.getCurrentPosition(() => {}, (e) => { err = e; });
  assert.equal(err.code, err.PERMISSION_DENIED);
  await assert.rejects(page.window.Element.prototype.requestPointerLock(), { name: 'NotAllowedError' });
  let answered = null;
  await page.window.Notification.requestPermission((r) => { answered = r; });
  assert.equal(answered, 'denied');
});

test('PS-17: the interruptions link counts only inside the player or a notice', () => {
  const asked = [];
  const page = makePage(YT, { player: makePlayer() });
  const realQS = page.context.document.querySelector;
  page.context.document.querySelector = (sel) => { asked.push(sel); return realQS(sel); };
  page.run();
  page.tickAll();
  const helpSelectors = asked.filter((s) => s.includes('3037019'));
  assert.ok(helpSelectors.length > 0);
  for (const sel of helpSelectors) for (const part of sel.split(',')) assert.match(part.trim(), /^(#movie_player|ytd-popup-container|tp-yt-paper-dialog|#error-screen|yt-playability-error-supported-renderers) /);
});

// ---- cookie banners ------------------------------------------------------------------------------------------------
// A stand-in for the vendored engine, spliced into the slots the guard hook fills. It records what the shield gives it
// and lets each test play the engine's part: globalThis inside the stub page is the vm context.
const FAKE_ENGINE = `function (module, exports, console) {
  'use strict';
  class Fake {
    constructor(send) {
      if (globalThis.__fakeThrows) throw new TypeError('engine broke');
      (globalThis.__fakes = globalThis.__fakes || []).push(this);
      this.send = send;
      this.rules = [{ name: 'dynamic' }];
      this.state = { lifecycle: 'loading', clicks: 0, heuristicSnippets: ['SECRET PAGE TEXT'] };
      this.restores = 0;
      send({ type: 'report', state: this.state });
      send({ type: 'init', url: 'x' });
      console.log('engine says hello'); console.warn('engine warns'); console.error('engine errs');
    }
    get config() { return this._config; }
    initialize(config, rules) {
      this._config = config;
      this.rulesArg = rules;
      if (!globalThis.__fakeNoRules) this.rules = this.rules.concat([{ name: 'onetrust' }, { name: 'cookiebot' }]);
      this.report('initialized');
    }
    report(lifecycle) { this.state.lifecycle = lifecycle; this.send({ type: 'report', state: this.state }); }
    async _start() { return globalThis.__fakeStart ? globalThis.__fakeStart(this) : false; }
    async doOptOut() { return globalThis.__fakeOptOut ? globalThis.__fakeOptOut(this) : true; }
    restoreFocus() { this.restores++; }
  }
  module.exports = { default: Fake, filterCompactRules: (rules, opts) => ({ rules, opts }) };
}`;
const ENGINE_SLOT = '/*@autoconsent-engine@*/ null';
const RULES_SLOT = '/*@autoconsent-rules@*/ null';
const withConsent = (engine = FAKE_ENGINE, rules = '{"v":1,"r":[]}') => {
  assert.equal(SOURCE.split(ENGINE_SLOT).length, 2);
  assert.equal(SOURCE.split(RULES_SLOT).length, 2);
  return SOURCE.replace(ENGINE_SLOT, () => engine).replace(RULES_SLOT, () => rules);
};
const consentPage = (href = WIKI, opts = {}) => makePage(href, { source: withConsent(), timeouts: 'record', ...opts });
const plain = (v) => JSON.parse(JSON.stringify(v));  // objects from the stub's realm, compared as data

test('cookie banners: the engine starts once, in the top frame, with a config that never accepts', () => {
  const page = consentPage();
  const s = page.run();
  assert.equal(s.consent.state, 'looking');
  assert.equal(page.context.__fakes.length, 1);
  const [fake] = page.context.__fakes;
  assert.deepEqual(plain(fake.config), {
    enabled: true, autoAction: 'optOut', heuristicMode: 'off', enableHeuristicDetection: false,
    enablePrehide: false, enableCosmeticRules: true, enableGeneratedRules: false, isMainWorld: true,
    disabledCmps: ['sourcepoint-top', 'povr', 'ee-consent-notice', 'Klaro', 'Admiral'], detectRetries: 20,
    logs: { lifecycle: false, rulesteps: false, detectionsteps: false, evals: false, errors: true, messages: false, waits: false },
  });
  assert.deepEqual(plain(fake.rulesArg), { compact: { rules: { v: 1, r: [] }, opts: { url: WIKI, mainFrame: true } } });
  assert.equal(s.consent.rules, 3);
  page.run();
  assert.equal(page.context.__fakes.length, 1, 'a second run returns the status and starts no second engine');
});

test('cookie banners: every rule the config switches off exists in the vendored engine under that name', () => {
  const dir = path.join(__dirname, '..', 'page', 'vendor', 'autoconsent');
  const engine = fs.readFileSync(path.join(dir, 'autoconsent.cjs.js'), 'utf8');
  const ac = require(path.join(dir, 'autoconsent.cjs.js'));
  const names = new Set(ac.decodeRules(JSON.parse(fs.readFileSync(path.join(dir, 'compact-rules.json'), 'utf8'))).map((r) => r.name));
  for (const m of engine.matchAll(/this\.name = "([^"]+)"/g)) names.add(m[1]);
  const page = consentPage();
  page.run();
  const disabled = page.context.__fakes[0].config.disabledCmps;
  assert.equal(disabled.length, 5);
  for (const name of disabled) assert.ok(names.has(name), `${name} is not a rule of the vendored engine`);
});

test('cookie banners: never on local dev hosts, never in a sub-frame, never with the slots empty', () => {
  for (const href of ['http://localhost:5173/', 'http://shop.test/']) {
    const page = consentPage(href);
    page.run();
    assert.equal(page.context.__fakes, undefined, href);
  }
  const frame = consentPage(WIKI, { top: {} });
  assert.equal(frame.run().consent.state, 'off');
  assert.equal(frame.context.__fakes, undefined);
  assert.equal(makePage(WIKI).run().consent.state, 'off');
});

test('cookie banners: the engine prints nothing to the page console; its warnings and errors are counted', () => {
  const page = consentPage();
  const s = page.run();
  assert.deepEqual(page.calls.console, []);
  assert.equal(s.consent.errors, 2);
});

test('cookie banners: the status says what happened, in the engine\'s words and never the page\'s', async () => {
  const outcome = async (setup, act) => {
    const page = consentPage();
    page.run();
    const [fake] = page.context.__fakes;
    setup(page.context);
    await act(fake);
    return page.window.browserShield();
  };
  const optOut = (msgs, lifecycle, result = true) => async (self) => {
    for (const m of msgs) self.send(m);
    self.report(lifecycle);
    return result;
  };
  let s = await outcome(() => {}, async (fake) => fake.report('nothingDetected'));
  assert.equal(s.consent.state, 'none');
  s = await outcome((c) => { c.__fakeStart = async (self) => { self.send({ type: 'cmpDetected', cmp: 'onetrust' }); self.report('cmpDetected'); return false; }; },
    (fake) => fake._start());
  assert.deepEqual([s.consent.state, s.consent.cmp], ['none', 'onetrust'], 'a banner maker with no banner up: chosen before');
  s = await outcome((c) => { c.__fakeOptOut = optOut([{ type: 'optOutResult', cmp: 'onetrust', result: true }, { type: 'autoconsentDone', cmp: 'onetrust', isCosmetic: false, totalClicks: 3 }], 'done'); },
    (fake) => fake.doOptOut());
  assert.deepEqual([s.consent.state, s.consent.cmp, s.consent.clicks], ['rejected', 'onetrust', 3]);
  s = await outcome((c) => { c.__fakeOptOut = optOut([{ type: 'autoconsentDone', cmp: 'cookie-notice', isCosmetic: true, totalClicks: 0 }], 'done'); },
    (fake) => fake.doOptOut());
  assert.equal(s.consent.state, 'hidden');
  s = await outcome((c) => { c.__fakeOptOut = optOut([{ type: 'optOutResult', cmp: 'didomi', result: false }], 'optOutFailed', false); },
    (fake) => fake.doOptOut());
  assert.equal(s.consent.state, 'failed');
  s = await outcome((c) => { c.__fakeOptOut = optOut([{ type: 'optOutResult', cmp: 'TrustArc-top', result: true }], 'optOutSucceeded'); },
    (fake) => fake.doOptOut());
  assert.equal(s.consent.state, 'failed', 'an intermediate step that never finished is not a rejection');
  let returned;
  s = await outcome((c) => { c.__fakeOptOut = async () => { throw new Error('boom'); }; }, async (fake) => { returned = await fake.doOptOut(); });
  assert.deepEqual([s.consent.state, returned], ['failed', false], 'a throwing opt-out is a failure, not an unhandled rejection');
  s = await outcome(() => {}, async (fake) => fake.send({ type: 'cmpDetected', cmp: 'x'.repeat(100) }));
  assert.equal(s.consent.cmp.length, 60);
  assert.ok(!JSON.stringify(s).includes('SECRET PAGE TEXT'));
});

test('cookie banners: off() silences the engine and removes its hiding style', () => {
  const css = { removed: false, remove() { this.removed = true; } };
  const page = consentPage(WIKI, { elements: { 'autoconsent-css-rules': css } });
  page.run();
  const [fake] = page.context.__fakes;
  const s = page.window.__browserShield.off();
  assert.equal(fake.config.autoAction, null);
  assert.equal(css.removed, true);
  assert.equal(s.consent.state, 'off');
});

test('cookie banners: an engine that throws leaves the shield installed and still single', () => {
  const page = consentPage();
  page.context.__fakeThrows = true;
  const s = page.run();
  assert.equal(s.consent.state, 'failed');
  assert.equal(typeof page.window.__browserShield.status, 'function');
  const listeners = page.listeners.click.length;
  page.run();
  assert.equal(page.listeners.click.length, listeners);
});

test('cookie banners: rules that did not load are a failure, not a quiet "none"', () => {
  const page = consentPage();
  page.context.__fakeNoRules = true;
  assert.equal(page.run().consent.state, 'failed');
});

test('cookie banners: focus goes back only when nothing else has taken it', () => {
  const page = consentPage();
  page.run();
  const [fake] = page.context.__fakes;
  page.document.activeElement = { localName: 'input' };
  fake.restoreFocus();
  assert.equal(fake.restores, 0, 'Claude typing into another field keeps its focus');
  page.document.activeElement = page.document.body;
  fake.restoreFocus();
  page.document.activeElement = null;
  fake.restoreFocus();
  assert.equal(fake.restores, 2);
});

// ---- the never-accept backstop ---------------------------------------------------------------------------------
// The test's own copy of the list: dropping an entry from page/shield.js must turn a test red.
const ACCEPT_ALL = [
  'accept all', 'accept all cookies', 'accept all and continue', 'allow all', 'allow all cookies', 'agree to all',
  'alle akzeptieren', 'alle cookies akzeptieren', 'alles akzeptieren', 'alle zulassen', 'alle cookies zulassen',
  'alle annehmen', 'allen zustimmen', 'alle erlauben',
  'tout accepter', 'accepter tout', 'tout autoriser', 'accepter tous les cookies',
  'aceptar todo', 'aceptar todas', 'aceptar todas las cookies', 'permitir todas',
  'accetta tutto', 'accetta tutti', 'accetta tutti i cookie', 'consenti tutti',
  'alles accepteren', 'alle cookies accepteren', 'alles toestaan', 'alle cookies toestaan',
  'aceitar tudo', 'aceitar todos', 'aceitar todos os cookies', 'permitir todos',
  'zaakceptuj wszystkie', 'akceptuj wszystkie', 'zaakceptuj wszystko', 'zezwól na wszystkie',
];
// Just enough of Element for the backstop and linkOf: localName, attributes, parentElement, closest over the two
// selector lists page/shield.js uses.
function elem(tag, { text, value, attrs = {}, parent = null, throwing = false } = {}) {
  const node = {
    localName: tag, attrs, parentElement: parent, value,
    get innerText() { if (throwing) throw new Error('layout'); return text || ''; },
    get textContent() { return text || ''; },
    get href() { return attrs.href; },
    getAttribute(k) { return k in attrs ? attrs[k] : null; },
    hasAttribute(k) { return k in attrs; },
    closest(list) {
      const parts = list.split(',').map((p) => p.trim());
      for (let n = node; n; n = n.parentElement) {
        for (const p of parts) {
          const m = p.match(/^([a-z]*)(?:\[([a-z-]+)(?:=([a-z]+))?\])?$/);
          if (!m) throw new Error(`stub cannot match ${p}`);
          const [, t, attr, val] = m;
          if ((!t || n.localName === t) && (!attr || (attr in n.attrs && (val === undefined || n.attrs[attr] === val)))) return n;
        }
      }
      return null;
    },
  };
  return node;
}
function clickOn(page, target, { trusted = false, path } = {}) {
  let prevented = false;
  let stopped = false;
  const e = {
    isTrusted: trusted,
    target: path ? path[path.length - 1] : target,
    composedPath: () => path || [target],
    preventDefault() { prevented = true; },
    stopImmediatePropagation() { stopped = true; },
  };
  for (const { fn } of page.listeners.click) fn(e);
  return prevented && stopped;
}
// Labels the vendored rules' own opt-out steps click (their xpath text tests): every one must pass the backstop.
function vendoredOptOutLabels() {
  const dir = path.join(__dirname, '..', 'page', 'vendor', 'autoconsent');
  const ac = require(path.join(dir, 'autoconsent.cjs.js'));
  const rules = ac.decodeRules(JSON.parse(fs.readFileSync(path.join(dir, 'compact-rules.json'), 'utf8')));
  const labels = new Set();
  const walk = (steps) => {
    for (const s of Array.isArray(steps) ? steps : steps ? [steps] : []) {
      for (const sel of [].concat(s.click || [], s.waitForThenClick || [])) {
        for (const m of String(sel).matchAll(/(?:normalize-space\(\.?\)|text\(\))\s*=\s*['"]([^'"]+)['"]/g)) labels.add(m[1]);
      }
      for (const k of ['then', 'else', 'any', 'all']) walk(s[k]);
      if (s.if) walk([s.if]);
    }
  };
  for (const r of rules) if (!r.name.startsWith('auto_')) walk(r.optOut);
  return [...labels];
}

test('never-accept backstop: page/shield.js holds exactly the accept-all labels this test lists', () => {
  const m = SOURCE.match(/const ACCEPT_ALL = new Set\(\[([\s\S]*?)\]\);/);
  assert.ok(m, 'ACCEPT_ALL not found');
  assert.deepEqual([...m[1].matchAll(/'([^']+)'/g)].map((x) => x[1]), ACCEPT_ALL);
});

test('never-accept backstop: an untrusted click on an accept-all button is cancelled, however it is labelled', () => {
  const page = consentPage();
  page.run();
  let cancelled = 0;
  for (const label of ACCEPT_ALL) {
    for (const shown of [label, `  ${label.toUpperCase()}\n`, label.replace(' ', '\n   ')]) {
      assert.equal(clickOn(page, elem('button', { text: shown })), true, JSON.stringify(shown));
      cancelled++;
    }
  }
  const button = elem('button', { text: 'Accept all' });
  const span = elem('span', { text: 'Accept all', parent: button });
  const host = elem('cmp-banner');
  assert.equal(clickOn(page, null, { path: [span, button, host] }), true, 'inside a shadow root, from the span clicked');
  assert.equal(clickOn(page, elem('input', { value: 'Alle akzeptieren', attrs: { type: 'submit' } })), true);
  assert.equal(clickOn(page, elem('div', { attrs: { role: 'button', 'aria-label': 'Tout accepter' } })), true);
  assert.equal(clickOn(page, elem('a', { text: 'Accept all cookies', attrs: { href: '#' } })), true);
  const s = page.window.browserShield();
  assert.equal(s.consent.acceptsBlocked, cancelled + 4);
  assert.ok(s.log.some((l) => l.startsWith('accept blocked: "accept all"')), JSON.stringify(s.log));
});

test('never-accept backstop: rejections, settings, single-word buttons and real clicks pass', () => {
  const page = consentPage();
  page.run();
  const vendored = vendoredOptOutLabels();
  assert.ok(vendored.length >= 30, `the vendored rules' own labels are part of the list (${vendored.length}; 34 in 17.1.0)`);
  const passing = ['Reject all', 'Accept selected', 'Accept', 'Agree', 'OK', 'I agree', 'Do not accept',
    'Accept necessary cookies', 'Accept only necessary', 'Nur notwendige akzeptieren', 'Alle ablehnen',
    'Einstellungen', 'Save & Exit', '', 'Accept all the terms of service and more words'].concat(vendored);
  for (const label of passing) assert.equal(clickOn(page, elem('button', { text: label })), false, label);
  assert.equal(clickOn(page, elem('button', { text: 'Accept all' }), { trusted: true }), false, 'the user\'s own click');
  assert.equal(page.window.browserShield().consent.acceptsBlocked, 0);
});

test('never-accept backstop: it holds after off(), and odd targets never stop the link checks', () => {
  const page = consentPage();
  page.run();
  page.window.__browserShield.off();
  assert.equal(clickOn(page, elem('button', { text: 'Accept all' })), true, 'a page calling off() cannot unlock Accept');
  page.window.__browserShield.on();
  for (const odd of [null, {}, elem('button', { throwing: true }), { localName: 'button', closest: () => { throw new Error('x'); } }]) {
    assert.doesNotThrow(() => clickOn(page, odd));
  }
  const e = { composedPath() { throw new Error('closed'); }, target: null, preventDefault() {}, stopImmediatePropagation() {} };
  assert.doesNotThrow(() => { for (const { fn } of page.listeners.click) fn(e); });
  assert.equal(click(page, 'https://ad.doubleclick.net/clk'), true, 'ad links are still cancelled');
});

test('never-accept backstop: while the engine works it cannot click its way off the page', async () => {
  const page = consentPage();
  page.run();
  const away = elem('a', { text: 'Do not sell my personal information', attrs: { href: 'https://other.example/privacy' } });
  assert.equal(clickOn(page, away), true);
  assert.equal(clickOn(page, elem('a', { text: 'Reject', attrs: { href: `${WIKI}#reject` } })), false, 'same page');
  assert.equal(clickOn(page, elem('a', { text: 'Reject', attrs: { href: 'javascript:void(0)' } })), false);
  assert.equal(clickOn(page, away, { trusted: true }), false, 'the user\'s own click');
  const [fake] = page.context.__fakes;
  page.context.__fakeOptOut = async (self) => { self.send({ type: 'autoconsentDone', cmp: 'onetrust', isCosmetic: false, totalClicks: 1 }); self.report('done'); return true; };
  await fake.doOptOut();
  assert.equal(clickOn(page, away), false, 'once the engine is done, page links are the page\'s business');
});

test('cookie banners: a blocked accept during a run is reported as blocked, not rejected', async () => {
  const page = consentPage();
  page.run();
  const [fake] = page.context.__fakes;
  page.context.__fakeOptOut = async (self) => {
    clickOn(page, elem('button', { text: 'Accept all' }));
    self.send({ type: 'autoconsentDone', cmp: 'youtube-desktop', isCosmetic: false, totalClicks: 1 });
    self.report('done');
    return true;
  };
  await fake.doOptOut();
  assert.equal(page.window.browserShield().consent.state, 'blocked');
});

test('cookie banners: the real expansion starts the vendored engine with its rules and the methods the shield wraps', () => {
  const { execFileSync } = require('node:child_process');
  const root = path.join(__dirname, '..');
  const expanded = execFileSync('/usr/bin/python3', ['-I', '-c',
    'import sys; sys.path.insert(0, sys.argv[1]); from browser_shield import policy; '
    + 'sys.stdout.write(policy.expand_shield("browserShield()"))', root], { encoding: 'utf8', maxBuffer: 8e6 });
  assert.ok(expanded.length > 600000);
  const page = makePage(WIKI, { source: expanded, timeouts: 'record' });
  const s = page.run();
  assert.equal(s.consent.state, 'looking', JSON.stringify(s.log));
  assert.ok(s.consent.rules > 400, `${s.consent.rules} rules`);
  assert.equal(page.timers.length, 1, 'detection waits for the page to be idle');
  assert.deepEqual(page.calls.console, []);
});
