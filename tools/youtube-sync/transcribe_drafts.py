#!/usr/bin/env python3
"""Create local, unreviewed timed-caption drafts from verified source audio.

Never uploads media, credentials, or captions. Keep review flags false until
someone checks the actual audio and the chosen transcript against it.
"""
import argparse
import dataclasses
import hashlib
import json
import re
import time
from pathlib import Path

DEFAULT_MODEL = 'adi-gov-tw/Taiwan-Tongues-ASR-CE-v1.0'
DEFAULT_REVISION = '46b5eb7ee167ac2fd06cfe02f40e8fac416e5086'


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def timestamp(seconds):
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    whole, millis = divmod(millis, 1000)
    return f'{hours:02}:{minutes:02}:{whole:02},{millis:03}'


def serialize_segment(segment, convert):
    words = [dataclasses.asdict(w) for w in (segment.words or [])]
    for word in words:
        word['traditional'] = convert(word['word'])
    warnings = []
    if segment.avg_logprob < -0.7:
        warnings.append('low-average-log-probability')
    if segment.no_speech_prob > 0.5:
        warnings.append('possible-non-speech')
    if segment.compression_ratio > 2.4:
        warnings.append('high-repetition-possible-hallucination')
    if any(w['probability'] < 0.5 for w in words):
        warnings.append('low-confidence-word')
    return {'start': segment.start, 'end': segment.end,
            'rawText': segment.text, 'text': convert(segment.text.strip()),
            'avg_logprob': segment.avg_logprob,
            'no_speech_prob': segment.no_speech_prob,
            'compression_ratio': segment.compression_ratio,
            'words': words, 'reviewFlags': warnings}


def draft_record(video_id, model, revision, language_mode, hotwords, source_sha, info, segments, elapsed):
    cleaned = []
    for segment in segments:
        segment = dict(segment)
        segment['reviewFlags'] = list(segment['reviewFlags'])
        if segment['end'] > info.duration:
            segment['reviewFlags'].append('end-trimmed-to-audio-duration')
            segment['end'] = info.duration
        if segment['end'] > segment['start'] >= 0 and segment['text']:
            cleaned.append(segment)
    return {'videoId': video_id, 'engine': 'faster-whisper',
            'model': model, 'modelRevision': revision,
            'device': 'cpu-int8', 'language': 'zh-Hant',
            'languageMode': language_mode,
            'detectedLanguage': info.language if language_mode == 'auto' else None,
            'detectedLanguageProbability': info.language_probability if language_mode == 'auto' else None,
            'allLanguageProbabilities': info.all_language_probs,
            'hotwords': hotwords,
            'duration': info.duration, 'durationAfterVad': info.duration_after_vad,
            'sourceMediaSha256': source_sha,
            'elapsedSeconds': round(elapsed, 3),
            'reviewed': False,
            'status': 'AI speech-recognition draft; compare with actual audio',
            'segments': cleaned}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--revision', help='HF commit SHA; default official model is pinned')
    parser.add_argument('--model-cache', type=Path, default=Path.home() / '.cache' / 'smartaction-asr')
    parser.add_argument('--model-tag')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--video-id')
    parser.add_argument('--skip-existing', action='store_true')
    parser.add_argument('--language', choices=['auto', 'zh'], default='zh')
    parser.add_argument('--hotwords', default='')
    args = parser.parse_args()
    from faster_whisper import WhisperModel
    from opencc import OpenCC
    convert = OpenCC('s2twp').convert
    args.out.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    print('Loading local model', args.model, flush=True)
    revision = args.revision or (DEFAULT_REVISION if args.model == DEFAULT_MODEL else None)
    model = WhisperModel(args.model, device='cpu', compute_type='int8',
                         cpu_threads=args.threads, num_workers=1,
                         download_root=str(args.model_cache), revision=revision,
                         use_auth_token=False)
    inputs = json.loads(args.inputs.read_text())
    for item in inputs:
        if args.video_id and item['videoId'] != args.video_id:
            continue
        wav = Path(item.get('audio') or item.get('wav') or item['source'])
        source_sha = file_sha256(wav)
        model_tag = re.sub(r'[^A-Za-z0-9_.-]', '-', args.model_tag or Path(args.model).name)
        model_tag += '.auto' if args.language == 'auto' else ''
        prefix = args.out / (item['videoId'] + '.' + model_tag)
        result_path = prefix.with_suffix(prefix.suffix + '.json')
        if args.skip_existing and result_path.exists():
            previous = json.loads(result_path.read_text())
            if (previous.get('sourceMediaSha256', previous.get('sourceAudioSha256')) == source_sha
                    and previous.get('modelRevision') == revision
                    and previous.get('hotwords', '') == args.hotwords
                    and previous.get('languageMode', 'zh') == args.language):
                print('Skipped existing matching audio', item['videoId'], flush=True)
                continue
        began = time.monotonic()
        stream, info = model.transcribe(str(wav), language=None if args.language == 'auto' else 'zh', task='transcribe',
            beam_size=5, temperature=0, condition_on_previous_text=False,
            word_timestamps=True, vad_filter=True,
            vad_parameters={'min_silence_duration_ms': 500},
            no_speech_threshold=0.6, log_prob_threshold=-1.0,
            hallucination_silence_threshold=1.0,
            hotwords=args.hotwords or None)
        segments = [serialize_segment(s, convert) for s in stream]
        data = draft_record(item['videoId'], args.model, revision, args.language,
                            args.hotwords, source_sha, info, segments, time.monotonic() - began)
        segments = data['segments']
        result_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        srt = '\n\n'.join(f"{n}\n{timestamp(s['start'])} --> {timestamp(s['end'])}\n{s['text']}"
                         for n, s in enumerate(segments, 1))
        prefix.with_suffix(prefix.suffix + '.zh-Hant.draft.srt').write_text(srt + ('\n' if srt else ''))
        print(json.dumps({'videoId': item['videoId'], 'segments': len(segments),
                         'elapsedSeconds': data['elapsedSeconds'],
                         'texts': [s['text'] for s in segments],
                         'reviewFlags': [s['reviewFlags'] for s in segments]}, ensure_ascii=False), flush=True)
    print('Total elapsed seconds', round(time.monotonic() - start, 2), flush=True)


if __name__ == '__main__':
    main()
