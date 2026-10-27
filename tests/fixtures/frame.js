// Frame grabs from a YouTube watch page, for an agent that studies gameplay videos (a copy of the owner's helper).
// Run this whole file once per page load through the Browser pane's javascript_tool, then:
//   await scoutFrame(95)  seeks to 95 s, pauses, paints that frame over the page; take a screenshot next
//   await scoutStoryboard(n) paints storyboard sheet n (many small frames, each labelled m:ss) to find moments fast
//   scoutHide()           removes the painted frame so the page can be clicked again
// A hidden Browser pane can show no video frame in a screenshot, so the frame is drawn to a canvas and shown as an <img>,
// which does render. Under the frame a strip reads "<video id> @ m:ss": check it on every screenshot, since a hidden
// pane has twice returned the previous grab. Every call returns within ~15 s; the tool gives up at 45 s.
window.scoutFrame = async (t) => {
  const v = document.querySelector('video');
  if (!v) return { error: 'no video on this page' };
  if (document.querySelector('.ad-showing')) {
    document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button')?.click();
    return { ad: true, secondsLeft: Math.round(v.duration - v.currentTime) };
  }
  v.muted = true;
  v.pause();
  if (Math.abs(v.currentTime - t) > 0.2) {
    await new Promise((resolve) => {
      const done = () => { v.removeEventListener('seeked', done); resolve(); };
      v.addEventListener('seeked', done);
      v.currentTime = t;
      setTimeout(done, 8000);
    });
  }
  for (let i = 0; i < 20 && v.readyState < 2; i++) await new Promise((r) => setTimeout(r, 250));
  const strip = Math.round(v.videoHeight / 18);
  const c = document.createElement('canvas');
  c.width = v.videoWidth;
  c.height = v.videoHeight + strip;
  const g = c.getContext('2d');
  g.drawImage(v, 0, 0);
  const d = g.getImageData(0, 0, v.videoWidth, v.videoHeight).data;
  let s = 0, q = 0, n = 0;
  for (let i = 0; i < d.length; i += 4 * 97) { const y = (d[i] + d[i + 1] + d[i + 2]) / 3; s += y; q += y * y; n++; }
  const mean = s / n;
  const spread = Math.sqrt(Math.max(0, q / n - mean * mean));
  const at = Math.floor(v.currentTime);
  const label = `${new URLSearchParams(location.search).get('v')} @ ${Math.floor(at / 60)}:${String(at % 60).padStart(2, '0')}`;
  g.fillStyle = '#000';
  g.fillRect(0, v.videoHeight, c.width, strip);
  g.fillStyle = '#fff';
  g.font = `${Math.round(strip * 0.7)}px monospace`;
  g.textBaseline = 'middle';
  g.fillText(label, Math.round(strip * 0.4), v.videoHeight + strip / 2);
  document.getElementById('scoutframe')?.remove();
  const img = document.createElement('img');
  img.id = 'scoutframe';
  img.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;object-fit:contain;z-index:2147483647;background:#000';
  img.src = c.toDataURL('image/jpeg', 0.92);
  // decode() never settles in a background tab; the image still renders once appended.
  await Promise.race([img.decode().catch(() => {}), new Promise((r) => setTimeout(r, 1500))]);
  document.body.appendChild(img);
  await new Promise((r) => setTimeout(r, 400));
  return { label, duration: Math.round(v.duration), size: `${v.videoWidth}x${v.videoHeight}`, blank: spread < 4 };
};
// YouTube's storyboard: the small preview frames the seek bar shows, 25 a sheet (about 10 s apart), each labelled m:ss.
// One screenshot of a sheet shows where the menus and big moments are; then scoutFrame the times worth keeping.
//   await scoutStoryboard(0)  paints sheet 0 (the first minutes) over the page; returns how many sheets there are
window.scoutStoryboard = async (sheet = 0) => {
  const response = document.getElementById('movie_player')?.getPlayerResponse?.() || window.ytInitialPlayerResponse;
  const spec = response?.storyboards?.playerStoryboardSpecRenderer?.spec;
  const duration = Number(response?.videoDetails?.lengthSeconds) || document.querySelector('video')?.duration || 0;
  if (!spec) return { error: 'this video has no storyboard' };
  const parts = spec.split('|');
  const levels = parts.slice(1).map((p, i) => {
    const [w, h, count, cols, rows, interval, name, sigh] = p.split('#');
    return { i, w: +w, h: +h, count: +count, cols: +cols, rows: +rows, interval: +interval, name, sigh };
  });
  // 160x90 cells, 25 a sheet, read well in one screenshot; else the largest size there is.
  const level = levels.find((l) => l.w === 160) || levels.filter((l) => l.name !== 'default').pop() || levels[levels.length - 1];
  const perSheet = level.cols * level.rows;
  const sheets = Math.ceil(level.count / perSheet);
  if (sheet < 0 || sheet >= sheets) return { error: `sheet must be 0 to ${sheets - 1}` };
  const name = level.name.replace('$M', String(sheet));
  const url = `${parts[0].replace('$L', String(level.i)).replace('$N', name)}&sigh=${level.sigh}`;
  const step = level.interval > 0 ? level.interval / 1000 : duration / level.count;
  document.getElementById('scoutframe')?.remove();
  const box = document.createElement('div');
  box.id = 'scoutframe';
  box.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#000;display:flex;align-items:center;justify-content:center';
  const grid = document.createElement('div');
  grid.style.cssText = `position:relative;aspect-ratio:${level.cols * level.w}/${level.rows * level.h};height:100%;max-width:100%`;
  const img = document.createElement('img');
  img.src = url;
  img.style.cssText = 'position:absolute;inset:0;width:100%;height:100%';
  grid.appendChild(img);
  for (let k = 0; k < perSheet && sheet * perSheet + k < level.count; k++) {
    const at = Math.floor((sheet * perSheet + k) * step);
    const tag = document.createElement('div');
    tag.textContent = `${Math.floor(at / 60)}:${String(at % 60).padStart(2, '0')}`;
    tag.style.cssText = `position:absolute;left:${((k % level.cols) / level.cols) * 100}%;top:${(Math.floor(k / level.cols) / level.rows) * 100}%;background:#000c;color:#fff;font:bold 13px monospace;padding:1px 3px`;
    grid.appendChild(tag);
  }
  box.appendChild(grid);
  document.body.appendChild(box);
  await Promise.race([img.decode().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
  return { sheet, sheets, perSheet, secondsApart: Math.round(step * 10) / 10, size: `${level.w}x${level.h}` };
};
window.scoutHide = () => { document.getElementById('scoutframe')?.remove(); return 'hidden'; };
'scout helpers ready';
