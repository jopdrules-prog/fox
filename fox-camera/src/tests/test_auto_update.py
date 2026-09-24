"""Update boundary tests: staging, corruption, offline launch and rollback."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_update as u


def release(v, extra=None):
    contents = {n: b'test' for n in u.REQUIRED}
    contents['auto_update.py'] = f"VERSION = '{v}'".encode()
    contents.update(extra or {})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for n, b in contents.items():
            z.writestr(n, b)
    raw = out.getvalue()
    pack = json.dumps({'schema': 1, 'app': u.APP, 'version': v,
                      'zip_sha256': hashlib.sha256(raw).hexdigest(),
                      'zip_base64': base64.b64encode(raw).decode()}).encode()
    url = u.BASE + f'releases/{v}/package.json'
    feed = json.dumps({'schema': 1, 'app': u.APP, 'version': v, 'url': url,
                      'sha256': hashlib.sha256(pack).hexdigest()}).encode()
    return pack, lambda address, limit: feed if address == u.FEED else pack


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        raw, _ = release('1.1.0')
        u.unpack(raw, self.data / 'versions/1.1.0', '1.1.0')
        u.atomic_json(self.data / 'auto-installed.json', {'current': '1.1.0'})
        (self.data / 'config.json').write_text('{"host":"unchanged"}')
        (self.data / 'remembered.json').write_text('{"garment":"keep"}')

    def test_stage_does_not_switch_or_edit_data(self):
        _, get = release('1.1.1')
        before = (self.data / 'auto-installed.json').read_bytes()
        with patch.object(u.subprocess, 'Popen') as start:
            self.assertEqual(u.check(self.data, '1.1.0', get), '1.1.1')
            start.assert_not_called()
        self.assertEqual((self.data / 'auto-installed.json').read_bytes(), before)
        self.assertEqual(json.loads((self.data / 'remembered.json').read_text()), {'garment': 'keep'})
        self.assertTrue(u.verified(self.data / 'versions/1.1.1'))

    def test_corrupt_archive_and_traversal_leave_current_untouched(self):
        raw, get = release('1.1.1')
        self.assertIsNone(u.check(self.data, '1.1.0', lambda a, l: get(a, l) if a == u.FEED else raw+b'x'))
        _, get = release('1.1.1', {'../config.json': b'overwrite'})
        self.assertIsNone(u.check(self.data, '1.1.0', get))
        self.assertFalse((self.data / 'update-pending.json').exists())
        self.assertEqual((self.data / 'config.json').read_text(), '{"host":"unchanged"}')

    def test_offline_preserves_downloaded_pending_and_launches(self):
        _, get = release('1.1.1')
        u.check(self.data, '1.1.0', get)
        self.assertIsNone(u.check(self.data, '1.1.0', Mock(side_effect=OSError('offline'))))
        child = Mock(); child.wait.return_value = 0
        with patch.object(u, 'check'), patch.object(u, 'start_and_confirm', return_value=child), patch.object(u.webbrowser, 'open'):
            self.assertEqual(u.launch_locked(self.data), 0)
        self.assertEqual(u.read_json(self.data / 'auto-installed.json')['current'], '1.1.1')

    def test_bad_start_rolls_back_config_and_blocks_version(self):
        _, get = release('1.1.1')
        u.check(self.data, '1.1.0', get)
        old = (self.data / 'config.json').read_bytes()
        child = Mock(); child.wait.return_value = 0
        def start(data, root, expected):
            if expected == '1.1.1':
                (data / 'config.json').write_text('new format')
                raise RuntimeError('bad boot')
            self.assertEqual(expected, '1.1.0')
            self.assertEqual((data / 'config.json').read_bytes(), old)
            return child
        with patch.object(u, 'check'), patch.object(u, 'start_and_confirm', side_effect=start), patch.object(u.webbrowser, 'open'):
            self.assertEqual(u.launch_locked(self.data), 0)
        self.assertEqual(u.read_json(self.data / 'auto-installed.json')['current'], '1.1.0')
        self.assertIn('1.1.1', u.read_json(self.data / 'update-blocked.json'))
        self.assertIsNone(u.check(self.data, '1.1.0', get))

    def test_open_running_server_does_not_update_or_restart(self):
        with patch.object(u, 'health', return_value={'app': u.APP, 'version': '1.0.5'}), \
             patch.object(u, 'launch_locked') as launch, patch.object(u.webbrowser, 'open'):
            self.assertEqual(u.launch(self.data), 0)
            launch.assert_not_called()

    def test_reject_other_app_and_downgrade(self):
        for v in ('1.0.4', '1.1.0'):
            _, get = release(v)
            self.assertIsNone(u.check(self.data, '1.1.0', get))
        self.assertIsNone(u.check(self.data, '1.1.0', lambda a, l: b'{"schema":1,"app":"other","version":"9.0.0"}'))
        self.assertFalse((self.data / 'update-pending.json').exists())

    def test_reject_tampered_download_before_activation(self):
        _, get = release('1.1.1')
        u.check(self.data, '1.1.0', get)
        (self.data / 'versions/1.1.1/assist.py').write_text('damaged')
        child = Mock(); child.wait.return_value = 0
        with patch.object(u, 'check'), patch.object(u, 'start_and_confirm', return_value=child) as start, patch.object(u.webbrowser, 'open'):
            u.launch_locked(self.data)
        self.assertEqual(start.call_args.args[2], '1.1.0')

    def test_lock_prevents_concurrent_launch(self):
        with u.lock(self.data / 'test.lock'):
            with self.assertRaises(OSError):
                with u.lock(self.data / 'test.lock'):
                    self.fail('second process lock acquired')


if __name__ == '__main__':
    unittest.main()
