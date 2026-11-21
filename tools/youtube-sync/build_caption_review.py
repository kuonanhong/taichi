#!/usr/bin/env python3
"""Build a private local audio/caption review page, separate from public metadata."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import wave


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def build(catalog_path, inputs_path, destination, video_ids=None):
    destination = Path(destination).resolve()
    if any(p in destination.parts for p in ('video-library', 'public', '.git')):
        raise ValueError('Review drafts and original audio must stay outside public directories')
    catalog_path, inputs_path = Path(catalog_path).resolve(), Path(inputs_path).resolve()
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    records = {v['videoId']: v for v in catalog['videos']}
    inputs = json.loads(inputs_path.read_text(encoding='utf-8'))
    selected, seen = [], set()
    for item in inputs:
        vid = item['videoId']
        if video_ids and vid not in video_ids:
            continue
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid) or vid not in records or vid in seen:
            raise ValueError('Unknown, invalid or repeated video ID')
        source = Path(item.get('audio') or item.get('wav') or item['source']).expanduser()
        if not source.is_absolute():
            source = inputs_path.parent / source
        source = source.resolve()
        if not source.is_file():
            raise ValueError('Source media is missing')
        if item.get('sha256') and sha256(source) != item['sha256']:
            raise ValueError('Source hash changed; verify the video mapping first')
        selected.append((vid, source, records[vid]))
        seen.add(vid)
    if not selected or (video_ids and set(video_ids) != seen):
        raise ValueError('Requested videos are missing from the verified input mapping')
    template = Path(__file__).with_name('caption-review.html')
    if not template.is_file():
        raise ValueError('caption-review.html must stay beside this script')
    destination.mkdir(parents=True, exist_ok=True)
    videos = []
    for vid, source, record in selected:
        target = destination / (vid + '.wav')
        if source.suffix.lower() == '.wav':
            if source != target:
                shutil.copy2(source, target)
        else:
            ffmpeg = shutil.which('ffmpeg')
            if not ffmpeg:
                raise ValueError('FFmpeg is required to extract audio from video')
            subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(source), '-vn',
                            '-ar', '16000', '-ac', '1', str(target)], check=True)
        with wave.open(str(target)) as audio:
            duration = audio.getnframes() / audio.getframerate()
        videos.append({'videoId': vid, 'title': record['title_zh'], 'duration': duration,
                       'audio': target.name, 'audioSha256': sha256(target), 'asrReference': ''})
    shutil.copy2(template, destination / 'index.html')
    (destination / 'review-data.json').write_text(json.dumps({
        'spokenLanguages': ['cmn-TW', 'nan-TW'], 'privateLocalReview': True,
        'videos': videos}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'videos': len(videos), 'output': str(destination), 'youtubeWrites': 0,
            'note': 'Serve only on 127.0.0.1; do not publish this folder or unreviewed drafts'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path(__file__).resolve().parents[2] / 'video-library/catalog.json')
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--video-id', action='append')
    args = parser.parse_args()
    print(json.dumps(build(args.catalog, args.inputs, args.out, args.video_id), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
