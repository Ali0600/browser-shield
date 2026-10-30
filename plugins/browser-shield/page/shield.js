// browser-shield page script: Claude's ad and harm blocker for one page. Run it once per page load through the
// browser's javascript_tool. With the guard hook installed, the text `browserShield()` expands to this file; on a page
// where the shield already runs, `browserShield()` returns its status. A full navigation clears it.
//
// What it does, all inside this page and nowhere else:
//   YouTube: skips each in-stream ad the moment it starts (mutes it, seeks it to its end), hides display ads, and
//            reports YouTube's ad-blocker notice, error screens and stalls; on a notice it stands down for good and
//            never hides or dismisses it. On YouTube it replaces no built-in function (YouTube watches for that).
//   Any other site: hides common ad slots, cancels clicks, navigations and form posts to ad-click, app-launch and
//            download targets, refuses pop-ups nobody clicked for, answers alert/confirm/prompt/print and "Leave
//            site?" without a dialog, denies notification, location, camera, clipboard, fullscreen and keyboard-lock
//            requests, and drops requests to ad-network hosts.
// It leaves local dev hosts (localhost, 127.x, *.localhost, *.test) alone: the app under test must behave as built.
// It is not a security boundary: it runs in the page's own world, after the page's code (the guard hook is the
// boundary); it stops accidents and ads, and a hostile page can undo it.
(() => {
  const VERSION = 4;
  const w = window;
  const prior = w.__browserShield;
  if (prior) {
    const s = prior.status();
    if ((s.version || 0) >= VERSION) {
      if (!s.enabled && prior.on) prior.on();
      return prior.status();
    }
    prior.off();  // an older version stands down (its wrappers pass through) and this one takes over
  }

  const host = location.hostname.toLowerCase();
  if (/^(localhost|127(\.\d{1,3}){3}|\[::1\])$|\.(localhost|test)$/.test(host) && !w.__browserShieldOnLocal) {
    return { skipped: `local dev host ${host}: the page under test is left alone` };
  }

  // Cookie banners: DuckDuckGo's autoconsent (page/vendor/autoconsent/, MPL-2.0, unmodified). The guard hook fills
  // these two slots when it expands browserShield() for the Browser pane; left empty (this file as it is: the unit
  // tests, the autorun mod, Claude in Chrome), consent handling is off.
  const AUTOCONSENT = /*@autoconsent-engine@*/ null;
  const CONSENT_RULES = /*@autoconsent-rules@*/ null;

  // Ad networks and ad-click redirectors. lists/ad-hosts.txt holds the same list for the guard hook (a test pins it).
  const AD_HOSTS = [
    '2mdn.net', 'adnxs.com', 'adsrvr.org', 'adservice.google.com', 'amazon-adsystem.com', 'casalemedia.com',
    'criteo.com', 'criteo.net', 'doubleclick.net', 'exoclick.com', 'googleadservices.com', 'googlesyndication.com',
    'googletagservices.com', 'mgid.com', 'moatads.com', 'outbrain.com', 'popads.net', 'propellerads.com',
    'pubmatic.com', 'revcontent.com', 'rubiconproject.com', 'taboola.com', 'teads.tv',
  ];
  // The same ad-click paths and link wrappers as AD_PATHS and REDIRECTORS in browser_shield/policy.py (a test pins them).
  const AD_PATHS = [
    [/(^|\.)google\.[a-z.]+$/, /^\/(aclk|pagead\/)/],
    [/(^|\.)youtube\.com$/, /^\/pagead\//],
  ];
  const REDIRECTORS = [
    [/(^|\.)google\.[a-z.]+$/, /^\/url$/, ['q', 'url']],
    [/(^|\.)youtube\.com$/, /^\/redirect$/, ['q']],
    [/^l[m]?\.facebook\.com$/, /^\/l\.php$/, ['u']],
    [/^l\.instagram\.com$/, /^\/$/, ['u']],
    [/^out\.reddit\.com$/, /^\//, ['url']],
    [/^steamcommunity\.com$/, /^\/linkfilter\/?$/, ['url', 'u']],
  ];
  const DOWNLOAD_EXT = /\.(dmg|pkg|mpkg|exe|msi|bat|cmd|scr|ps1|sh|command|tool|scpt|applescript|workflow|terminal|jar|deb|rpm|apk|ipa|iso|img|bin|run|appimage|xpi|crx|zip|rar|7z|tar|gz|tgz|bz2|xz)$/i;
  // Installers and executables: a download even from the page's own site (a repo page named acme.sh is not one).
  const INSTALLER_EXT = /\.(dmg|pkg|mpkg|exe|msi|bat|cmd|scr|ps1|command|tool|scpt|applescript|workflow|terminal|jar|deb|rpm|apk|ipa|iso|img|appimage|xpi|crx)$/i;
  const SAFE_SCHEMES = new Set(['http:', 'https:', 'about:', 'javascript:']);
  const isYouTube = /(^|\.)youtube(-nocookie)?\.com$/.test(host);

  const state = {
    version: VERSION, host, youtube: isYouTube, enabled: true, enforcement: false, enforcementSign: '',
    adsSkipped: 0, clicksBlocked: 0, navigationsBlocked: 0, popupsBlocked: 0, requestsBlocked: 0,
    dialogsAnswered: 0, permissionsDenied: 0, acceptsBlocked: 0, log: [],
  };
  // The cookie engine's progress (below): looking, then none, rejected, hidden, blocked or failed; off without it.
  const consent = { state: 'off', cmp: undefined, lifecycle: undefined, clicks: 0, rules: 0, errors: 0, blockedDuringRun: false };
  const note = (kind, detail) => {
    state.log.push(`${kind}: ${String(detail).slice(0, 140)}`);
    if (state.log.length > 30) state.log.shift();
  };
  const listed = (hostname) => {
    const h = String(hostname).toLowerCase().replace(/\.$/, '');
    return AD_HOSTS.some((d) => h === d || h.endsWith('.' + d));
  };
  const toURL = (u) => { try { return new URL(String(u), location.href); } catch { return null; } };
  // Why following this link would be harmful, or '' when it is fine.
  const linkVerdict = (href, isDownload, depth = 0) => {
    const u = toURL(href);
    if (!u) return '';
    if (u.protocol === 'blob:' || u.protocol === 'data:') {
      return `a file the page built itself (${u.protocol}), the way pages smuggle a download past filters`;
    }
    if (!SAFE_SCHEMES.has(u.protocol)) return `opens ${u.protocol} (an app or a local resource)`;
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
    const h = u.hostname.toLowerCase();
    if (listed(h)) return `ad click through ${h}`;
    for (const [hr, pr] of AD_PATHS) if (hr.test(h) && pr.test(u.pathname)) return `ad click through ${h}${u.pathname}`;
    if (depth < 2) {
      for (const [hr, pr, params] of REDIRECTORS) {
        if (!hr.test(h) || !pr.test(u.pathname)) continue;
        for (const p of params) {
          for (const target of u.searchParams.getAll(p)) {
            const why = linkVerdict(target, false, depth + 1);
            if (why) return `${why} (behind the link wrapper ${h})`;
          }
        }
      }
    }
    const last = u.pathname.split('/').pop();
    const sameOrigin = u.origin === location.origin;
    if (isDownload) return `download ${last || h}`;
    if (sameOrigin ? INSTALLER_EXT.test(last) : DOWNLOAD_EXT.test(last)) return `download ${last}`;
    return '';
  };
  const cancel = (e, counter, kind, why) => {
    e.preventDefault();
    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
    state[counter]++;
    note(kind, why);
  };
  // The nodes an event passed through, innermost first and through open shadow roots; [] when there is no path.
  const pathOf = (e) => {
    try { return (e && e.composedPath && e.composedPath()) || []; } catch { return []; }
  };
  // The link an event came from, through shadow roots, with SVG <a> elements read by their attribute.
  const linkOf = (e) => {
    const path = pathOf(e);
    const nodes = path.length ? path : [e && e.target];
    for (const n of nodes) {
      const tag = n && n.localName;
      if (tag === 'a' || tag === 'area') {
        const href = typeof n.href === 'string' ? n.href
          : (n.getAttribute && (n.getAttribute('href') || n.getAttributeNS && n.getAttributeNS('http://www.w3.org/1999/xlink', 'href')));
        if (href) return { href, download: Boolean(n.hasAttribute && n.hasAttribute('download')) };
      }
    }
    try {
      const a = e.target && e.target.closest && e.target.closest('a[href], area[href]');
      return a ? { href: a.href, download: a.hasAttribute('download') } : null;
    } catch {
      return null;
    }
  };

  // ---- the never-accept backstop: no script presses "accept all" on a cookie banner ----------------------------
  // Whole labels, exactly: the cookie engine's own opt-outs press "Agree", "OK" or "Accept" after unticking every box,
  // and "Do not accept" or "Accept necessary" are refusals. tests/shield.test.js holds its own copy of this list.
  const ACCEPT_ALL = new Set([
    'accept all', 'accept all cookies', 'accept all and continue', 'allow all', 'allow all cookies', 'agree to all',
    'alle akzeptieren', 'alle cookies akzeptieren', 'alles akzeptieren', 'alle zulassen', 'alle cookies zulassen',
    'alle annehmen', 'allen zustimmen', 'alle erlauben',
    'tout accepter', 'accepter tout', 'tout autoriser', 'accepter tous les cookies',
    'aceptar todo', 'aceptar todas', 'aceptar todas las cookies', 'permitir todas',
    'accetta tutto', 'accetta tutti', 'accetta tutti i cookie', 'consenti tutti',
    'alles accepteren', 'alle cookies accepteren', 'alles toestaan', 'alle cookies toestaan',
    'aceitar tudo', 'aceitar todos', 'aceitar todos os cookies', 'permitir todos',
    'zaakceptuj wszystkie', 'akceptuj wszystkie', 'zaakceptuj wszystko', 'zezwól na wszystkie',
  ]);
  const BUTTONISH = 'button, a, [role=button], input[type=submit], input[type=button]';
  const labelOf = (node) => {
    try {
      const el = (node && node.closest && node.closest(BUTTONISH)) || node;
      const text = el && (el.innerText || el.textContent || el.value || (el.getAttribute && el.getAttribute('aria-label')));
      return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    } catch {
      return '';
    }
  };
  // A script's click (never the user's) on an accept-all button is cancelled, the shield on or off; so is a script's
  // click on a link off the page while the cookie engine is at work (its heuristic can meet "Do not sell" links).
  const backstop = (e) => {
    if (!e || e.isTrusted) return false;
    const path = pathOf(e);
    const label = labelOf(path.length ? path[0] : e.target);
    if (ACCEPT_ALL.has(label)) {
      if (consent.state === 'looking') consent.blockedDuringRun = true;
      cancel(e, 'acceptsBlocked', 'accept blocked', `"${label}"`);
      return true;
    }
    if (consent.state !== 'looking') return false;
    const link = linkOf(e);
    const u = link && toURL(link.href);
    if (u && (u.protocol === 'http:' || u.protocol === 'https:')
        && (u.origin !== location.origin || u.pathname !== new URL(location.href).pathname)) {
      cancel(e, 'clicksBlocked', 'click blocked', `the cookie engine would have left the page for ${u.origin}`);
      return true;
    }
    return false;
  };

  // ---- clicks and form posts to ad, app-launch and download targets (listeners, not patches: safe on YouTube) ----
  const onClick = (e) => {
    if (backstop(e)) return;
    if (!state.enabled) return;
    const link = linkOf(e);
    if (!link) return;
    const why = linkVerdict(link.href, link.download);
    if (why) cancel(e, 'clicksBlocked', 'click blocked', why);
  };
  w.addEventListener('click', onClick, true);
  w.addEventListener('auxclick', onClick, true);
  w.addEventListener('submit', (e) => {
    if (!state.enabled || !e.target) return;
    const action = e.target.getAttribute ? e.target.getAttribute('action') : e.target.action;
    if (!action) return;  // posts back to this page
    const why = linkVerdict(e.target.action || action, false);
    if (why) cancel(e, 'clicksBlocked', 'form post blocked', why);
  }, true);

  // ---- navigations the page starts itself (redirect chains, location.href = ..., downloads), where supported ----
  if (w.navigation && w.navigation.addEventListener) {
    w.navigation.addEventListener('navigate', (e) => {
      if (!state.enabled || !e.cancelable || (e.destination && e.destination.sameDocument)) return;
      const why = e.downloadRequest != null
        ? `download ${e.downloadRequest || 'file'}`
        : linkVerdict(e.destination && e.destination.url, false);
      if (why) cancel(e, 'navigationsBlocked', 'navigation blocked', why);
    });
  }

  // ---- everything below replaces built-in functions, so it stays off YouTube; each passes through after off() ----
  const replace = (obj, name, make) => {
    if (!obj || typeof obj[name] !== 'function') return;
    const real = obj[name];
    const fake = make(real);
    try {
      obj[name] = function (...args) { return state.enabled ? fake.apply(this, args) : real.apply(this, args); };
    } catch { /* a read-only built-in stays as it is */ }
  };
  if (!isYouTube) {
    replace(w, 'open', (real) => function (url, target, ...rest) {
      const t = String(target || '').toLowerCase();
      if (t === '_self' || t === '_top' || t === '_parent') {
        const why = url ? linkVerdict(url, false) : '';
        if (!why) return real.call(this, url, target, ...rest);
        state.navigationsBlocked++;
        note('navigation blocked', why);
        return null;
      }
      const u = toURL(url === undefined || url === '' ? 'about:blank' : url);
      const clicked = Boolean(navigator.userActivation && navigator.userActivation.isActive);
      const blank = !u || u.href === 'about:blank';
      if ((u && u.origin === location.origin) || (blank && clicked)) return real.call(this, url, target, ...rest);
      if (u && !blank && clicked && !linkVerdict(u.href, false)) return real.call(this, url, target, ...rest);
      state.popupsBlocked++;
      note('pop-up blocked', url === undefined ? '(no address)' : url);
      return null;
    });

    // Dialogs that would hang an automated session.
    const answer = (kind, value) => () => function (msg) {
      state.dialogsAnswered++;
      note(kind, msg === undefined ? '' : msg);
      return value;
    };
    replace(w, 'alert', answer('alert', undefined));
    replace(w, 'confirm', answer('confirm (answered Cancel)', false));
    replace(w, 'prompt', answer('prompt (answered Cancel)', null));
    replace(w, 'print', answer('print', undefined));
    // "Leave site?": the page's own beforeunload work still runs (a draft gets saved); only the dialog is dropped.
    const BUE = w.BeforeUnloadEvent && w.BeforeUnloadEvent.prototype;
    if (BUE) {
      replace(BUE, 'preventDefault', () => function () { note('dialog', 'Leave site? (dropped)'); });
      const desc = Object.getOwnPropertyDescriptor(BUE, 'returnValue');
      if (desc && desc.set) {
        try {
          Object.defineProperty(BUE, 'returnValue', {
            configurable: true, get: desc.get, set(v) { if (!state.enabled) desc.set.call(this, v); },
          });
        } catch { /* left as it is */ }
      }
    }

    // Permission prompts, clipboard writes (the "paste this into Terminal" lure), fullscreen and keyboard traps.
    const deny = (what) => { state.permissionsDenied++; note('denied', what); };
    const refused = (what) => () => function () {
      deny(what);
      return Promise.reject(new DOMException(`${what} denied by browser-shield`, 'NotAllowedError'));
    };
    replace(w.Notification, 'requestPermission', () => function (cb) {
      deny('notifications');
      if (typeof cb === 'function') setTimeout(() => cb('denied'));
      return Promise.resolve('denied');
    });
    const geoError = { code: 1, message: 'denied by browser-shield', PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 };
    const refuseGeo = () => function (ok, fail) { deny('location'); if (fail) setTimeout(() => fail(geoError)); return 0; };
    replace(navigator.geolocation, 'getCurrentPosition', refuseGeo);
    replace(navigator.geolocation, 'watchPosition', refuseGeo);
    replace(navigator.mediaDevices, 'getUserMedia', refused('camera/microphone'));
    replace(navigator.clipboard, 'writeText', refused('clipboard write'));
    replace(navigator.clipboard, 'write', refused('clipboard write'));
    replace(document, 'execCommand', (real) => function (cmd, ...rest) {
      if (/^(copy|cut)$/i.test(String(cmd))) { deny('clipboard write'); return false; }
      return real.call(this, cmd, ...rest);
    });
    replace(navigator, 'registerProtocolHandler', () => function () { deny('protocol handler'); });
    if (w.Element && w.Element.prototype) {
      replace(w.Element.prototype, 'requestFullscreen', refused('fullscreen'));
      replace(w.Element.prototype, 'requestPointerLock', refused('pointer lock'));
    }
    replace(navigator.keyboard, 'lock', refused('keyboard lock'));

    // Requests to ad networks.
    const urlOf = (input) => (input && typeof input === 'object' && 'url' in input ? input.url : input == null ? '' : String(input));
    replace(w, 'fetch', (real) => function (input, init) {
      const u = toURL(urlOf(input));
      if (u && listed(u.hostname)) {
        state.requestsBlocked++;
        note('request blocked', u.hostname);
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return real.call(this, input, init);
    });
    replace(XMLHttpRequest.prototype, 'open', (real) => function (method, url, ...rest) {
      const u = toURL(url);
      this.__browserShieldBlocked = Boolean(u && listed(u.hostname));
      return real.call(this, method, url, ...rest);
    });
    replace(XMLHttpRequest.prototype, 'send', (real) => function (...args) {
      if (this.__browserShieldBlocked) {
        state.requestsBlocked++;
        note('request blocked', 'XMLHttpRequest');
        return this.abort();
      }
      return real.apply(this, args);
    });
    replace(navigator, 'sendBeacon', (real) => function (url, data) {
      const u = toURL(url);
      if (u && listed(u.hostname)) { state.requestsBlocked++; return true; }
      return real.call(this, url, data);
    });
  }

  // ---- ad slots out of sight (and out of reach of a stray click) ----------------------------------------------------
  // Fandom's (the game wikis research agents read): its own <fandom-ad> element and wrappers, measured 2026-10-26.
  const GENERIC_SLOTS = [
    'ins.adsbygoogle', 'iframe[src*="doubleclick.net"]', 'iframe[src*="googlesyndication.com"]',
    'iframe[id^="google_ads_iframe"]', 'div[id^="div-gpt-ad"]', 'div[id^="taboola-"]', '.OUTBRAIN',
    'fandom-ad', '.fandom-ad-wrapper', '.fandom-ad-label', 'div[class^="top_leaderboard-odyssey-wrapper"]',
  ];
  // EasyList's youtube.com hiding rules and AdGuard's, as of 2026-10-26. Not #player-ads: uBlock Origin deliberately
  // un-hides it. The in-player ad module stays visible; the skipper deals with it.
  const YOUTUBE_SLOTS = [
    '#masthead-ad', 'ytd-ad-slot-renderer', 'ytd-in-feed-ad-layout-renderer', 'ytd-display-ad-renderer',
    'ytd-rich-item-renderer:has(> #content > ytd-ad-slot-renderer)', 'ytd-rich-item-renderer:has(ytd-display-ad-renderer)',
    'ytd-search-pyv-renderer', 'ytd-promoted-sparkles-web-renderer', 'ytd-promoted-sparkles-text-search-renderer',
    'ytd-promoted-video-renderer', 'ytd-companion-slot-renderer', 'ytd-action-companion-ad-renderer',
    'ytd-banner-promo-renderer', 'ytd-statement-banner-renderer', 'ytd-video-masthead-ad-v3-renderer',
    'ytd-primetime-promo-renderer', 'ytd-merch-shelf-renderer', '#shopping-timely-shelf', 'yt-overlay-product-sticker',
    'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-ads"]',
    '.ytp-ad-overlay-container', '.ytp-ad-image-overlay',
  ];
  const style = document.createElement('style');
  style.id = 'browser-shield-style';
  style.textContent = (isYouTube ? GENERIC_SLOTS.concat(YOUTUBE_SLOTS) : GENERIC_SLOTS)
    .map((s) => `${s} { display: none !important; }`).join('\n');
  (document.head || document.documentElement).appendChild(style);

  // ---- YouTube's in-stream ads --------------------------------------------------------------------------------------
  // Measured 2026-10-26 in the Browser pane: YouTube inserted ads client-side (debug_info "USDAI, ..."), each its own
  // clip in the same <video> while #movie_player has `ad-showing`; seeking the clip to its end cleared a 135 s
  // pre-roll and a 15 s + 30 s mid-roll pod in under 2 s. Under server-stitched ads (debug_info "SSAP, AD") the video
  // holds ad and content as one timeline, so seeking the <video> would skip the whole video: there the player's own
  // seekTo is used, and only while the ad's progress and the video's clock agree (a pre-roll); what getProgressState
  // reports during a stitched mid-roll is unmeasured, so such an ad is muted and left to end.
  let inAd = false;
  let mutedBefore = false;
  let lastAd = '';
  let waitingNoted = '';
  const SIGNS = [
    ['ytd-enforcement-message-view-model', 'ad-blocker notice'],
    [['#movie_player', 'ytd-popup-container', 'tp-yt-paper-dialog', '#error-screen', 'yt-playability-error-supported-renderers']
      .map((c) => `${c} a[href*="support.google.com/youtube/answer/3037019"]`).join(', '), '"Experiencing interruptions?" link'],
  ];
  const call = (o, name) => { try { return o && typeof o[name] === 'function' ? o[name]() : undefined; } catch { return undefined; } };
  const playerVideo = () => {
    const player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
    return [player, player && (player.querySelector('video.html5-main-video') || player.querySelector('video'))];
  };
  const standDown = () => {
    const [, video] = playerVideo();
    if (inAd && video) video.muted = mutedBefore;
    inAd = false;
  };
  const watchEnforcement = () => {
    for (const [sel, what] of SIGNS) {
      if (document.querySelector(sel)) {
        standDown();
        state.enforcement = true;
        state.enforcementSign = what;
        note('stopped', `YouTube shows its ${what}; the shield stands down and leaves it in place`);
        return true;
      }
    }
    return false;
  };
  const tick = () => {
    if (!state.enabled || !isYouTube || state.enforcement || watchEnforcement()) return;
    const [player, video] = playerVideo();
    if (!player) return;
    const mode = String((call(player, 'getStatsForNerds') || {}).debug_info || '');
    const ssap = mode.startsWith('SSAP, AD');
    const showing = player.classList.contains('ad-showing') || ssap;
    if (!showing) {
      standDown();
      return;
    }
    const begin = () => { if (!inAd) { inAd = true; mutedBefore = video ? video.muted : false; } if (video) video.muted = true; };
    if (mode.startsWith('SSAP')) {
      const p = call(player, 'getProgressState');
      begin();
      if (p && p.duration > 0 && video && Math.abs(p.current - video.currentTime) < 0.5 && p.current < p.duration - 0.25
          && typeof player.seekTo === 'function') {
        const key = `ssap ${Math.round(p.duration)}`;
        if (key !== lastAd) { lastAd = key; state.adsSkipped++; note('ad skipped', `${Math.round(p.duration)} s (stitched)`); }
        player.seekTo(p.duration);
      } else if (waitingNoted !== mode) {
        waitingNoted = mode;
        note('ad muted', 'a stitched ad whose position is not the video clock: left to end');
      }
      return;
    }
    if (!video) return;
    const d = video.duration;
    const length = Number(((call(player, 'getPlayerResponse') || {}).videoDetails || {}).lengthSeconds) || 0;
    const skip = player.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button');
    if (!length || Math.abs(d - length) <= 1.5) {
      // No length to compare with, or a clip as long as the video itself: it may be the video. Never seek it.
      if (skip) skip.click();
      return;
    }
    begin();
    if (Number.isFinite(d) && d > 0 && video.currentTime < d - 0.25) {
      const key = `${d.toFixed(2)}@${video.src}`;
      if (key !== lastAd) { lastAd = key; state.adsSkipped++; note('ad skipped', `${Math.round(d)} s`); }
      video.currentTime = Math.max(0, d - 0.1);
    }
  };
  let watched = null;
  const watch = () => {
    const [player] = playerVideo();
    if (player && player !== watched && player.id === 'movie_player') {
      watched = player;
      new MutationObserver(tick).observe(player, { attributes: true, attributeFilter: ['class'] });
    }
    const video = player && player.querySelector('video');
    if (video && !video.__browserShield) {
      video.__browserShield = true;
      for (const ev of ['loadedmetadata', 'durationchange', 'playing', 'timeupdate']) video.addEventListener(ev, tick);
    }
    tick();
  };
  if (isYouTube) {
    watch();
    setInterval(watch, 500);
    document.addEventListener('yt-navigate-finish', watch);
  }

  const youtubeStatus = () => {
    const [player] = playerVideo();
    const stats = call(player, 'getStatsForNerds') || {};
    const playability = ((call(player, 'getPlayerResponse') || {}).playabilityStatus || {});
    return {
      adPlaying: Boolean(player && (player.classList.contains('ad-showing') || String(stats.debug_info || '').startsWith('SSAP, AD'))),
      playability: playability.status ? `${playability.status}${playability.reason ? ': ' + playability.reason : ''}` : undefined,
      stalled: stats.buffer_health_seconds === '0.00 s' && stats.resolution === '0x0',
    };
  };
  // ---- cookie banners: reject, never accept ----------------------------------------------------------------------
  // The engine runs in the top frame only, once per page, with a module object of its own and a console that prints
  // nothing. Its config, each value from reading the engine (docs/DECISIONS.md, 2026-10-30): opt out only; no
  // heuristic for banners no rule knows (it follows "Do not sell" links, answers foreign age gates "no", and in
  // 'tier2' presses Accept); nothing hidden before a decision (prehiding makes YouTube's dialog backdrop transparent);
  // no generated rules (they click by position); and five rules off: sourcepoint-top (hides walls, pay-or-OK ones
  // included, storing no choice), povr (goes back in history), ee-consent-notice (submits the Accept form), Klaro (its
  // fallback ticks unticked purposes) and Admiral (clicks any button whose text names a purpose). Pinned by
  // tests/shield.test.js.
  const CONSENT_CONFIG = {
    enabled: true, autoAction: 'optOut', heuristicMode: 'off', enableHeuristicDetection: false,
    enablePrehide: false, enableCosmeticRules: true, enableGeneratedRules: false, isMainWorld: true,
    disabledCmps: ['sourcepoint-top', 'povr', 'ee-consent-notice', 'Klaro', 'Admiral'], detectRetries: 20,
    logs: { lifecycle: false, rulesteps: false, detectionsteps: false, evals: false, errors: true, messages: false, waits: false },
  };
  let engine = null;
  const ruleName = (v) => (v == null ? undefined : String(v).slice(0, 60));
  // Only a run still looking settles, so the first outcome stands (off() and a failure to start are final).
  const settle = (to) => {
    if (consent.state !== 'looking') return;
    consent.state = to;
    note('consent', `${to}${consent.cmp ? ` (${consent.cmp})` : ''}`);
  };
  // Messages from the engine. Its constructor sends the first ones before it returns, so this never touches `engine`;
  // what it keeps is the engine's own vocabulary (rule names, lifecycle words), never text from the page.
  const onConsentMessage = (msg) => {
    if (!msg || typeof msg !== 'object') return Promise.resolve();
    if (msg.type === 'report' && msg.state) {
      consent.lifecycle = ruleName(msg.state.lifecycle);
      if (msg.state.lifecycle === 'nothingDetected') settle('none');
    } else if (msg.type === 'cmpDetected' || msg.type === 'popupFound') {
      consent.cmp = ruleName(msg.cmp);
    } else if (msg.type === 'optOutResult' && !msg.result) {
      settle('failed');
    } else if (msg.type === 'autoconsentDone') {
      consent.cmp = ruleName(msg.cmp);
      consent.clicks = Number(msg.totalClicks) || 0;
      settle(consent.blockedDuringRun ? 'blocked' : msg.isCosmetic ? 'hidden' : 'rejected');
    } else if (msg.type === 'autoconsentError') {
      consent.errors++;
    }
    return Promise.resolve();
  };
  const startConsent = () => {
    if (!AUTOCONSENT || !CONSENT_RULES || w.top !== w) return;
    consent.state = 'looking';
    const mod = { exports: {} };
    const quiet = new Proxy({}, { get: (_, name) => (name === 'error' || name === 'warn' ? () => { consent.errors++; } : () => {}) });
    AUTOCONSENT(mod, mod.exports, quiet);
    const compact = mod.exports.filterCompactRules(CONSENT_RULES, { url: location.href, mainFrame: true });
    engine = new mod.exports.default(onConsentMessage);
    const start = engine._start;
    const optOut = engine.doOptOut;
    const restore = engine.restoreFocus;
    if (typeof start !== 'function' || typeof optOut !== 'function' || typeof restore !== 'function') {
      throw new TypeError('this engine lacks a method the shield wraps (_start, doOptOut, restoreFocus)');
    }
    // Detection ends with a banner maker found but no banner up: the visitor chose before.
    engine._start = async function (...args) {
      try {
        return await start.apply(this, args);
      } catch (err) {
        consent.errors++;
        settle('failed');
        return false;
      } finally {
        if (consent.lifecycle === 'cmpDetected' || consent.lifecycle === 'nothingDetected') settle('none');
      }
    };
    // An opt-out that throws, or stops at an intermediate step, is a failure (and never an unhandled rejection).
    engine.doOptOut = async function (...args) {
      try {
        return await optOut.apply(this, args);
      } catch (err) {
        consent.errors++;
        return false;
      } finally {
        settle('failed');
      }
    };
    // Focus goes back to where it was only if nothing else took it meanwhile (Claude may be typing elsewhere).
    engine.restoreFocus = function (...args) {
      const active = document.activeElement;
      if (active && active !== document.body) {
        this.focusedElement = undefined;
        return undefined;
      }
      return restore.apply(this, args);
    };
    const builtIn = engine.rules.length;
    engine.initialize(CONSENT_CONFIG, { compact });
    consent.rules = engine.rules.length;
    if (consent.rules <= builtIn) {
      engine.config.autoAction = null;
      settle('failed');
      note('consent', 'the cookie rules did not load');
    }
  };
  // The engine has no stop: with no action left to take it only watches, and its hiding rules go.
  const stopConsent = () => {
    if (!engine) return;
    try { engine.config.autoAction = null; } catch { /* never initialized: nothing to silence */ }
    const css = document.getElementById && document.getElementById('autoconsent-css-rules');
    if (css) css.remove();
    consent.state = 'off';
  };

  const status = () => Object.assign({
    version: state.version, host: state.host, youtube: state.youtube, enabled: state.enabled,
    enforcement: state.enforcement, enforcementSign: state.enforcementSign || undefined, adsSkipped: state.adsSkipped,
    clicksBlocked: state.clicksBlocked, navigationsBlocked: state.navigationsBlocked, popupsBlocked: state.popupsBlocked,
    requestsBlocked: state.requestsBlocked, dialogsAnswered: state.dialogsAnswered, permissionsDenied: state.permissionsDenied,
    consent: {
      state: consent.state, cmp: consent.cmp, lifecycle: consent.lifecycle, clicks: consent.clicks, rules: consent.rules,
      acceptsBlocked: state.acceptsBlocked, errors: consent.errors,
    },
  }, isYouTube ? youtubeStatus() : {}, prior ? { upgradedFrom: prior.status().version } : {}, { log: state.log.slice(-10) });
  w.__browserShield = {
    status,
    off() { standDown(); stopConsent(); state.enabled = false; style.remove(); return status(); },
    on() { state.enabled = true; (document.head || document.documentElement).appendChild(style); return status(); },
  };
  w.browserShield = status;
  // After the shield is in place, so a consent failure can never leave it half installed.
  try {
    startConsent();
  } catch (err) {
    consent.state = 'failed';
    note('consent', `the engine did not start (${err && err.name})`);
  }
  note('active', location.href);
  return status();
})()
