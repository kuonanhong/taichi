export const isPending = cue => cue.status === 'pending' || /待確認/.test(cue.zh || '') || /pending confirmation/i.test(cue.en || '');

export function clock(seconds) {
  const total = Math.max(0, Math.round(Number(seconds || 0) * 1000));
  const h = Math.floor(total / 3600000), m = Math.floor(total / 60000) % 60, s = Math.floor(total / 1000) % 60;
  return (h ? String(h).padStart(2, '0') + ':' : '') + [m, s].map(v => String(v).padStart(2, '0')).join(':') + '.' + String(total % 1000).padStart(3, '0');
}

export function activeCueIndex(cues, time) {
  if (!Number.isFinite(time)) return -1;
  let low = 0, high = cues.length - 1, candidate = -1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    if (cues[mid].start <= time) { candidate = mid; low = mid + 1; } else high = mid - 1;
  }
  return candidate >= 0 && time < cues[candidate].end ? candidate : -1;
}

export function seekTime(seconds, duration, playerDuration) {
  const limits = [duration, playerDuration].filter(value => Number.isFinite(value) && value > 0);
  const wanted = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  return limits.length ? Math.min(wanted, Math.max(0, Math.min(...limits) - .01)) : wanted;
}

export function cueErrors(cues, duration) {
  const errors = [];
  let previousEnd = -Infinity;
  cues.forEach((cue, index) => {
    if (!Number.isFinite(cue.start) || !Number.isFinite(cue.end) || cue.start < 0 || cue.end <= cue.start) errors.push({index, message: '時間碼需為有效且遞增的秒數'});
    if (cue.start < previousEnd - .05) errors.push({index, message: '字幕段落重疊'});
    if (Number.isFinite(duration) && cue.end > duration + .1) errors.push({index, message: '時間碼超過影片長度'});
    previousEnd = cue.end;
  });
  return errors;
}

export function captionText(value) {
  // Blank lines delimit cues in SRT/VTT, even when entered as paragraphs by a reviewer.
  return String(value || '').replace(/\r\n?/g, '\n').split('\n').filter(line => line.trim()).join('\n').trim();
}

export function srt(cues, language) {
  if (!['zh', 'en'].includes(language)) throw Error('Unsupported caption language');
  if (cueErrors(cues).length) throw Error('請先核對字幕時間碼。');
  const stamp = seconds => {
    const time = clock(seconds), full = time.split(':').length === 2 ? '00:' + time : time;
    return full.replace('.', ',');
  };
  return cues.map((cue, index) => `${index + 1}\n${stamp(cue.start)} --> ${stamp(cue.end)}\n${captionText(cue[language]) || (language === 'zh' ? '待確認' : 'Pending confirmation')}`).join('\n\n') + '\n';
}

export function exportVideo(video, cues) {
  const preserved = Object.fromEntries(['categoryKey', 'categoryLabel', 'categoryLabelEn', 'note_zh', 'note_en',
    'asrStatus', 'primaryCompleted', 'comparisonCompleted', 'videoLevelPlaceholder', 'timingEstimated', 'lessonDate']
    .filter(key => video[key] !== undefined).map(key => [key, video[key]]));
  return {
    videoId: video.videoId, title_zh: video.title_zh, title_en: video.title_en,
    category: video.category || video.categoryKey || '', duration: video.duration,
    ...preserved, reviewed: false,
    cues: cues.map((cue, index) => ({id: cue.id || `${video.videoId}-${index + 1}`, start: cue.start, end: cue.end,
      zh: cue.zh || '待確認', en: cue.en || 'Pending confirmation', status: isPending(cue) ? 'pending' : 'draft', reviewed: false,
      ...(cue.reason ? {reason: cue.reason} : {}),
      ...(cue.timingEstimated !== undefined ? {timingEstimated: cue.timingEstimated} : {}),
      ...(cue.hasPartialText !== undefined ? {hasPartialText: cue.hasPartialText} : {}),
      ...(cue.videoLevelPlaceholder !== undefined ? {videoLevelPlaceholder: cue.videoLevelPlaceholder} : {})})),
  };
}

export function archiveDraft(backups, video, entry, preservedAt) {
  const cues = exportVideo(video, entry.cues || []).cues;
  if (!backups.some(item => item.videoId === video.videoId && item.sourceSignature === entry.signature && JSON.stringify(item.cues) === JSON.stringify(cues))) {
    backups.push({videoId: video.videoId, sourceSignature: entry.signature,
      preservedAt, reviewed: false, cues});
  }
  return backups;
}
