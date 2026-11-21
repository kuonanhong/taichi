import tempfile
from pathlib import Path
import unittest
from publish_github import public_files, publish, blob_sha


class FakeAPI:
    def __init__(self, changed_head=False, same=False):
        self.calls = []
        self.refs = 0
        self.changed_head = changed_head
        self.same = same

    def call(self, endpoint, method='GET', body=None):
        self.calls.append((endpoint, method, body))
        if endpoint.startswith('/git/ref/'):
            self.refs += 1
            return {'object': {'sha': 'new' if self.changed_head and self.refs > 1 else 'base'}}
        if endpoint == '/git/commits/base': return {'tree': {'sha': 'tree'}}
        if endpoint.startswith('/git/trees/'):
            return {'tree': [{'path': 'video-library/index.html', 'type': 'blob',
                              'sha': blob_sha('hello') if self.same else 'old'}]}
        return {'sha': 'made'}


class PublishTests(unittest.TestCase):
    def test_no_mutations_in_dry_run(self):
        api = FakeAPI()
        publish(api, {'video-library/index.html': 'hello'}, 'main')
        self.assertTrue(all(x[1] == 'GET' for x in api.calls))

    def test_rerun_skips_identical_files(self):
        api = FakeAPI(same=True)
        publish(api, {'video-library/index.html': 'hello'}, 'main', True)
        self.assertTrue(all(x[1] == 'GET' for x in api.calls))

    def test_concurrent_change_prevents_ref_mutation(self):
        api = FakeAPI(changed_head=True)
        with self.assertRaises(RuntimeError):
            publish(api, {'video-library/index.html': 'hello'}, 'main', True)
        self.assertFalse(any(x[1] == 'PATCH' for x in api.calls))
        created_tree = next(x[2] for x in api.calls if x[0] == '/git/trees')
        self.assertEqual('tree', created_tree['base_tree'])

    def test_reject_private_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'video-library').mkdir()
            f = root / 'video-library/catalog.json'
            f.write_text('{"localFile":"/Users/example/private.mp4"}')
            with self.assertRaises(ValueError): public_files(root)
            f.write_text('{"refresh_token":"SECRET"}')
            with self.assertRaises(ValueError): public_files(root)


if __name__ == '__main__':
    unittest.main()
