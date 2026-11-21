from dataclasses import dataclass
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from transcribe_drafts import DEFAULT_REVISION, draft_record, file_sha256, serialize_segment, timestamp


@dataclass
class Word:
    word: str
    probability: float
    start: float = 0
    end: float = 1


def segment(**changes):
    values = dict(text='原辨識文字', start=0, end=1, words=[Word('文字', .99)],
                  avg_logprob=-.1, no_speech_prob=.01, compression_ratio=1)
    values.update(changes)
    return SimpleNamespace(**values)


class DraftSafetyTests(unittest.TestCase):
    def test_low_confidence_and_non_speech_remain_flagged(self):
        result = serialize_segment(segment(avg_logprob=-1, no_speech_prob=.9,
                                            words=[Word('不確定', .1)]), lambda x: x)
        self.assertIn('low-average-log-probability', result['reviewFlags'])
        self.assertIn('possible-non-speech', result['reviewFlags'])
        self.assertIn('low-confidence-word', result['reviewFlags'])

    def test_confident_repetitive_hallucination_is_not_overlooked(self):
        result = serialize_segment(segment(text='好' * 100, compression_ratio=26,
                                            avg_logprob=-.1), lambda x: x)
        self.assertIn('high-repetition-possible-hallucination', result['reviewFlags'])

    def test_original_recognition_is_retained_separately_from_conversion(self):
        result = serialize_segment(segment(text=' 发声 '), lambda x: x.replace('发声', '發聲'))
        self.assertEqual(' 发声 ', result['rawText'])
        self.assertEqual('發聲', result['text'])

    def test_output_cannot_become_reviewed_even_for_confident_or_empty_input(self):
        info = SimpleNamespace(duration=1, duration_after_vad=1, language='zh',
                               language_probability=1, all_language_probs=None, reviewed=True)
        for segments in [[], [serialize_segment(segment(), lambda x: x)]]:
            result = draft_record('aAxfSSazhhU', 'model', DEFAULT_REVISION, 'zh', '',
                                  'hash', info, segments, 1)
            self.assertIs(result['reviewed'], False)
            self.assertIn('draft', result['status'])
            self.assertIsNone(result['detectedLanguageProbability'])

    def test_cues_beyond_clip_are_trimmed_without_mutating_source(self):
        source = serialize_segment(segment(end=20), lambda x: x)
        info = SimpleNamespace(duration=15, duration_after_vad=14, language='zh',
                               language_probability=1, all_language_probs=None)
        result = draft_record('aAxfSSazhhU', 'model', DEFAULT_REVISION, 'zh', '',
                              'hash', info, [source], 1)
        self.assertEqual(15, result['segments'][0]['end'])
        self.assertEqual(20, source['end'])
        self.assertIn('end-trimmed-to-audio-duration', result['segments'][0]['reviewFlags'])

    def test_timestamp_rollovers_and_negative_start(self):
        self.assertEqual('00:01:00,000', timestamp(59.9999))
        self.assertEqual('01:00:00,000', timestamp(3599.9999))
        self.assertEqual('00:00:00,000', timestamp(-1))

    def test_file_hash_works_without_whole_file_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'audio.wav'
            value = b'caption-source' * 180000
            path.write_bytes(value)
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file read')):
                self.assertEqual(hashlib.sha256(value).hexdigest(), file_sha256(path))


if __name__ == '__main__':
    unittest.main()
