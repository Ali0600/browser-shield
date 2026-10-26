// browser-shield page script: Claude's ad and harm blocker for one page. Run it once per page load through the
// browser's javascript_tool. With the guard hook installed, the text `browserShield()` expands to this file; on a page
// where the shield already runs, `browserShield()` returns its status. A full navigation clears it.
//
// What it does, all inside this page and nowhere else:
//   YouTube: skips each in-stream ad the moment it starts (mutes it, seeks it to its end), hides display ads, and
//            reports YouTube's ad-blocker notice, error screens and stalls; on a notice it stands down for good and
//            never hides or dismisses it. On YouTube it replaces no built-in function (YouTube watches for that).
//   Any other site: hides common ad slots, cancels clicks, navigations and form posts to ad, app-launch and download
//            targets, refuses cross-origin pop-ups, answers alert/confirm/prompt/print and "Leave site?" without a
//            dialog, denies notification, location, camera, clipboard, fullscreen and keyboard-lock requests, and drops
//            requests to ad-network hosts.
// It leaves local dev hosts (localhost, 127.x, *.localhost, *.test) alone: the app under test must behave as built.
(() => {
  const VERSION = 2;
  const w = window;
  if (w.__browserShield) return w.__browserShield.status();

  const host = location.hostname.toLowerCase();
  if (/^(localhost|127(\.\d{1,3}){3}|\[::1\])$|\.(localhost|test)$/.test(host) && !w.__browserShieldOnLocal) {
    return { skipped: `local dev host ${host}: the page under test is left alone` };
  }

  // Ad networks and ad-click redirectors. lists/ad-hosts.txt holds the same list for the guard hook (a test pins it).
  const AD_HOSTS = [
    '2mdn.net', 'adnxs.com', 'adsrvr.org', 'adservice.google.com', 'amazon-adsystem.com', 'casalemedia.com',
    'criteo.com', 'criteo.net', 'doubleclick.net', 'exoclick.com', 'googleadservices.com', 'googlesyndication.com',
    'googletagservices.com', 'mgid.com', 'moatads.com', 'outbrain.com', 'popads.net', 'propellerads.com',
    'pubmatic.com', 'revcontent.com', 'rubiconproject.com', 'taboola.com', 'teads.tv',
  ];
  const DOWNLOAD_EXT = /\.(dmg|pkg|mpkg|exe|msi|bat|cmd|scr|ps1|sh|command|tool|scpt|applescript|workflow|terminal|jar|deb|rpm|apk|ipa|iso|img|bin|run|appimage|xpi|crx|zip|rar|7z|tar|gz|tgz|bz2|xz)$/i;
  const SAFE_SCHEMES = new Set(['http:', 'https:', 'about:', 'javascript:', 'mailto:']);
  const isYouTube = /(^|\.)youtube(-nocookie)?\.com$/.test(host);

  const state = {
    version: VERSION, host, youtube: isYouTube, enabled: true, enforcement: false, enforcementSign: '',
    adsSkipped: 0, clicksBlocked: 0, navigationsBlocked: 0, popupsBlocked: 0, requestsBlocked: 0,
    dialogsAnswered: 0, permissionsDenied: 0, log: [],
  };
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
  const linkVerdict = (href, isDownload) => {
    const u = toURL(href);
    if (!u) return '';
    if (!SAFE_SCHEMES.has(u.protocol)) return `opens ${u.protocol} (an app or a local resource)`;
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
    if (listed(u.hostname)) return `ad click through ${u.hostname}`;
    const last = u.pathname.split('/').pop();
    if (isDownload || DOWNLOAD_EXT.test(last)) return `download ${last || u.hostname}`;
    return '';
  };
  const cancel = (e, counter, kind, why) => {
    e.preventDefault();
    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
    state[counter]++;
    note(kind, why);
  };

  // ---- clicks and form posts to ad, app-launch and download targets (listeners, not patches: safe on YouTube) ----
  const onClick = (e) => {
    if (!state.enabled) return;
    const a = e.target && e.target.closest && e.target.closest('a[href], area[href]');
    if (!a) return;
    const why = linkVerdict(a.href, a.hasAttribute('download'));
    if (why) cancel(e, 'clicksBlocked', 'click blocked', why);
  };
  w.addEventListener('click', onClick, true);
  w.addEventListener('auxclick', onClick, true);
  w.addEventListener('submit', (e) => {
    if (!state.enabled || !e.target) return;
    const why = linkVerdict(e.target.action || location.href, false);
    if (why) cancel(e, 'clicksBlocked', 'form post blocked', why);
  }, true);

  // ---- navigations the page starts itself (redirect chains, location.href = ..., downloads), where supported ----
  if (w.navigation && w.navigation.addEventListener) {
    w.navigation.addEventListener('navigate', (e) => {
      if (!state.enabled || !e.cancelable) return;
      const why = e.downloadRequest != null
        ? `download ${e.downloadRequest || 'file'}`
        : linkVerdict(e.destination && e.destination.url, false);
      if (why) cancel(e, 'navigationsBlocked', 'navigation blocked', why);
    });
  }

  // ---- everything below replaces built-in functions, so it stays off YouTube -------------------------------------
  if (!isYouTube) {
    const realOpen = w.open;
    w.open = function (url, ...rest) {
      const u = toURL(url === undefined ? 'about:blank' : url);
      if (!state.enabled || (u && u.origin === location.origin)) return realOpen.call(this, url, ...rest);
      state.popupsBlocked++;
      note('pop-up blocked', url);
      return null;
    };

    // Dialogs that would hang an automated session.
    const answer = (kind, value) => function (msg) {
      state.dialogsAnswered++;
      note(kind, msg === undefined ? '' : msg);
      return value;
    };
    w.alert = answer('alert', undefined);
    w.confirm = answer('confirm (answered Cancel)', false);
    w.prompt = answer('prompt (answered Cancel)', null);
    w.print = answer('print', undefined);
    w.addEventListener('beforeunload', (e) => {
      if (state.enabled) e.stopImmediatePropagation();
    }, true);

    // Permission prompts, clipboard writes (the "paste this into Terminal" lure), fullscreen and keyboard traps.
    const deny = (what) => { state.permissionsDenied++; note('denied', what); };
    const refused = (what) => () => { deny(what); return Promise.reject(new DOMException(`${what} denied by browser-shield`, 'NotAllowedError')); };
    if (w.Notification) {
      w.Notification.requestPermission = () => { deny('notifications'); return Promise.resolve('denied'); };
    }
    if (navigator.geolocation) {
      const refuse = (ok, fail) => { deny('location'); if (fail) setTimeout(() => fail({ code: 1, message: 'denied by browser-shield' })); return 0; };
      navigator.geolocation.getCurrentPosition = refuse;
      navigator.geolocation.watchPosition = refuse;
    }
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      navigator.mediaDevices.getUserMedia = refused('camera/microphone');
    }
    if (navigator.clipboard) {
      navigator.clipboard.writeText = refused('clipboard write');
      navigator.clipboard.write = refused('clipboard write');
    }
    if (document.execCommand) {
      const realExec = document.execCommand.bind(document);
      document.execCommand = (cmd, ...rest) => {
        if (state.enabled && /^(copy|cut)$/i.test(String(cmd))) { deny('clipboard write'); return false; }
        return realExec(cmd, ...rest);
      };
    }
    if (navigator.registerProtocolHandler) navigator.registerProtocolHandler = () => deny('protocol handler');
    if (w.Element && w.Element.prototype) {
      w.Element.prototype.requestFullscreen = refused('fullscreen');
      w.Element.prototype.requestPointerLock = () => deny('pointer lock');
    }
    if (navigator.keyboard && navigator.keyboard.lock) navigator.keyboard.lock = refused('keyboard lock');

    // Requests to ad networks.
    const realFetch = w.fetch;
    w.fetch = function (input, init) {
      const url = typeof input === 'string' ? input : input && input.url;
      const u = url && toURL(url);
      if (state.enabled && u && listed(u.hostname)) {
        state.requestsBlocked++;
        note('request blocked', u.hostname);
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return realFetch.apply(this, arguments);
    };
    const realXOpen = XMLHttpRequest.prototype.open;
    const realXSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      const u = toURL(url);
      this.__browserShieldBlocked = Boolean(u && listed(u.hostname));
      return realXOpen.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function (...args) {
      if (state.enabled && this.__browserShieldBlocked) {
        state.requestsBlocked++;
        note('request blocked', 'XMLHttpRequest');
        return this.abort();
      }
      return realXSend.apply(this, args);
    };
    if (navigator.sendBeacon) {
      const realBeacon = navigator.sendBeacon.bind(navigator);
      navigator.sendBeacon = (url, data) => {
        const u = toURL(url);
        if (state.enabled && u && listed(u.hostname)) { state.requestsBlocked++; return true; }
        return realBeacon(url, data);
      };
    }
  }

  // ---- ad slots out of sight (and out of reach of a stray click) ----------------------------------------------------
  const GENERIC_SLOTS = [
    'ins.adsbygoogle', 'iframe[src*="doubleclick.net"]', 'iframe[src*="googlesyndication.com"]',
    'iframe[id^="google_ads_iframe"]', 'div[id^="div-gpt-ad"]', 'div[id^="taboola-"]', '.OUTBRAIN',
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
  // seekTo past the ad's progress is used instead (uBlock Origin's approach).
  let inAd = false;
  let mutedBefore = false;
  let lastAd = '';
  const SIGNS = [
    ['ytd-enforcement-message-view-model', 'ad-blocker notice'],
    ['a[href*="support.google.com/youtube/answer/3037019"]', '"Experiencing interruptions?" link'],
  ];
  const call = (o, name) => { try { return o && typeof o[name] === 'function' ? o[name]() : undefined; } catch { return undefined; } };
  const watchEnforcement = () => {
    for (const [sel, what] of SIGNS) {
      if (document.querySelector(sel)) {
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
    const player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
    if (!player) return;
    const video = player.querySelector('video.html5-main-video') || player.querySelector('video');
    const mode = String((call(player, 'getStatsForNerds') || {}).debug_info || '');
    const ssap = mode.startsWith('SSAP, AD');
    const showing = player.classList.contains('ad-showing') || ssap;
    if (showing && !inAd) { inAd = true; mutedBefore = video ? video.muted : false; }
    if (!showing) {
      if (inAd && video) video.muted = mutedBefore;
      inAd = false;
      return;
    }
    if (video) video.muted = true;
    if (ssap) {
      const p = call(player, 'getProgressState');
      if (p && p.duration > 0 && p.current < p.duration - 0.25 && typeof player.seekTo === 'function') {
        const key = `ssap ${Math.round(p.duration)}`;
        if (key !== lastAd) { lastAd = key; state.adsSkipped++; note('ad skipped', `${Math.round(p.duration)} s (stitched)`); }
        player.seekTo(p.duration);
      }
      return;
    }
    if (!video) return;
    const d = video.duration;
    const length = Number(((call(player, 'getPlayerResponse') || {}).videoDetails || {}).lengthSeconds) || 0;
    if (length && d >= length - 1) {
      // An "ad" clip as long as the video itself is not an ad clip: never seek it.
      const skip = player.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button');
      if (skip) skip.click();
      return;
    }
    if (Number.isFinite(d) && d > 0 && video.currentTime < d - 0.25) {
      const key = `${d.toFixed(2)}@${video.src}`;
      if (key !== lastAd) { lastAd = key; state.adsSkipped++; note('ad skipped', `${Math.round(d)} s`); }
      video.currentTime = Math.max(0, d - 0.1);
    }
  };
  let watched = null;
  const watch = () => {
    const player = document.getElementById('movie_player');
    if (player && player !== watched) {
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
    const player = document.getElementById('movie_player');
    const stats = call(player, 'getStatsForNerds') || {};
    const playability = ((call(player, 'getPlayerResponse') || {}).playabilityStatus || {});
    return {
      adPlaying: Boolean(player && (player.classList.contains('ad-showing') || String(stats.debug_info || '').startsWith('SSAP, AD'))),
      playability: playability.status ? `${playability.status}${playability.reason ? ': ' + playability.reason : ''}` : undefined,
      stalled: stats.buffer_health_seconds === '0.00 s' && stats.resolution === '0x0',
    };
  };
  const status = () => Object.assign({
    version: state.version, host: state.host, youtube: state.youtube, enabled: state.enabled,
    enforcement: state.enforcement, enforcementSign: state.enforcementSign || undefined, adsSkipped: state.adsSkipped,
    clicksBlocked: state.clicksBlocked, navigationsBlocked: state.navigationsBlocked, popupsBlocked: state.popupsBlocked,
    requestsBlocked: state.requestsBlocked, dialogsAnswered: state.dialogsAnswered, permissionsDenied: state.permissionsDenied,
  }, isYouTube ? youtubeStatus() : {}, { log: state.log.slice(-10) });
  w.__browserShield = {
    status,
    off() { state.enabled = false; style.remove(); return status(); },
  };
  w.browserShield = status;
  note('active', location.href);
  return status();
})()
