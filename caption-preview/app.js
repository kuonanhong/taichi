import {activeCueIndex, archiveDraft, clock, cueErrors, exportVideo, isPending, seekTime, srt} from './caption-utils.js';

const $ = id => document.getElementById(id);
const state = {data: null, video: null, cues: [], player: null, playerReady: false, activeIndex: -1, generation: 0, editing: -1, requestedSeek: null};
const STORAGE = 'smartaction-bilingual-caption-preview-v1';
let stored = {}, revisionBackups = [];
try { stored = JSON.parse(localStorage.getItem(STORAGE) || '{}'); } catch {}
try { revisionBackups = JSON.parse(localStorage.getItem(STORAGE + '-backups') || '[]'); } catch {}
let apiPromise;

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}
function categoryKey(video) { return video.category || video.categoryKey || ''; }
function category(video) { return (state.data.categories || []).find(c => c.key === categoryKey(video)); }
function categoryTitle(video) { return video.categoryLabel || category(video)?.title_zh || categoryKey(video) || '其他'; }
function signature(video) { return JSON.stringify(video.cues || []); }
function preserveOldDraft(video, entry) {
  archiveDraft(revisionBackups, video, entry, new Date().toISOString());
  $('backupStatus').hidden = false;
  $('backupStatus').textContent = `已保留 ${revisionBackups.length} 份舊版本校對稿；下載全部 JSON 可取回。`;
  try { localStorage.setItem(STORAGE + '-backups', JSON.stringify(revisionBackups)); return true; }
  catch { $('backupStatus').textContent += ' 儲存空間不足，請先下載 JSON；原稿不會被新版本覆寫。'; return false; }
}
function editedCues(video) {
  const record = stored[video.videoId];
  if (record && record.signature !== signature(video)) preserveOldDraft(video, record);
  return structuredClone(record && record.signature === signature(video) ? record.cues : (video.cues || []));
}
function persist() {
  const previous = stored[state.video.videoId];
  if (previous && previous.signature !== signature(state.video) && !preserveOldDraft(state.video, previous)) return;
  stored[state.video.videoId] = {signature: signature(state.video), cues: structuredClone(state.cues), reviewed: false};
  try { localStorage.setItem(STORAGE, JSON.stringify(stored)); } catch {
    $('playerStatus').textContent = '瀏覽器未能暫存修改，請下載校對 JSON 保存。';
  }
}
function renderLibrary() {
  const query = $('search').value.trim().toLocaleLowerCase(), selectedCategory = $('category').value;
  const list = state.data.videos.filter(video => (!selectedCategory || categoryKey(video) === selectedCategory) &&
    (!query || `${video.title_zh} ${video.title_en} ${video.videoId}`.toLocaleLowerCase().includes(query)));
  $('videoList').replaceChildren();
  $('filterCount').textContent = `顯示 ${list.length} / ${state.data.videos.length} 支影片`;
  for (const video of list) {
    const button = node('button', 'video-choice'); button.type = 'button'; button.dataset.video = video.videoId;
    button.setAttribute('aria-current', String(video.videoId === state.video?.videoId));
    button.append(node('span', 'name', video.title_zh), node('span', 'translation', video.title_en));
    const cues = video.videoId === state.video?.videoId ? state.cues : editedCues(video);
    button.append(node('span', 'mini', video.asrStatus === 'not-started' ? '語音處理中' : `${cues.length} 段 · 待確認 ${cues.filter(isPending).length} 段`));
    button.addEventListener('click', () => selectVideo(video.videoId));
    $('videoList').append(button);
  }
  if (!list.length) $('videoList').append(node('p', 'empty', '沒有符合的影片，請調整搜尋或分類。'));
}
function renderStats() {
  const pending = state.cues.filter(isPending).length;
  $('videoStats').replaceChildren(node('span', 'stat', `字幕 ${state.cues.length} 段`), node('span', 'stat pending', `待確認 ${pending} 段`), node('span', 'stat', '尚未上傳 YouTube'));
  if (state.video.timingEstimated || state.cues.some(cue => cue.timingEstimated)) $('videoStats').append(node('span', 'stat', '時間碼待校對'));
}
function renderCues() {
  const list = $('cueList'); list.replaceChildren();
  state.cues.forEach((cue, index) => {
    const pending = isPending(cue), card = node('article', 'cue-card' + (pending ? ' pending' : ''));
    card.id = 'cue-' + index;
    const top = node('div', 'cue-top'), seek = node('button', 'seek-button', `${clock(cue.start)} – ${clock(cue.end)}`);
    seek.type = 'button'; seek.setAttribute('aria-label', `跳到第 ${index + 1} 句，${clock(cue.start)}`); seek.addEventListener('click', () => seekTo(cue.start));
    top.append(seek, node('span', 'badge' + (pending ? ' pending' : ''), pending ? '待確認' : '字幕草稿'));
    const zh = node('p', 'cue-zh', cue.zh || '待確認'); zh.lang = 'zh-Hant';
    const en = node('p', 'cue-en', cue.en || 'Pending confirmation'); en.lang = 'en';
    const bottom = node('div', 'cue-bottom'), edit = node('button', 'edit-button', '校對這句');
    edit.type = 'button'; edit.addEventListener('click', () => editCue(index));
    bottom.append(node('p', 'cue-reason', pending ? (cue.reason || '請核對原音') : '確認前仍保留為草稿'), edit);
    card.append(top, zh, en, bottom); list.append(card);
  });
  if (!state.cues.length) list.append(node('p', 'empty', '這支影片尚無字幕段落。'));
  state.activeIndex = -2; renderStats(); updatePosition(0, false);
}
function updatePosition(seconds, follow = true) {
  $('currentTime').textContent = clock(seconds);
  const next = activeCueIndex(state.cues, seconds);
  if (next === state.activeIndex) return;
  if (state.activeIndex >= 0) $('cue-' + state.activeIndex)?.classList.remove('active');
  state.activeIndex = next;
  const band = document.querySelector('.live-captions');
  if (next < 0) {
    const wholeVideoPending = Boolean(state.video.videoLevelPlaceholder);
    band.classList.add('pending'); $('cueState').textContent = '原音待確認 / Pending confirmation';
    $('liveZh').textContent = wholeVideoPending ? '尚無可靠字幕，待確認。' : '此時段尚無字幕，原音待確認。';
    $('liveEn').textContent = wholeVideoPending ? 'No reliable captions yet. Pending confirmation.' : 'No captions for this interval yet. Original audio pending confirmation.'; return;
  }
  const cue = state.cues[next], pending = isPending(cue), card = $('cue-' + next);
  band.classList.toggle('pending', pending); $('cueState').textContent = pending ? '待確認 / Pending confirmation' : '字幕草稿 / Draft';
  $('liveZh').textContent = cue.zh || '待確認'; $('liveEn').textContent = cue.en || 'Pending confirmation';
  card?.classList.add('active');
  if (follow && $('follow').checked && card) {
    const list = $('cueList'), top = card.offsetTop - list.offsetTop;
    if (list.scrollHeight > list.clientHeight && (top < list.scrollTop || top + card.offsetHeight > list.scrollTop + list.clientHeight)) list.scrollTo({top: Math.max(0, top - 30), behavior: 'smooth'});
  }
}
function selectVideo(id) {
  const video = state.data.videos.find(item => item.videoId === id); if (!video) return;
  state.generation++; state.playerReady = false; state.requestedSeek = null;
  try { state.player?.destroy(); } catch {} state.player = null;
  state.video = video; state.cues = editedCues(video);
  $('titleZh').textContent = video.title_zh; $('titleEn').textContent = video.title_en;
  $('videoNote').textContent = video.note_zh || '字幕為初稿，聽不清楚處保留待確認。';
  $('videoNoteEn').textContent = video.note_en || 'Draft captions. Unclear speech remains pending confirmation.';
  $('categoryName').textContent = categoryTitle(video); $('youtubeLink').href = `https://www.youtube.com/watch?v=${video.videoId}`;
  $('playerStatus').textContent = ''; $('speed').value = '1';
  const placeholder = node('div'); placeholder.id = 'playerPlaceholder';
  const load = node('button', '', '載入播放器 / Load player'); load.id = 'loadPlayer'; load.type = 'button'; load.addEventListener('click', loadPlayer);
  placeholder.append(node('p', '', '播放原有 YouTube 影片'), load, node('p', 'small', '載入後，按 YouTube 原生播放鈕開始。'));
  $('playerStage').replaceChildren(placeholder);
  renderCues(); renderLibrary();
  const url = new URL(location.href); url.searchParams.set('v', id); history.replaceState(null, '', url);
  if (matchMedia('(max-width:850px)').matches) $('libraryDetails').open = false;
}
function loadAPI() {
  if (window.YT?.Player) return Promise.resolve();
  if (apiPromise) return apiPromise;
  apiPromise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('播放器未能載入，請使用原 YouTube 連結。')), 15000);
    window.onYouTubeIframeAPIReady = () => { clearTimeout(timer); resolve(); };
    const script = document.createElement('script'); script.src = 'https://www.youtube.com/iframe_api'; script.async = true;
    script.onerror = () => { clearTimeout(timer); reject(Error('YouTube 播放器連線失敗。')); }; document.head.append(script);
  }).catch(error => { apiPromise = null; throw error; });
  return apiPromise;
}
async function loadPlayer() {
  if (state.player || $('loadPlayer')?.disabled) return;
  const generation = state.generation, videoId = state.video.videoId;
  if ($('loadPlayer')) $('loadPlayer').disabled = true;
  $('playerStatus').textContent = '正在載入原 YouTube 播放器…';
  try {
    await loadAPI(); if (generation !== state.generation) return;
    const frame = document.createElement('iframe'); frame.id = 'youtubePlayer'; frame.title = state.video.title_zh;
    frame.src = `https://www.youtube-nocookie.com/embed/${videoId}?enablejsapi=1&playsinline=1&origin=${encodeURIComponent(location.origin)}&rel=0`;
    frame.allow = 'autoplay; encrypted-media; accelerometer; gyroscope; picture-in-picture; fullscreen'; frame.allowFullscreen = true; frame.referrerPolicy = 'strict-origin-when-cross-origin';
    $('playerStage').replaceChildren(frame);
    state.player = new YT.Player(frame, {events: {
      onReady: () => { if (generation !== state.generation) return; state.playerReady = true; $('playerStatus').textContent = '播放器已就緒；按原生播放鈕開始。'; if (state.requestedSeek !== null) { const seconds = seekTime(state.requestedSeek, state.video.duration, state.player.getDuration()); state.player.cueVideoById({videoId, startSeconds: seconds}); updatePosition(seconds); state.requestedSeek = null; } },
      onError: event => { if (generation !== state.generation) return; $('playerStatus').textContent = `此瀏覽器暫時無法內嵌播放（${event.data}），請使用原 YouTube 連結。`; },
      onStateChange: () => { if (generation === state.generation) tick(); },
    }});
  } catch (error) {
    if (generation !== state.generation) return; $('playerStatus').textContent = error.message; if ($('loadPlayer')) $('loadPlayer').disabled = false;
  }
}
function seekTo(seconds) {
  seconds = seekTime(seconds, state.video.duration, state.playerReady ? state.player.getDuration() : undefined);
  if (!state.playerReady) { state.requestedSeek = seconds; loadPlayer(); updatePosition(seconds); return; }
  state.player.seekTo(seconds, true); updatePosition(seconds);
}
function tick() {
  if (!state.playerReady || document.hidden) return;
  try { updatePosition(state.player.getCurrentTime()); } catch {}
}
function download(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], {type})), anchor = document.createElement('a');
  anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function editCue(index) {
  state.editing = index; const cue = state.cues[index];
  $('editTime').textContent = `${clock(cue.start)} – ${clock(cue.end)}` + (cue.timingEstimated ? '（時間碼待校對）' : '');
  $('editZh').value = cue.zh || '待確認'; $('editEn').value = cue.en || 'Pending confirmation'; $('editDialog').showModal();
}
$('cancelEdit').addEventListener('click', () => $('editDialog').close());
$('editForm').addEventListener('submit', event => {
  event.preventDefault(); const cue = state.cues[state.editing];
  cue.zh = $('editZh').value.trim() || '待確認'; cue.en = $('editEn').value.trim() || 'Pending confirmation';
  cue.status = /待確認/.test(cue.zh) || /pending confirmation/i.test(cue.en) ? 'pending' : 'draft'; cue.reviewed = false;
  persist(); $('editDialog').close(); renderCues(); renderLibrary(); tick();
});
$('search').addEventListener('input', () => state.data && renderLibrary()); $('category').addEventListener('change', () => state.data && renderLibrary());
$('speed').addEventListener('change', () => {
  if (!state.playerReady) { $('playerStatus').textContent = '載入影片後可調整播放速度。'; return; }
  const wanted = Number($('speed').value), available = state.player.getAvailablePlaybackRates();
  if (available.includes(wanted)) state.player.setPlaybackRate(wanted); else $('playerStatus').textContent = '此影片不提供這個速度，請使用播放器可用的速度。';
});
$('displayLanguage').addEventListener('change', () => { document.body.classList.remove('display-zh', 'display-en'); if ($('displayLanguage').value !== 'both') document.body.classList.add('display-' + $('displayLanguage').value); });
for (const [button, language] of [['exportZh', 'zh'], ['exportEn', 'en']]) $(button).addEventListener('click', () => {
  try { download(`${state.video.videoId}.preview.${language === 'zh' ? 'zh-Hant' : 'en'}.srt`, srt(state.cues, language), 'text/plain;charset=utf-8'); } catch (error) { $('playerStatus').textContent = error.message; }
});
$('exportVideo').addEventListener('click', () => download(`${state.video.videoId}.caption-review.json`, JSON.stringify({schemaVersion: 1, reviewed: false, revisionBackups: revisionBackups.filter(item => item.videoId === state.video.videoId), videos: [exportVideo(state.video, state.cues)]}, null, 2), 'application/json'));
$('exportAll').addEventListener('click', () => {
  const videos = state.data.videos.map(video => exportVideo(video, video.videoId === state.video?.videoId ? state.cues : editedCues(video)));
  download('smartaction-58-caption-review.json', JSON.stringify({schemaVersion: 1, reviewed: false, categories: state.data.categories || [], revisionBackups, videos}, null, 2), 'application/json');
});

fetch('captions.json', {cache: 'no-store'}).then(response => { if (!response.ok) throw Error('尚未找到字幕資料。'); return response.json(); }).then(data => {
  if (!Array.isArray(data.videos) || !data.videos.length) throw Error('字幕清單是空的。');
  for (const video of data.videos) {
    if (!/^[A-Za-z0-9_-]{11}$/.test(video.videoId) || !Array.isArray(video.cues) || cueErrors(video.cues, video.duration).length) throw Error('字幕資料的影片 ID 或時間碼需要修正。');
  }
  state.data = data; $('app').hidden = false; $('loadStatus').textContent = '';
  if (revisionBackups.length) { $('backupStatus').hidden = false; $('backupStatus').textContent = `已保留 ${revisionBackups.length} 份舊版本校對稿；下載全部 JSON 可取回。`; }
  const pending = data.videos.reduce((sum, video) => sum + video.cues.filter(isPending).length, 0);
  $('videoCount').textContent = `${data.videos.length} 支`;
  const processed = data.stats?.primaryCompleted, compared = data.stats?.comparisonCompleted;
  const progress = Number.isFinite(processed) ? ` 主轉錄 ${processed} / ${data.videos.length}` + (Number.isFinite(compared) ? `；第二輪比對 ${compared} / ${data.videos.length}` : '') + '。' : '';
  $('loadStatus').textContent = `已載入 ${data.videos.length} 支影片；${pending} 段仍待確認。` + progress;
  const categories = new Map(); for (const video of data.videos) categories.set(categoryKey(video), categoryTitle(video));
  for (const [key, title] of categories) { const option = node('option', '', title); option.value = key; $('category').append(option); }
  selectVideo(new URL(location.href).searchParams.get('v') || data.videos[0].videoId);
  if (!state.video) selectVideo(data.videos[0].videoId);
  setInterval(tick, 200);
}).catch(error => { $('loadStatus').textContent = `${error.message} 請透過已部署的預覽網址或本機 HTTP 伺服器開啟。`; });
