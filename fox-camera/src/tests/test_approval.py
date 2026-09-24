import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assist import Assistant, product_key


def garment(color):
    image = Image.new('RGB', (224, 224), color)
    for x in range(20, 200, 22):
        for y in range(20, 200, 22):
            image.paste((240, 240, 240), (x, y, x + 8, y + 8))
    return image


class FakeHelper:
    def __init__(self):
        self.value = {'products': [{'id': 'a', 'name': '현재 사진', 'code': 'FOX-001', 'editorial': 'a.jpg'},
            {'id': 'b', 'name': '빨강 무늬 옷', 'code': 'FOX-002', 'editorial': 'b.jpg'},
            {'id': 'c', 'name': '다른 옷', 'code': 'FOX-003', 'editorial': 'c.jpg'}], 'active': 'a', 'revision': 3}
        self.calls = []
        self.fail_read = False

    def state(self):
        if self.fail_read:
            raise ValueError('연결 끊김')
        return copy.deepcopy(self.value)

    def activate(self, pid):
        self.calls.append(pid)
        self.value['active'] = pid
        self.value['revision'] += 1


class FakeVision:
    def encode(self, image):
        pixel = image.getpixel((0, 0))
        vector = np.zeros(512, np.float32)
        vector[0 if pixel[0] > pixel[2] else 1] = 1
        return vector

    def ready(self):
        return True


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.helper, self.vision = FakeHelper(), FakeVision()
        self.now = 100
        self.app = Assistant(self.tmp.name, self.helper, self.vision, lambda: self.now)
        self.red, self.blue = garment((190, 40, 30)), garment((30, 40, 190))
        self.app.capture = lambda: (self.red, self.red)
        self.app.refresh_products()
        self.app.index = {'a': np.eye(512,dtype=np.float32)[[2]], 'b': np.eye(512,dtype=np.float32)[[0]], 'c':np.eye(512,dtype=np.float32)[[1]]}
        self.app.index_key = product_key(self.helper.value['products'])
        self.app.start()

    def tearDown(self):
        self.app.close(); self.tmp.cleanup()

    def propose(self):
        for _ in range(3):
            self.app.tick(); self.now += 4
        self.assertIsNotNone(self.app.pending)
        return self.app.pending['id']

    def test_no_switch_from_start_poll_refresh_or_proposal(self):
        self.propose()
        for _ in range(3):
            self.app.public_state(); self.app.tick()
        self.assertEqual(self.helper.calls, [])

    def test_explicit_approval_only_and_replay_rejected(self):
        pid = self.propose()
        before = copy.deepcopy(self.helper.value['products'])
        self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, ['b'])
        self.assertEqual(before, self.helper.value['products'])
        with self.assertRaises(ValueError): self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, ['b'])

    def test_missing_consent_rejected(self):
        pid = self.propose()
        for value in [None, False, 1, 'true']:
            with self.assertRaises(ValueError): self.app.approve(pid, 'b', value)
        self.assertEqual(self.helper.calls, [])

    def test_changed_camera_at_click_rejected(self):
        pid = self.propose()
        self.app.capture = lambda: (self.blue, self.blue)
        with self.assertRaises(ValueError): self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, [])
        self.assertIsNone(self.app.pending)

    def test_changed_camera_clears_visible_candidate(self):
        self.propose(); self.app.capture = lambda: (self.blue, self.blue); self.app.tick()
        self.assertIsNone(self.app.pending)
        self.assertEqual(self.helper.calls, [])

    def test_fast_alternating_clothes_never_propose(self):
        for n in range(8):
            im = self.red if n % 2 else self.blue
            self.app.capture = lambda im=im: (im, im)
            self.app.tick()
        self.assertIsNone(self.app.pending)

    def test_dismissal_never_switches_and_suppresses_repeat(self):
        pid = self.propose(); self.app.dismiss(pid)
        for _ in range(5): self.app.tick()
        self.assertIsNone(self.app.pending)
        self.assertEqual(self.helper.calls, [])

    def test_timeout_rejected(self):
        pid = self.propose(); self.now += 30
        with self.assertRaises(ValueError): self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, [])

    def test_manual_selection_invalidates_approval(self):
        pid = self.propose(); self.helper.value['active'] = 'c'; self.helper.value['revision'] += 1
        with self.assertRaises(ValueError): self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, [])

    def test_product_edit_invalidates_approval(self):
        pid = self.propose(); self.helper.value['products'][1]['editorial'] = 'new.jpg'
        with self.assertRaises(ValueError): self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, [])

    def test_pause_invalidates_approval(self):
        pid = self.propose(); self.app.pause()
        with self.assertRaises(ValueError): self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, [])

    def test_unlisted_product_rejected(self):
        pid = self.propose()
        with self.assertRaises(ValueError): self.app.approve(pid, 'unknown', True)
        self.assertEqual(self.helper.calls, [])

    def test_disconnected_camera_rejected(self):
        pid = self.propose()
        def disconnected(): raise ValueError('OBS disconnected')
        self.app.capture = disconnected
        with self.assertRaises(ValueError): self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, [])
        self.assertIsNone(self.app.pending)

    def test_helper_disconnected_rejected(self):
        pid = self.propose(); self.helper.fail_read = True
        with self.assertRaises(ValueError): self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, [])

    def test_undo_is_manual_and_does_not_override_new_selection(self):
        pid = self.propose(); self.app.approve(pid, 'b', True)
        self.assertEqual(self.helper.calls, ['b'])
        self.helper.value['active'] = 'c'
        with self.assertRaises(ValueError): self.app.undo_switch(True)
        self.assertEqual(self.helper.calls, ['b'])

    def test_password_not_saved_or_exposed(self):
        self.app.configure({'host':'192.168.219.120','source':'DroidCam','password':'not-for-logs'})
        self.assertNotIn('not-for-logs', json.dumps(self.app.public_state()))
        self.assertNotIn('not-for-logs', (Path(self.tmp.name)/'config.json').read_text())

    def test_uniform_background_does_not_propose(self):
        blank = Image.new('RGB',(224,224),(100,100,100))
        self.app.capture=lambda:(blank,blank)
        for _ in range(4): self.app.tick()
        self.assertIsNone(self.app.pending)


if __name__ == '__main__':
    unittest.main()
