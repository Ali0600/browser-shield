// page/shield.js against a stub page: no browser, no DOM library. Run: node --test tests/shield.test.js
// The live check (a real YouTube pre-roll and mid-roll) is in README "Verify"; this pins the logic between runs.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'page', 'shield.js'), 'utf8');

function makePage(href, { player = null, signs = [] } = {}) {
  const u = new URL(href);
  const listeners = {};
  const navListeners = [];
  const intervals = [];
  const calls = { open: [], fetch: [], beacon: [], exec: [] };
  const appended = [];
  const el = (tag) => ({ tagName: tag, id: '', textContent: '', remove() { this.removed = true; } });
  const document = {
    head: { appendChild: (n) => appended.push(n) },
    documentElement: { appendChild: (n) => appended.push(n) },
    createElement: el,
    getElementById: (id) => (id === 'movie_player' ? player : null),
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
  const context = {
    window, document, location: window.location, navigator, XMLHttpRequest, URL, DOMException, Promise,
    setInterval: (fn) => intervals.push(fn), setTimeout: (fn) => fn(),
    MutationObserver: class { observe() {} }, Number, String, Boolean, Math, Object, Array, JSON, Set, TypeError, Error,
  };
  Object.assign(window, { XMLHttpRequest, navigator });
  vm.createContext(context);
  const originals = { open: window.open, alert: window.alert, confirm: window.confirm, fetch: window.fetch, xhrOpen: XMLHttpRequest.prototype.open };
  return {
    run: () => vm.runInContext(SOURCE, context),
    window, calls, listeners, navListeners, intervals, appended, context, originals,
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
  assert.equal(s.version, 3);
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
