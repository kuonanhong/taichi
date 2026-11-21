#!/usr/bin/env python3
"""Reviewable YouTube metadata, playlists and timed-caption synchronization.

No network or credentials are needed for `plan`. All writes require --apply,
an explicit channel ID and reviewed records. Tokens/journal never belong in Git.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
PRIVATE_DIR = Path.home() / ".config" / "smartaction-youtube"
DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "video-library" / "catalog.json"
MAIN_PLAYLIST = "PLSFQlDsijCrs"
MANAGED_NAMES = {"zh-Hant": "SmartAction 繁體中文", "en": "SmartAction English"}
MUTABLE_SNIPPET = ("title", "description", "tags", "categoryId", "defaultLanguage", "defaultAudioLanguage")


class SafeError(RuntimeError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def validate_text(title, description):
    if not title or len(title) > 100 or any(c in title for c in "<>"):
        raise SafeError("Title must contain 1–100 characters and no angle brackets")
    if len(description.encode("utf-8")) > 5000 or any(c in description for c in "<>"):
        raise SafeError("Description must fit 5000 UTF-8 bytes and contain no angle brackets")


def descriptions(record):
    return record["description_zh"].strip() + "\n\nEnglish\n" + record["description_en"].strip()


def metadata_body(existing, record):
    """Read-modify-write only writable fields. Never send status/visibility."""
    snippet = {k: copy.deepcopy(v) for k, v in existing["snippet"].items() if k in MUTABLE_SNIPPET}
    snippet.update(title=record["title_zh"], description=descriptions(record), defaultLanguage="zh-Hant")
    if not snippet.get("categoryId"):
        raise SafeError("Existing categoryId is required; refusing to invent or remove it")
    localizations = copy.deepcopy(existing.get("localizations", {}))
    localizations["en"] = {"title": record["title_en"], "description": record["description_en"]}
    # YouTube mirrors the default-language localization from snippet on readback.
    # Match that representation so reruns do not rewrite identical metadata.
    localizations["zh-Hant"] = {"title": snippet["title"], "description": snippet["description"]}
    validate_text(snippet["title"], snippet["description"])
    for item in localizations.values():
        validate_text(item["title"], item["description"])
    return {"id": existing["id"], "snippet": snippet, "localizations": localizations}


def metadata_changed(existing, desired):
    original = {k: copy.deepcopy(v) for k, v in existing["snippet"].items() if k in MUTABLE_SNIPPET}
    return original != desired["snippet"] or existing.get("localizations", {}) != desired["localizations"]


def caption_file(catalog_path, caption):
    path = (Path(catalog_path).resolve().parent / caption["file"]).resolve()
    if path.suffix.lower() not in (".srt", ".vtt") or not path.is_file():
        raise SafeError(f"Missing SRT/VTT file: {path}")
    text = path.read_text(encoding="utf-8-sig")
    pattern = re.compile(r"(?m)^(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{3})(?:[^\n]*)\n([^\n]+)")
    matches = list(pattern.finditer(text))
    if not matches or text.count("-->") != len(matches):
        raise SafeError("Caption file needs timed cues with text; untimed descriptions are not subtitles")
    previous_start = -1
    for cue in matches:
        values = cue.groups()
        h1, m1, s1, ms1, h2, m2, s2, ms2 = [int(x or 0) for x in values[:8]]
        if max(m1, m2, s1, s2) >= 60:
            raise SafeError("Invalid caption time")
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        if start < previous_start or end <= start or not values[8].strip():
            raise SafeError("Caption cues must be chronological, nonempty and positive in duration")
        previous_start = start
    return path


def validate_catalog(catalog, catalog_path):
    keys, playlist_keys = set(), set()
    for playlist in catalog.get("playlists", []):
        if playlist["key"] in playlist_keys:
            raise SafeError("Duplicate playlist key")
        playlist_keys.add(playlist["key"])
        validate_text(playlist["title_zh"], descriptions(playlist))
    seen_ids = set()
    for record in catalog.get("videos", []):
        if record["key"] in keys:
            raise SafeError("Duplicate video key")
        keys.add(record["key"])
        vid = record.get("videoId")
        if vid:
            if not re.fullmatch(r"[\w-]{11}", vid) or vid in seen_ids:
                raise SafeError("Invalid or duplicate video ID")
            seen_ids.add(vid)
        validate_text(record["title_zh"], descriptions(record))
        validate_text(record["title_en"], record["description_en"])
        if set(record.get("playlistKeys", [])) - playlist_keys:
            raise SafeError(f"Unknown playlist in {record['key']}")
        for caption in record.get("captions", []):
            if caption.get("language") not in MANAGED_NAMES:
                raise SafeError("Managed captions support zh-Hant and en")
            caption_file(catalog_path, caption)
    return catalog


def plan(catalog):
    return {
        "mode": "dry-run", "sourcePlaylistPreserved": MAIN_PLAYLIST,
        "videos": [{"key": r["key"], "videoId": r.get("videoId"), "reviewed": r.get("reviewed") is True,
                    "action": "update-existing" if r.get("videoId") else "upload-private-candidate",
                    "descriptionBytes": len(descriptions(r).encode("utf-8")),
                    "captionTracks": len(r.get("captions", [])), "playlistKeys": r.get("playlistKeys", [])}
                   for r in catalog.get("videos", [])],
        "note": "No upload or mutation. Unreviewed records are skipped on apply. No transcript is generated by this tool."
    }


def api_client(private_dir, client_secrets=None, authenticate=False):
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise SafeError("Install requirements.txt in a virtual environment before using authenticated commands") from exc
    token_file = Path(private_dir) / "token.json"
    credentials = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file), [SCOPE])
    if authenticate:
        if not client_secrets:
            raise SafeError("auth requires --client-secrets /local/path/client_secret.json")
        config = read_json(client_secrets)
        if "installed" not in config:
            raise SafeError("Use a Desktop app OAuth client JSON, not a Web client or API key")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), [SCOPE], autogenerate_code_verifier=True)
        credentials = flow.run_local_server(host="localhost", port=0, access_type="offline", prompt="consent", open_browser=True)
    elif credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid or not credentials.has_scopes([SCOPE]):
        raise SafeError("Run auth first with the channel owner's account and the youtube.force-ssl scope")
    private_json(token_file, json.loads(credentials.to_json()))
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def pages(resource, **kwargs):
    while True:
        result = resource.list(**kwargs).execute()
        yield from result.get("items", [])
        if not result.get("nextPageToken"):
            return
        kwargs["pageToken"] = result["nextPageToken"]


def owned_channel(api, expected):
    channels = list(pages(api.channels(), part="id,snippet,contentDetails", mine=True, maxResults=50))
    if not expected:
        return [{"id": c["id"], "title": c["snippet"]["title"]} for c in channels]
    chosen = next((c for c in channels if c["id"] == expected), None)
    if not chosen:
        raise SafeError("Authorized channel does not match --channel-id. No write performed")
    return chosen


def owned_video(api, video_id, channel_id):
    found = api.videos().list(part="snippet,localizations,status", id=video_id).execute().get("items", [])
    if len(found) != 1 or found[0]["snippet"].get("channelId") != channel_id:
        raise SafeError(f"Video {video_id} is missing or not owned by the selected channel")
    return found[0]


def verify_metadata(api, video_id, channel_id, desired):
    # YouTube list responses can briefly retain pre-update translations.
    # Retry only these reads; never repeat the mutation to chase stale reads.
    for delay in (0, 1, 2, 4, 8, 10):
        if delay:
            time.sleep(delay)
        after = owned_video(api, video_id, channel_id)
        if not metadata_changed(after, desired):
            return after
    raise SafeError(f"Metadata verification is still pending for {video_id}; reconcile on the next run")


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.data = read_json(path) if self.path.exists() else {"operations": {}}

    def get(self, key):
        return self.data["operations"].get(key)

    def set(self, key, **values):
        self.data["operations"][key] = {**(self.get(key) or {}), **values}
        private_json(self.path, self.data)

    def insert_once(self, key, discover, create):
        """Reconcile before create. An uncertain prior attempt cannot be blindly retried."""
        match = discover()
        if match:
            self.set(key, status="done", id=match)
            return match
        if self.get(key):
            raise SafeError(f"Uncertain or missing prior operation {key}; inspect YouTube and reconcile before retrying")
        self.set(key, status="pending")
        result = create()
        self.set(key, status="done", id=result)
        return result


def read_managed_playlist(api, playlist_id, channel_id):
    for delay in (0, 1, 2, 4, 8, 10):
        if delay:
            time.sleep(delay)
        found = api.playlists().list(part="snippet,localizations", id=playlist_id).execute().get("items", [])
        if len(found) == 1:
            if found[0]["snippet"].get("channelId") != channel_id:
                raise SafeError("Managed playlist belongs to another channel")
            return found[0]
    raise SafeError("Managed playlist readback is still pending; no additional playlist was created")


def ensure_playlist(api, journal, channel_id, record):
    marker = f"[SmartAction playlist:{record['key']}]"
    def discover():
        matches = [p["id"] for p in pages(api.playlists(), part="snippet", mine=True, maxResults=50)
                   if marker in p["snippet"].get("description", "")]
        if len(matches) > 1:
            raise SafeError("Multiple managed playlists match; refusing to guess")
        return matches[0] if matches else None
    def create():
        title = record["title_zh"]
        body = {"snippet": {"title": title, "description": descriptions(record) + "\n\n" + marker,
                            "defaultLanguage": "zh-Hant"}, "status": {"privacyStatus": record.get("privacyStatus", "unlisted")},
                "localizations": {"en": {"title": record["title_en"], "description": record["description_en"]}}}
        validate_text(title, body["snippet"]["description"])
        return api.playlists().insert(part="snippet,status,localizations", body=body).execute()["id"]
    playlist_id = journal.insert_once(f"playlist:{channel_id}:{record['key']}", discover, create)
    # A discovered playlist may have an older title or explanation. Preserve
    # visibility and unrelated translations while reconciling managed metadata.
    original = read_managed_playlist(api, playlist_id, channel_id)
    snippet = {"title": record["title_zh"], "description": descriptions(record) + "\n\n" + marker,
               "defaultLanguage": "zh-Hant"}
    localizations = copy.deepcopy(original.get("localizations", {}))
    localizations["en"] = {"title": record["title_en"], "description": record["description_en"]}
    localizations["zh-Hant"] = {"title": snippet["title"], "description": snippet["description"]}
    validate_text(snippet["title"], snippet["description"])
    for localized in localizations.values():
        validate_text(localized["title"], localized["description"])
    old_snippet = {k: original["snippet"].get(k) for k in snippet}
    if old_snippet != snippet or original.get("localizations", {}) != localizations:
        api.playlists().update(part="snippet,localizations", body={"id": playlist_id, "snippet": snippet,
                              "localizations": localizations}).execute()
    return playlist_id


def ensure_member(api, journal, playlist_id, video_id):
    def discover():
        found = [p["id"] for p in pages(api.playlistItems(), part="snippet", playlistId=playlist_id, maxResults=50)
                 if p["snippet"].get("resourceId", {}).get("videoId") == video_id]
        return found[0] if found else None
    def create():
        body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
        return api.playlistItems().insert(part="snippet", body=body).execute()["id"]
    return journal.insert_once(f"member:{playlist_id}:{video_id}", discover, create)


def caption_readiness(track):
    status = track.get("snippet", {}).get("status")
    if status == "failed":
        raise SafeError("YouTube caption processing failed; inspect the track before retrying")
    return "ready" if status == "serving" else "processing" if status == "syncing" else "accepted"


def sync_caption(api, journal, video_id, caption, path):
    language = caption["language"]
    name = MANAGED_NAMES[language]
    existing = api.captions().list(part="snippet", videoId=video_id).execute().get("items", [])
    matches = [c for c in existing if c["snippet"].get("language") == language and c["snippet"].get("name") == name]
    if len(matches) > 1:
        raise SafeError("Multiple managed caption tracks found")
    key = f"caption:{video_id}:{language}"
    sha = digest(path)
    previous = journal.get(key)
    if matches:
        stage = caption_readiness(matches[0])
    if matches and previous and previous.get("sha256") == sha and previous.get("status") == "done":
        journal.set(key, apiStatus=matches[0]["snippet"].get("status"), readiness=stage)
        return {"action": "unchanged", "readiness": stage}
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(str(path), mimetype="application/octet-stream", resumable=False)
    if matches:
        track_id = matches[0]["id"]
        journal.set(key, status="pending-update", id=track_id)
        result = api.captions().update(part="snippet", body={"id": track_id, "snippet": {"isDraft": False}}, media_body=media).execute()
    else:
        if previous:
            raise SafeError("Caption insert is uncertain or track disappeared; reconcile before retrying")
        journal.set(key, status="pending")
        result = api.captions().insert(part="snippet", body={"snippet": {"videoId": video_id, "language": language,
                                      "name": name, "isDraft": False}}, media_body=media).execute()
    # Upload acceptance is separate from asynchronous caption processing.
    journal.set(key, status="done", id=result["id"], sha256=sha, readiness="accepted")
    readback = api.captions().list(part="snippet", videoId=video_id).execute().get("items", [])
    observed = next((item for item in readback if item["id"] == result["id"]), None)
    if observed:
        stage = caption_readiness(observed)
        journal.set(key, apiStatus=observed["snippet"].get("status"), readiness=stage)
    else:
        stage = "accepted"
    return {"action": "updated" if matches else "inserted", "readiness": stage}


def sync(api, journal, channel_id, catalog, catalog_path):
    owned_channel(api, channel_id)
    approved = [r for r in catalog.get("videos", []) if r.get("reviewed") is True and r.get("videoId")]
    # Preflight every selected video and caption before any write.
    originals = {r["videoId"]: owned_video(api, r["videoId"], channel_id) for r in approved}
    desired = {r["videoId"]: metadata_body(originals[r["videoId"]], r) for r in approved}
    selected_playlists = set(k for r in approved for k in r.get("playlistKeys", []))
    records = {p["key"]: p for p in catalog.get("playlists", [])}
    for key in selected_playlists:
        if records[key].get("reviewed") is not True:
            raise SafeError(f"Playlist {key} must be reviewed before applying membership")
    results = []
    for record in approved:
        vid = record["videoId"]
        body = desired[vid]
        if metadata_changed(originals[vid], body):
            journal.set(f"metadata:{vid}", status="pending", before=originals[vid])
            api.videos().update(part="snippet,localizations", body=body).execute()
            verify_metadata(api, vid, channel_id, body)
            journal.set(f"metadata:{vid}", status="done")
            changed = True
        else:
            changed = False
            previous = journal.get(f"metadata:{vid}")
            if previous and previous.get("status") == "pending":
                journal.set(f"metadata:{vid}", status="done", reconciled=True)
        captions = []
        for caption in record.get("captions", []):
            if caption.get("reviewed") is True:
                captions.append(sync_caption(api, journal, vid, caption, caption_file(catalog_path, caption)))
        results.append({"videoId": vid, "metadataChanged": changed, "captions": captions})
    playlists = {key: ensure_playlist(api, journal, channel_id, records[key]) for key in sorted(selected_playlists)}
    for record in approved:
        for key in record.get("playlistKeys", []):
            ensure_member(api, journal, playlists[key], record["videoId"])
    return {"videos": results, "playlists": playlists, "skippedUnreviewed": len(catalog.get("videos", [])) - len(approved)}


def upload(api, journal, channel_id, catalog, record_key, catalog_path):
    """Private resumable upload; never automatically restart an uncertain session."""
    from googleapiclient.http import MediaFileUpload
    channel = owned_channel(api, channel_id)
    record = next((v for v in catalog["videos"] if v["key"] == record_key), None)
    if not record or record.get("reviewed") is not True or record.get("privacyReviewed") is not True:
        raise SafeError("Upload requires a reviewed record and privacyReviewed:true (mosaic checked)")
    if record.get("videoId"):
        raise SafeError("Record already has a videoId; use sync instead of re-upload")
    if record.get("newUploadConfirmed") is not True:
        raise SafeError("After checking channel inventory, set newUploadConfirmed:true only for genuinely new videos")
    path = (Path(catalog_path).resolve().parent / record["localFile"]).resolve()
    if not path.is_file() or digest(path) != record.get("sha256"):
        raise SafeError("Local file SHA256 does not match the reviewed manifest")
    marker = f"[SmartAction source-sha256:{record['sha256']}]"
    # Exhaust the channel uploads playlist before deciding that the marker is absent.
    upload_ids = [i["snippet"]["resourceId"]["videoId"] for i in pages(api.playlistItems(), part="snippet",
                  playlistId=channel["contentDetails"]["relatedPlaylists"]["uploads"], maxResults=50)]
    matches, title_matches = [], []
    for start in range(0, len(upload_ids), 50):
        items = api.videos().list(part="snippet", id=",".join(upload_ids[start:start+50])).execute().get("items", [])
        matches.extend(v["id"] for v in items if marker in v["snippet"].get("description", ""))
        title_matches.extend(v["id"] for v in items if v["snippet"].get("title") == record["title_zh"]
                             and marker not in v["snippet"].get("description", ""))
    if len(matches) > 1:
        raise SafeError("Several uploaded videos have this source hash; manual reconciliation required")
    key = f"upload:{channel_id}:{record['sha256']}"
    if matches:
        journal.set(key, status="done", id=matches[0])
        return {"videoId": matches[0], "uploaded": False, "adoptThisVideoIdIntoCatalog": True}
    if title_matches:
        raise SafeError("Existing same-title videos have no source hash; identify their videoIds before uploading")
    previous = journal.get(key)
    if previous and not previous.get("resumable_uri"):
        raise SafeError("Prior upload is uncertain; inspect channel before retrying. No new upload started")
    if previous and previous.get("status") == "done":
        raise SafeError("Previously uploaded video is no longer found; no duplicate will be created")
    description = descriptions(record) + "\n\n" + marker
    validate_text(record["title_zh"], description)
    body = {"snippet": {"title": record["title_zh"], "description": description, "categoryId": "27",
                         "defaultLanguage": "zh-Hant", "defaultAudioLanguage": "zh-Hant"},
            "status": {"privacyStatus": "private"},
            "localizations": {"en": {"title": record["title_en"], "description": record["description_en"]}}}
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = api.videos().insert(part="snippet,status,localizations", body=body, media_body=media, notifySubscribers=False)
    if previous:
        request.resumable_uri = previous["resumable_uri"]
        # Ask the server for the accepted byte position before sending more data.
        request._in_error_state = True
    else:
        journal.set(key, status="pending", sourceFile=str(path))
    response = None
    try:
        while response is None:
            _, response = request.next_chunk(num_retries=0)
            journal.set(key, status="uploading", resumable_uri=request.resumable_uri)
    finally:
        if request.resumable_uri:
            journal.set(key, resumable_uri=request.resumable_uri)
    journal.set(key, status="done", id=response["id"])
    return {"videoId": response["id"], "uploaded": True, "privacyStatus": "private", "adoptThisVideoIdIntoCatalog": True}


def inventory(api, playlist_id):
    members = list(pages(api.playlistItems(), part="snippet", playlistId=playlist_id, maxResults=50))
    ids = list(dict.fromkeys(i["snippet"]["resourceId"]["videoId"] for i in members))
    videos, warnings = [], []
    for start in range(0, len(ids), 50):
        batch = ",".join(ids[start:start+50])
        try:
            # fileDetails.fileName is owner-only and preserves the original upload
            # filename. A public title is never treated as proof of that filename.
            response = api.videos().list(part="snippet,localizations,contentDetails,status,fileDetails", id=batch).execute()
        except Exception as error:
            if getattr(getattr(error, "resp", None), "status", None) != 403:
                raise
            try:
                reasons = {e.get("reason") for e in json.loads(error.content).get("error", {}).get("errors", [])}
            except (AttributeError, TypeError, ValueError):
                reasons = set()
            if not reasons.intersection({"forbidden", "insufficientPermissions"}):
                raise
            warnings.append("Owner-only fileDetails unavailable for a batch; original source filenames remain unverified.")
            response = api.videos().list(part="snippet,localizations,contentDetails,status", id=batch).execute()
        for video in response.get("items", []):
            video["sourceFilenameVerified"] = bool(video.get("fileDetails", {}).get("fileName"))
            video["sourceFilename"] = video.get("fileDetails", {}).get("fileName")
            videos.append(video)
    return {"playlistId": playlist_id, "videos": videos, "warnings": warnings}


def export_captions(api, channel_id, video_id, out_dir, english_translation=False):
    owned_channel(api, channel_id)
    owned_video(api, video_id, channel_id)
    from googleapiclient.http import MediaIoBaseDownload
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tracks = api.captions().list(part="snippet", videoId=video_id).execute().get("items", [])
    result = []
    for track in tracks:
        language = re.sub(r"[^A-Za-z0-9_-]", "_", track["snippet"]["language"])
        for translation in ([None, "en"] if english_translation and not language.startswith("en") else [None]):
            args = {"id": track["id"], "tfmt": "srt"}
            if translation:
                args["tlang"] = translation
            data = io.BytesIO()
            downloader = MediaIoBaseDownload(data, api.captions().download(**args))
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=0)
            name = f"{video_id}.{language}.{track['id']}" + (".en-machine-draft" if translation else "") + ".srt"
            path = out_dir / name
            path.write_bytes(data.getvalue())
            result.append({"file": str(path), "language": translation or language, "reviewed": False, "machineTranslated": bool(translation)})
    return {"videoId": video_id, "tracks": result, "note": "No caption tracks means transcription is still required; no transcript was fabricated."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "auth", "inventory", "export-captions", "sync", "upload"])
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--private-dir", type=Path, default=PRIVATE_DIR)
    parser.add_argument("--client-secrets", type=Path)
    parser.add_argument("--channel-id")
    parser.add_argument("--playlist-id", default=MAIN_PLAYLIST)
    parser.add_argument("--video-id")
    parser.add_argument("--record-key")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--english-translation", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    # Apply is opt-in even when the user accidentally runs `sync` or `upload`.
    if args.command == "plan" or args.command in ("sync", "upload") and not args.apply:
        data = validate_catalog(read_json(args.catalog), args.catalog)
        result = plan(data)
    elif args.command == "auth":
        api = api_client(args.private_dir, args.client_secrets, authenticate=True)
        result = {"authorizedChannels": owned_channel(api, None), "tokenStoredPrivately": True}
    else:
        if args.command in ("sync", "upload", "export-captions") and not args.channel_id:
            raise SafeError("This command requires the expected --channel-id")
        api = api_client(args.private_dir)
        if args.command == "inventory":
            result = inventory(api, args.playlist_id)
        elif args.command == "export-captions":
            if not args.video_id or not args.out:
                raise SafeError("export-captions requires --video-id and --out")
            result = export_captions(api, args.channel_id, args.video_id, args.out, args.english_translation)
        else:
            import fcntl
            args.private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock_path = args.private_dir / "sync.lock"
            with lock_path.open("a") as lock:
                os.chmod(lock_path, 0o600)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise SafeError("Another sync/upload is running; refusing concurrent writes") from exc
                data = validate_catalog(read_json(args.catalog), args.catalog)
                journal = Journal(args.private_dir / "journal.json")
                result = sync(api, journal, args.channel_id, data, args.catalog) if args.command == "sync" else upload(api, journal, args.channel_id, data, args.record_key, args.catalog)
    if args.out and args.command != "export-captions":
        # Inventory may contain private metadata; use restrictive permissions.
        private_json(args.out, result)
        print(f"Saved {args.out}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except SafeError as error:
        print(f"Stopped safely: {error}", file=sys.stderr)
        sys.exit(2)
    except Exception as error:
        # Do not dump HTTP headers, token-bearing upload URLs or credential objects.
        print(f"Operation failed ({type(error).__name__}). Check the private journal; do not blindly retry an uncertain upload.", file=sys.stderr)
        sys.exit(1)
