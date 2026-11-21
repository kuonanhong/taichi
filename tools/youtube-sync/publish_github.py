#!/usr/bin/env python3
"""Publish only the public video-library files; leave the game and credentials alone."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request


def public_files(root):
    root = Path(root).resolve()
    library = root / 'video-library'
    if not library.is_dir():
        raise ValueError('Missing video-library directory')
    result = {}
    for path in sorted(library.rglob('*')):
        if not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(library.resolve()):
            raise ValueError('Public files cannot be symlinks')
        if path.suffix.lower() not in {'.json', '.html', '.css', '.srt', '.vtt', '.txt'}:
            raise ValueError('Unsupported public file: ' + path.name)
        if re.search(r'token|client.?secret|credentials|oauth|\.env', path.name, re.I):
            raise ValueError('Credential-like filename refused')
        raw = path.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Use video hosting for large media')
        text = raw.decode('utf-8')
        if re.search(r'"(?:refresh_token|client_secret|access_token)"\s*:|/Users/|/home/', text):
            raise ValueError('Private credential or absolute local path in public file')
        if path.suffix == '.json':
            json.loads(text)
        result[path.relative_to(root).as_posix()] = text
    return result


def blob_sha(text):
    raw = text.encode('utf-8')
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


class GitHub:
    def __init__(self, token, repository):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository):
            raise ValueError('Invalid repository')
        self.base = 'https://api.github.com/repos/' + repository
        self.token = token

    def call(self, endpoint, method='GET', body=None):
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'SmartActionVideoLibrary',
                   'X-GitHub-Api-Version': '2026-03-10'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + endpoint, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError('GitHub HTTP ' + str(exc.code) + ': operation stopped; re-read before retry') from None


def publish(api, files, branch, apply=False):
    if not re.fullmatch(r'[\w./-]+', branch) or '..' in branch:
        raise ValueError('Invalid branch')
    head = api.call('/git/ref/heads/' + branch)['object']['sha']
    commit = api.call('/git/commits/' + head)
    tree = api.call('/git/trees/' + commit['tree']['sha'] + '?recursive=1')
    if tree.get('truncated'):
        raise RuntimeError('Incomplete remote tree; stop rather than guess')
    remote = {x['path']: x.get('sha') for x in tree['tree'] if x['type'] == 'blob'}
    entries = [{'path': name, 'mode': '100644', 'type': 'blob', 'content': text}
               for name, text in files.items() if remote.get(name) != blob_sha(text)]
    print(json.dumps({'dryRun': not apply, 'changedFiles': [x['path'] for x in entries],
                      'deleteFiles': [], 'baseCommit': head}, ensure_ascii=False, indent=2))
    if not apply or not entries:
        return head
    new_tree = api.call('/git/trees', 'POST', {'base_tree': commit['tree']['sha'], 'tree': entries})
    new_commit = api.call('/git/commits', 'POST', {'message': 'Update bilingual Tai Chi video library',
                         'tree': new_tree['sha'], 'parents': [head]})
    if api.call('/git/ref/heads/' + branch)['object']['sha'] != head:
        raise RuntimeError('Branch changed concurrently; no ref update made')
    # Fast-forward only. If the final response is lost, rerunning compares file hashes first.
    api.call('/git/refs/heads/' + branch, 'PATCH', {'sha': new_commit['sha'], 'force': False})
    return new_commit['sha']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--repository', default='kuonanhong/taichi')
    parser.add_argument('--branch', default='main')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    token = os.environ.get('GITHUB_TOKEN')
    if args.apply and not token:
        parser.error('Set GITHUB_TOKEN in the local environment; never commit it')
    files = public_files(args.root)
    print('Commit:', publish(GitHub(token, args.repository), files, args.branch, args.apply))


if __name__ == '__main__':
    main()
