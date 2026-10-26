// browser-shield page script: Claude's ad and harm blocker for one page. Run it once per page load through the
// browser's javascript_tool. With the guard hook installed, the text `browserShield()` expands to this file; on a page
// where the shield already runs, `browserShield()` returns its status. A full navigation clears it.
//
// What it does, all inside this page and nowhere else:
//   YouTube: skips each in-stream ad the moment it starts (mutes it, presses Skip, seeks it to its end), hides
//            display ads, and stops for good if YouTube shows its ad-blocker enforcement notice (never hides it).
//   Any site: hides common ad slots, cancels clicks on ad-click links, app-launching links and download links,
//            refuses cross-origin pop-ups, answers alert/confirm/prompt and "Leave site?" without a dialog, denies
//            notification, location, camera and microphone requests, and drops requests to ad-network hosts.
// It leaves local dev hosts (localhost, 127.x, *.localhost, *.test) alone: the app under test must behave as built.
(() => {
  const VERSION = 1;
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
    version: VERSION, host, youtube: isYouTube, enabled: true, enforcement: false,
    adsSkipped: 0, skipClicks: 0, clicksBlocked: 0, popupsBlocked: 0, requestsBlocked: 0,
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

  // ---- clicks on ad, app-launch and download links ------------------------------------------------------------
  const onClick = (e) => {
    if (!state.enabled) return;
    const a = e.target && e.target.closest && e.target.closest('a[href], area[href]');
    if (!a) return;
    const why = linkVerdict(a.href, a.hasAttribute('download'));
    if (!why) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    state.clicksBlocked++;
    note('click blocked', why);
  };
  w.addEventListener('click', onClick, true);
  w.addEventListener('auxclick', onClick, true);

  // ---- pop-ups ----------------------------------------------------------------------------------------------------
  const realOpen = w.open;
  w.open = function (url, ...rest) {
    const u = toURL(url === undefined ? 'about:blank' : url);
    if (!state.enabled || (u && u.origin === location.origin)) return realOpen.call(this, url, ...rest);
    state.popupsBlocked++;
    note('pop-up blocked', url);
    return null;
  };

  // ---- dialogs that would hang an automated session ---------------------------------------------------------------
  const answer = (kind, value) => function (msg) {
    state.dialogsAnswered++;
    note(kind, msg === undefined ? '' : msg);
    return value;
  };
  w.alert = answer('alert', undefined);
  w.confirm = answer('confirm (answered Cancel)', false);
  w.prompt = answer('prompt (answered Cancel)', null);
  w.addEventListener('beforeunload', (e) => {
    if (state.enabled) e.stopImmediatePropagation();
  }, true);

  // ---- permission prompts ------------------------------------------------------------------------------------------
  const deny = (what) => { state.permissionsDenied++; note('permission denied', what); };
  if (w.Notification) {
    w.Notification.requestPermission = () => { deny('notifications'); return Promise.resolve('denied'); };
  }
  if (navigator.geolocation) {
    const refuse = (ok, fail) => { deny('location'); if (fail) setTimeout(() => fail({ code: 1, message: 'denied by browser-shield' })); return 0; };
    navigator.geolocation.getCurrentPosition = refuse;
    navigator.geolocation.watchPosition = refuse;
  }
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    navigator.mediaDevices.getUserMedia = () => { deny('camera/microphone'); return Promise.reject(new DOMException('denied by browser-shield', 'NotAllowedError')); };
  }

  // ---- requests to ad networks (not on YouTube, whose own ad requests trigger its ad-blocker detection) -----------
  if (!isYouTube) {
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
  const YOUTUBE_SLOTS = [
    '#masthead-ad', '#player-ads', 'ytd-ad-slot-renderer', 'ytd-in-feed-ad-layout-renderer',
    'ytd-banner-promo-renderer', 'ytd-statement-banner-renderer', 'ytd-video-masthead-ad-v3-renderer',
    'ytd-primetime-promo-renderer', 'ytd-companion-slot-renderer', 'ytd-action-companion-ad-renderer',
    'ytd-promoted-sparkles-web-renderer', 'ytd-promoted-video-renderer', 'ytd-search-pyv-renderer',
    'ytd-display-ad-renderer', 'ytd-merch-shelf-renderer', 'ytd-player-legacy-desktop-watch-ads-renderer',
    'ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-ads"]',
    'ytd-rich-item-renderer:has(ytd-ad-slot-renderer)', '.ytp-ad-overlay-container', '.ytp-ad-image-overlay',
  ];
  const style = document.createElement('style');
  style.id = 'browser-shield-style';
  style.textContent = (isYouTube ? GENERIC_SLOTS.concat(YOUTUBE_SLOTS) : GENERIC_SLOTS)
    .map((s) => `${s} { display: none !important; }`).join('\n');
  (document.head || document.documentElement).appendChild(style);

  // ---- YouTube's in-stream ads --------------------------------------------------------------------------------------
  // Measured 2026-10-26: a 135 s pre-roll and a two-ad mid-roll pod each cleared in under a second by seeking the ad
  // to its end. Mutes during the ad and gives the video its own mute state back afterwards.
  let inAd = false;
  let mutedBefore = false;
  let lastAd = '';
  const ENFORCEMENT = 'ytd-enforcement-message-view-model';
  const tick = () => {
    if (!state.enabled || !isYouTube || state.enforcement) return;
    if (document.querySelector(ENFORCEMENT)) {
      state.enforcement = true;
      note('stopped', 'YouTube shows its ad-blocker notice; the shield stands down and leaves the notice in place');
      return;
    }
    const player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
    if (!player) return;
    const video = player.querySelector('video.html5-main-video') || player.querySelector('video');
    const showing = player.classList.contains('ad-showing');
    if (showing && !inAd) { inAd = true; mutedBefore = video ? video.muted : false; }
    if (!showing) {
      if (inAd && video) video.muted = mutedBefore;
      inAd = false;
      return;
    }
    const skip = player.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button');
    if (skip) { skip.click(); state.skipClicks++; }
    if (!video) return;
    video.muted = true;
    const d = video.duration;
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

  const status = () => ({
    version: state.version, host: state.host, youtube: state.youtube, enabled: state.enabled,
    enforcement: state.enforcement, adsSkipped: state.adsSkipped, skipClicks: state.skipClicks,
    adPlaying: isYouTube ? Boolean(document.querySelector('#movie_player.ad-showing')) : undefined,
    clicksBlocked: state.clicksBlocked, popupsBlocked: state.popupsBlocked, requestsBlocked: state.requestsBlocked,
    dialogsAnswered: state.dialogsAnswered, permissionsDenied: state.permissionsDenied, log: state.log.slice(-10),
  });
  w.__browserShield = {
    status,
    off() { state.enabled = false; style.remove(); return status(); },
  };
  w.browserShield = status;
  note('active', location.href);
  return status();
})()
