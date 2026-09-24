"""Exercise the paired laptop workflow through a real HTTP server."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assist import Assistant
from camera_server import Server
from test_approval import FakeHelper, FakeVision, garment


class CatalogHelper(FakeHelper):
    def photo(self, filename):
        return filename.encode()


class CatalogVision(FakeVision):
    def install(self, message):
        pass

    def references(self, raw, original=False):
        # Only a remembered camera example can match the red test garment.
        return np.eye(512, dtype=np.float32)[[2]]


class LaptopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.helper = CatalogHelper()
        self.app = Assistant(self.tmp.name, self.helper, CatalogVision())
        red = garment((190, 40, 30))
        self.app.capture = lambda: (red, red)
        self.server = Server(('127.0.0.1', 0), self.app)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.cookie = ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=3)
        self.app.close()
        self.tmp.cleanup()

    def request(self, path, values=None, origin=None):
        # A second loopback address exercises the actual non-local branch.
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port,
                                            timeout=3, source_address=('127.0.0.2', 0))
        headers = {'Cookie': self.cookie, 'Origin': origin or f'http://127.0.0.1:{self.server.server_port}'}
        if values is not None:
            headers.update({'Content-Type': 'application/json', 'X-FOX-Camera': '1'})
        client.request('POST' if values is not None else 'GET', path,
                       body=None if values is None else json.dumps(values), headers=headers)
        response = client.getresponse()
        if response.getheader('Set-Cookie'):
            self.cookie = response.getheader('Set-Cookie').split(';', 1)[0]
        result = response.status, json.loads(response.read())
        client.close()
        return result

    def pair(self):
        self.assertEqual(self.request('/api/pair', {'pin': self.server.pin})[0], 200)

    def test_laptop_remember_prepare_start_approve_and_undo(self):
        self.pair()
        status, state = self.request('/api/state')
        self.assertEqual(status, 200)
        self.assertFalse(state['local'])
        self.assertIsNone(state['pin'])
        self.assertEqual(self.request('/api/remember', {'product': 'b', 'confirmed': True})[0], 200)
        self.assertEqual(self.request('/api/state')[1]['indexed'], 0)
        self.assertEqual(self.request('/api/prepare', {})[0], 200)
        deadline = time.monotonic() + 3
        while self.app.preparing and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.app.preparing)
        state = self.request('/api/state')[1]
        self.assertEqual(state['indexed'], 3)
        self.assertEqual(state['error'], '')
        self.assertEqual(self.request('/api/start', {})[0], 200)
        for _ in range(3):
            self.app.tick()
        state = self.request('/api/state')[1]
        self.assertEqual(state['proposal']['candidates'][0]['id'], 'b')
        self.assertEqual(self.helper.calls, [])
        values = {'proposal': state['proposal']['id'], 'product': 'b', 'confirmed': True}
        self.assertEqual(self.request('/api/approve', values)[0], 200)
        self.assertEqual(self.helper.calls, ['b'])
        self.assertEqual(self.request('/api/state')[1]['active'], 'b')
        self.assertEqual(self.request('/api/undo', {'confirmed': True})[0], 200)
        self.assertEqual(self.helper.calls, ['b', 'a'])

    def test_prepare_requires_pairing_and_same_origin(self):
        self.assertEqual(self.request('/api/prepare', {})[0], 401)
        self.assertEqual(self.request('/api/state')[0], 401)
        self.pair()
        self.assertEqual(self.request('/api/prepare', {}, origin='http://untrusted.invalid')[0], 403)
        for path in ('/api/config', '/api/check', '/api/rotate'):
            self.assertEqual(self.request(path, {})[0], 403)
        self.assertEqual(self.request('/api/approve', {'confirmed': False})[0], 400)
        self.assertEqual(self.helper.calls, [])


if __name__ == '__main__':
    unittest.main()
