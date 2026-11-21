import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import sync_youtube as tool


def record(**changes):
    return {"key": "warmup1", "videoId": "aAxfSSazhhU", "title_zh": "熱身1", "title_en": "Warm-up 1",
            "description_zh": "溫和熱身。", "description_en": "Gentle warm-up.", "playlistKeys": [],
            "captions": [], "reviewed": False, **changes}


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.existing = {"id": "aAxfSSazhhU", "snippet": {"title": "Old", "description": "Old text", "categoryId": "27",
                        "channelId": "channel", "tags": ["keep", "運動"], "defaultAudioLanguage": "zh-TW",
                        "defaultLanguage": "zh-TW", "thumbnails": {"default": {"url": "keep"}}},
                         "localizations": {"ja": {"title": "日本語", "description": "既存説明"}},
                         "status": {"privacyStatus": "unlisted"}}

    def test_preserve_metadata_and_other_translations_never_send_visibility(self):
        before = copy.deepcopy(self.existing)
        body = tool.metadata_body(self.existing, record())
        self.assertEqual(body["snippet"]["tags"], ["keep", "運動"])
        self.assertEqual(body["snippet"]["defaultAudioLanguage"], "zh-TW")
        self.assertEqual(body["localizations"]["ja"], before["localizations"]["ja"])
        self.assertNotIn("status", body)
        self.assertNotIn("thumbnails", body["snippet"])
        self.assertEqual(self.existing, before)

    def test_utf8_description_limit_not_python_character_count(self):
        with self.assertRaises(tool.SafeError):
            tool.metadata_body(self.existing, record(description_zh="字" * 1700))

    def test_no_change_skips_metadata_update(self):
        desired = tool.metadata_body(self.existing, record())
        observed = {**copy.deepcopy(self.existing), **desired}
        self.assertFalse(tool.metadata_changed(observed, desired))

    def test_default_localization_matches_youtube_snippet_mirror(self):
        desired = tool.metadata_body(self.existing, record())
        observed = {**copy.deepcopy(self.existing), **copy.deepcopy(desired)}
        observed["localizations"]["zh-Hant"] = {
            "title": desired["snippet"]["title"],
            "description": desired["snippet"]["description"],
        }
        self.assertFalse(tool.metadata_changed(observed, desired))

    def test_eventually_consistent_readback_retries_reads_without_repeating_write(self):
        from unittest.mock import patch
        desired = tool.metadata_body(self.existing, record())
        observed = {**copy.deepcopy(self.existing), **copy.deepcopy(desired)}
        with patch.object(tool, "owned_video", side_effect=[self.existing, observed]) as read:
            with patch.object(tool.time, "sleep") as sleep:
                self.assertEqual(tool.verify_metadata(None, "video", "channel", desired), observed)
        self.assertEqual(read.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_unreviewed_records_cannot_mutate(self):
        api = MagicMock()
        api.channels().list().execute.return_value = {"items": [{"id": "channel", "snippet": {"title": "Ours"}}]}
        with tempfile.TemporaryDirectory() as folder:
            result = tool.sync(api, tool.Journal(Path(folder) / "journal.json"), "channel", {"videos": [record()], "playlists": []}, Path(folder)/"catalog.json")
        self.assertEqual(result["skippedUnreviewed"], 1)
        api.videos().update.assert_not_called()
        api.playlists().insert.assert_not_called()
        api.captions().insert.assert_not_called()

    def test_wrong_channel_stops_before_write(self):
        api = MagicMock()
        api.channels().list().execute.return_value = {"items": [{"id": "other", "snippet": {"title": "Other"}}]}
        with self.assertRaises(tool.SafeError):
            tool.owned_channel(api, "expected")
        api.videos().update.assert_not_called()


class CaptionTests(unittest.TestCase):
    def test_caption_readiness_distinguishes_processing_and_ready(self):
        self.assertEqual(tool.caption_readiness({"snippet": {"status": "serving"}}), "ready")
        self.assertEqual(tool.caption_readiness({"snippet": {"status": "syncing"}}), "processing")
        self.assertEqual(tool.caption_readiness({"snippet": {}}), "accepted")
        with self.assertRaises(tool.SafeError):
            tool.caption_readiness({"snippet": {"status": "failed"}})

    def test_same_hash_processing_track_skips_reupload_but_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"caption.srt"
            path.write_text("1\n00:00:01,000 --> 00:00:02,000\n站穩。\n")
            journal = tool.Journal(Path(folder)/"journal.json")
            journal.set("caption:aAxfSSazhhU:zh-Hant", status="done", sha256=tool.digest(path))
            api = MagicMock()
            api.captions().list().execute.return_value = {"items": [{"id": "track", "snippet": {
                "language": "zh-Hant", "name": tool.MANAGED_NAMES["zh-Hant"], "status": "syncing"}}]}
            result = tool.sync_caption(api, journal, "aAxfSSazhhU", {"language": "zh-Hant"}, path)
            self.assertEqual(result, {"action": "unchanged", "readiness": "processing"})
            api.captions().insert.assert_not_called()
            api.captions().update.assert_not_called()

    def test_timed_srt_and_vtt_accepted_untimed_summary_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name, content in [("zh.srt", "1\n00:00:01,000 --> 00:00:02,000\n雙腳站穩。\n"),
                                  ("en.vtt", "WEBVTT\n\n00:01.000 --> 00:02.000\nStand steadily.\n")]:
                (base/name).write_text(content)
                self.assertEqual(tool.caption_file(base/"catalog.json", {"file": name}), (base/name).resolve())
            (base/"bad.srt").write_text("This is an interesting movement description.")
            with self.assertRaises(tool.SafeError):
                tool.caption_file(base/"catalog.json", {"file": "bad.srt"})

    def test_backwards_cue_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"bad.srt"
            path.write_text("1\n00:00:02,000 --> 00:00:01,000\nStop.\n")
            with self.assertRaises(tool.SafeError):
                tool.caption_file(path.parent/"catalog.json", {"file": path.name})


class JournalTests(unittest.TestCase):
    def test_new_playlist_missing_from_first_read_does_not_trigger_another_insert(self):
        from unittest.mock import patch
        api = MagicMock()
        expected = {"id": "playlist", "snippet": {"channelId": "channel"}}
        api.playlists().list().execute.side_effect = [{"items": []}, {"items": [expected]}]
        with patch.object(tool.time, "sleep"):
            self.assertEqual(tool.read_managed_playlist(api, "playlist", "channel"), expected)
        api.playlists().insert.assert_not_called()

    def test_existing_playlist_updates_bilingual_metadata_without_visibility_changes(self):
        playlist = {"key": "warmup", "title_zh": "熱身", "title_en": "Warm-up", "description_zh": "暖身說明。", "description_en": "Warm-up notes."}
        original = {"id": "playlist1", "snippet": {"title": "Old", "description": "[SmartAction playlist:warmup]", "channelId": "channel"},
                    "localizations": {"ja": {"title": "旧", "description": "保存"}}, "status": {"privacyStatus": "public"}}
        api = MagicMock()
        api.playlists().list().execute.side_effect = [{"items": [original]}, {"items": [original]}]
        with tempfile.TemporaryDirectory() as folder:
            found = tool.ensure_playlist(api, tool.Journal(Path(folder)/"journal.json"), "channel", playlist)
        self.assertEqual(found, "playlist1")
        api.playlists().insert.assert_not_called()
        kwargs = api.playlists().update.call_args.kwargs
        self.assertEqual(kwargs["part"], "snippet,localizations")
        self.assertNotIn("status", kwargs["body"])
        self.assertEqual(kwargs["body"]["localizations"]["ja"], original["localizations"]["ja"])
        self.assertEqual(kwargs["body"]["localizations"]["en"]["title"], "Warm-up")

    def test_successful_insert_reconciles_without_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = tool.Journal(Path(folder)/"journal.json")
            create = MagicMock(return_value="item1")
            self.assertEqual(journal.insert_once("membership", lambda: None, create), "item1")
            self.assertEqual(journal.insert_once("membership", lambda: "item1", create), "item1")
            self.assertEqual(create.call_count, 1)
            self.assertEqual((journal.path.stat().st_mode & 0o777), 0o600)

    def test_timeout_not_blindly_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"journal.json"
            journal = tool.Journal(path)
            create = MagicMock(side_effect=TimeoutError("uncertain"))
            with self.assertRaises(TimeoutError):
                journal.insert_once("playlist", lambda: None, create)
            loaded = tool.Journal(path)
            with self.assertRaises(tool.SafeError):
                loaded.insert_once("playlist", lambda: None, create)
            self.assertEqual(create.call_count, 1)

    def test_timeout_recovered_by_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = tool.Journal(Path(folder)/"journal.json")
            journal.set("caption", status="pending")
            create = MagicMock()
            self.assertEqual(journal.insert_once("caption", lambda: "found", create), "found")
            create.assert_not_called()


class PaginationTests(unittest.TestCase):
    def test_all_pages_are_read(self):
        resource = MagicMock()
        resource.list().execute.side_effect = [{"items": [1], "nextPageToken": "page2"}, {"items": [2]}]
        self.assertEqual(list(tool.pages(resource, part="snippet", maxResults=50)), [1, 2])
        self.assertEqual(resource.list.call_args.kwargs["pageToken"], "page2")

    def test_inventory_original_filename_from_owner_file_details(self):
        api = MagicMock()
        api.playlistItems().list().execute.return_value = {"items": [{"snippet": {"resourceId": {"videoId": "aAxfSSazhhU"}}}]}
        api.videos().list().execute.return_value = {"items": [{"id": "aAxfSSazhhU", "snippet": {"title": "Changed title"}, "fileDetails": {"fileName": "Original.mp4"}}]}
        result = tool.inventory(api, "playlist")
        self.assertEqual(result["videos"][0]["sourceFilename"], "Original.mp4")
        self.assertTrue(result["videos"][0]["sourceFilenameVerified"])

    def test_inventory_forbidden_falls_back_without_guessing_filename(self):
        class Forbidden(Exception):
            resp = type("Response", (), {"status": 403})()
            content = b'{"error":{"errors":[{"reason":"forbidden"}]}}'
        api = MagicMock()
        api.playlistItems().list().execute.return_value = {"items": [{"snippet": {"resourceId": {"videoId": "aAxfSSazhhU"}}}]}
        api.videos().list().execute.side_effect = [Forbidden(), {"items": [{"id": "aAxfSSazhhU", "snippet": {"title": "Not the filename"}}]}]
        result = tool.inventory(api, "playlist")
        self.assertIsNone(result["videos"][0]["sourceFilename"])
        self.assertFalse(result["videos"][0]["sourceFilenameVerified"])
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
