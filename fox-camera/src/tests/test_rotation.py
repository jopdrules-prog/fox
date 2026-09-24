import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from assist import Assistant
from vision import decode


class Camera:
    def __init__(self):
        image = Image.new('RGB', (160, 80), 'red')
        image.paste('green', (80, 0, 160, 40))
        image.paste('blue', (0, 40, 80, 80))
        image.paste('yellow', (80, 40, 160, 80))
        data = io.BytesIO()
        image.save(data, 'PNG')
        self.raw = data.getvalue()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def call(self, request):
        assert request == 'GetInputList'
        return {'inputs': [{'inputName': 'DroidCamOBS'}]}

    def capture(self, source):
        assert source == 'DroidCamOBS'
        return self.raw


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.helper = Mock()
        self.app = Assistant(self.tmp.name, helper=self.helper, vision=Mock())
        self.app.configure({'host': '127.0.0.1', 'source': 'DroidCamOBS',
                            'password': 'test-only', 'roi': [0, 0, .5, .5]})
        self.camera = Camera()
        self.app.obs = lambda: self.camera

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def test_preview_and_recognition_share_orientation_and_selected_garment(self):
        for turns, size, corner in [(0, (160, 80), (255, 0, 0)),
                                    (90, (80, 160), (0, 0, 255)),
                                    (180, (160, 80), (255, 255, 0)),
                                    (270, (80, 160), (0, 128, 0)),
                                    (0, (160, 80), (255, 0, 0))]:
            with self.subTest(rotation=turns):
                self.assertEqual(self.app.config['rotation'], turns)
                image, region = self.app.capture()
                self.assertEqual(image.size, size)
                self.assertEqual(image.getpixel((10, 10)), corner)
                self.assertEqual(region.getextrema(), ((255, 255), (0, 0), (0, 0)))
                self.assertTrue(self.app.camera_check()['camera_ready'])
                preview = decode(self.app.latest)
                self.assertEqual(preview.size, size)
                for actual, expected in zip(preview.getpixel((10, 10)), corner):
                    self.assertLessEqual(abs(actual - expected), 4)
                self.app.rotate_camera(90)
        self.helper.activate.assert_not_called()

    def test_rotation_persists_and_cancels_stale_proposals_without_broadcast(self):
        self.app.running = True
        self.app.pending = {'id': 'stale'}
        self.app.latest = b'old'
        self.app.rotate_camera(-90)
        self.assertFalse(self.app.running)
        self.assertIsNone(self.app.pending)
        self.assertIsNone(self.app.latest)
        self.assertEqual(self.app.config['rotation'], 270)
        self.assertEqual(self.app.password, 'test-only')
        saved = Path(self.tmp.name, 'config.json').read_text()
        self.assertNotIn('test-only', saved)
        restarted = Assistant(self.tmp.name, helper=Mock(), vision=Mock())
        self.assertEqual(restarted.config['rotation'], 270)
        self.assertEqual(restarted.config['roi'], self.app.config['roi'])
        self.assertEqual(restarted.password, '')
        restarted.close()
        self.helper.activate.assert_not_called()

    def test_legacy_settings_and_later_config_save(self):
        path = Path(self.tmp.name, 'config.json')
        legacy = dict(self.app.config)
        legacy.pop('rotation')
        path.write_text(json.dumps(legacy))
        restarted = Assistant(self.tmp.name, helper=Mock(), vision=Mock())
        self.assertEqual(restarted.config['rotation'], 0)
        restarted.rotate_camera(90)
        restarted.configure(legacy)
        self.assertEqual(restarted.config['rotation'], 90)
        restarted.close()

    def test_invalid_rotation_does_not_change_saved_settings(self):
        before = Path(self.tmp.name, 'config.json').read_bytes()
        for bad in (0, 45, '90', True, None):
            with self.assertRaises(ValueError):
                self.app.rotate_camera(bad)
        with self.assertRaises(ValueError):
            self.app.configure(dict(self.app.config, rotation=45))
        self.assertEqual(Path(self.tmp.name, 'config.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
