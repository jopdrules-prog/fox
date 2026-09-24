"""Regress Windows exclusive-bind conflict using the actual TCPServer bind path."""
import json
import contextlib
import io
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camera_server
import bootstrap


class WindowsSocket:
    EXCLUSIVE = -5  # Stand-in constant so the Windows branch also runs on Linux.

    def __init__(self):
        self.options = {}
        self.bound = False

    def setsockopt(self, level, option, value):
        self.options[option] = value
        if self.options.get(self.EXCLUSIVE) and self.options.get(socket.SO_REUSEADDR):
            raise OSError(10022, 'Winsock exclusive and reuse options conflict')

    def bind(self, address):
        self.bound = True
        self.address = address

    def getsockname(self):
        return self.address


class StartupTests(unittest.TestCase):
    def test_old_running_version_does_not_silently_open_old_page(self):
        with patch.object(bootstrap.urllib.request, 'build_opener') as make_client, \
             patch.object(bootstrap.webbrowser, 'open') as open_browser, \
             patch.object(bootstrap.subprocess, 'call') as run_child, \
             contextlib.redirect_stdout(io.StringIO()) as message:
            make_client.return_value.open.return_value.__enter__.return_value = io.StringIO(
                json.dumps({'app': 'fox-camera-assist', 'version': '1.0.1'}))
            self.assertEqual(bootstrap.main(), 1)
            open_browser.assert_not_called()
            run_child.assert_not_called()
            self.assertIn('이전 카메라 도우미', message.getvalue())

    def test_windows_exclusive_bind_avoids_inherited_reuse(self):
        server = camera_server.Server.__new__(camera_server.Server)
        server.socket = WindowsSocket()
        server.server_address = ('0.0.0.0', 8772)
        with patch.object(camera_server, 'os', SimpleNamespace(name='nt')), \
             patch.object(socket, 'SO_EXCLUSIVEADDRUSE', WindowsSocket.EXCLUSIVE, create=True):
            server.server_bind()
        self.assertTrue(server.socket.bound)
        self.assertEqual(server.socket.options[WindowsSocket.EXCLUSIVE], 1)
        self.assertFalse(server.socket.options.get(socket.SO_REUSEADDR))

    def test_real_server_health_and_occupied_port(self):
        server = camera_server.Server(('127.0.0.1', 0), None)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f'http://127.0.0.1:{server.server_port}/api/health', timeout=3) as response:
                self.assertEqual(json.load(response), {'app': 'fox-camera-assist', 'version': '1.1.0'})
            with self.assertRaises(OSError):
                camera_server.Server(('127.0.0.1', server.server_port), None)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
