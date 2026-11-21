import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import wave
from build_caption_review import build


class ReviewBuilderTests(unittest.TestCase):
    def prepare(self, root):
        source = root / 'input.wav'
        with wave.open(str(source), 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b'\0\0' * 1600)
        catalog = root / 'catalog.json'; inputs = root / 'inputs.json'
        catalog.write_text(json.dumps({'videos': [{'videoId': 'aAxfSSazhhU', 'title_zh': '攬鵲尾'}]}))
        inputs.write_text(json.dumps([{'videoId': 'aAxfSSazhhU', 'audio': 'input.wav',
                                      'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]))
        return catalog, inputs

    def test_audio_is_copied_with_hash_and_no_private_paths_in_web_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); catalog, inputs = self.prepare(root)
            result = build(catalog, inputs, root / 'review')
            self.assertEqual(result['youtubeWrites'], 0)
            data = json.loads((root / 'review/review-data.json').read_text())
            video = data['videos'][0]
            self.assertEqual(video['audio'], 'aAxfSSazhhU.wav')
            self.assertAlmostEqual(video['duration'], .1)
            self.assertNotIn(str(root), json.dumps(data))

    def test_public_destination_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); catalog, inputs = self.prepare(root)
            with self.assertRaises(ValueError): build(catalog, inputs, root / 'video-library/review')

    def test_changed_media_hash_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); catalog, inputs = self.prepare(root)
            (root / 'input.wav').write_bytes(b'changed')
            with self.assertRaises(ValueError): build(catalog, inputs, root / 'review')


if __name__ == '__main__':
    unittest.main()
