"""LAN companion UI, separately authenticated. Never changes OBS scenes/settings."""
import argparse
import errno
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import socketserver
import threading
import time
from urllib.parse import urlparse, parse_qs
import webbrowser

import auto_update
from assist import Assistant
from connections import Helper, error_text
from vision import decode, jpeg

ROOT = Path(__file__).resolve().parent


def default_data():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'FOX_Camera_Assist'


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        if os.name == 'nt':
            # HTTPServer inherits address reuse. Winsock cannot combine it with
            # exclusive use; TCPServer.server_bind would otherwise enable both.
            self.allow_reuse_address = False
            self.allow_reuse_port = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def __init__(self, address, assistant):
        super().__init__(address, Handler)
        self.assistant = assistant
        self.sessions, self.attempts = {}, {}
        self.auth_lock = threading.Lock()
        self.pin = ''.join(secrets.choice('0123456789') for _ in range(8))
        self.hosts = {'127.0.0.1', 'localhost', socket.gethostname().lower()}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(('192.0.2.1', 80))
                self.hosts.add(sock.getsockname()[0])
        except OSError:
            pass
        self.thumbnails = {}


class Handler(BaseHTTPRequestHandler):
    server_version = 'FOXCameraAssist/1.1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *_):
        pass

    def local(self):
        return self.client_address[0] in ('127.0.0.1', '::1')

    def headers_ok(self):
        host = self.headers.get('Host', '')
        try:
            parsed = urlparse('http://' + host)
            good = parsed.hostname in self.server.hosts and parsed.port == self.server.server_port
        except ValueError:
            return False
        return good and self.headers.get('Origin', 'http://' + host) == 'http://' + host

    def authorized(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            sid = cookies.get('fox_camera_session')
            with self.server.auth_lock:
                return bool(sid and self.server.sessions.get(sid.value, 0) > time.time())
        except Exception:
            return False

    def new_session(self):
        sid = secrets.token_urlsafe(32)
        with self.server.auth_lock:
            self.server.sessions = {k: t for k, t in self.server.sessions.items() if t > time.time()}
            self.server.sessions[sid] = time.time() + 86400
        return f'fox_camera_session={sid}; Path=/; SameSite=Strict; HttpOnly; Max-Age=86400'

    def send(self, status, value, mime='application/json; charset=utf-8', cookie=None):
        if not isinstance(value, bytes):
            value = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(value)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(value)

    def do_GET(self):
        try:
            self.get()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as exc:
            self.send(400, {'error': error_text(exc, self.server.assistant.password)})

    def get(self):
        if not self.headers_ok():
            return self.send(403, {'error': '접속 주소를 확인해 주세요.'})
        url = urlparse(self.path)
        if url.path == '/api/health' and self.local():
            return self.send(200, {'app': 'fox-camera-assist', 'version': '1.1.0'})
        static = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
        if url.path in static:
            p = ROOT / 'web' / static[url.path]
            cookie = self.new_session() if url.path == '/' and self.local() and not self.authorized() else None
            return self.send(200, p.read_bytes(), mimetypes.guess_type(p.name)[0] or 'text/plain', cookie)
        if not self.authorized():
            return self.send(401, {'error': '메인 데스크탑에 보이는 카메라 도우미 연결번호를 입력해 주세요.'})
        app = self.server.assistant
        if url.path == '/api/state':
            state = app.public_state()
            state.update(updates=auto_update.public_status(app.data), local=self.local(), pin=self.server.pin if self.local() else None,
                         addresses=[f'http://{h}:{self.server.server_port}' for h in sorted(self.server.hosts) if h not in ('127.0.0.1', 'localhost')])
            return self.send(200, state)
        if url.path == '/api/preview':
            with app.lock:
                raw = app.latest
            return self.send(200, raw, 'image/jpeg') if raw else self.send(404, {'error': '카메라 연결 확인을 먼저 눌러 주세요.'})
        if url.path == '/api/photo':
            pid = parse_qs(url.query).get('id', [''])[0]
            with app.lock:
                p = next((p for p in app.products if p['id'] == pid), None)
            if not p:
                return self.send(404, {'error': '상품 사진이 없어요.'})
            key = hashlib.sha256(p['editorial'].encode()).hexdigest()
            raw = self.server.thumbnails.get(key)
            if not raw:
                image = decode(app.helper.photo(p['editorial']))
                image.thumbnail((480, 854))
                raw = jpeg(image)
                if len(self.server.thumbnails) > 200:
                    self.server.thumbnails.clear()
                self.server.thumbnails[key] = raw
            return self.send(200, raw, 'image/jpeg')
        return self.send(404, {'error': '찾을 수 없는 화면이에요.'})

    def do_POST(self):
        try:
            self.post()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as exc:
            self.send(400, {'error': error_text(exc, self.server.assistant.password)})

    def post(self):
        if not self.headers_ok() or self.headers.get('X-FOX-Camera') != '1':
            return self.send(403, {'error': '카메라 도우미 화면에서 눌러 주세요.'})
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 8192:
            raise ValueError('입력 내용이 너무 커요.')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError('요청이 중단됐어요.')
        values = json.loads(raw)
        if not isinstance(values, dict):
            raise ValueError('요청 형식을 확인해 주세요.')
        path = urlparse(self.path).path
        if path == '/api/pair':
            with self.server.auth_lock:
                now = time.time()
                count, since = self.server.attempts.get(self.client_address[0], (0, now))
                if now - since > 300:
                    count, since = 0, now
                if count >= 10:
                    return self.send(429, {'error': '연결번호를 확인하고 5분 뒤 다시 입력해 주세요.'})
                if not secrets.compare_digest(str(values.get('pin', '')), self.server.pin):
                    self.server.attempts[self.client_address[0]] = (count + 1, since)
                    return self.send(403, {'error': '연결번호가 맞지 않아요.'})
                self.server.attempts.pop(self.client_address[0], None)
            return self.send(200, {'ok': True}, cookie=self.new_session())
        if not self.authorized():
            return self.send(401, {'error': '먼저 연결번호를 입력해 주세요.'})
        app = self.server.assistant
        # Paired operators can prepare products as well as start and approve.
        # Connection credentials and initial camera setup stay on the desktop.
        local_only = {'/api/config', '/api/check', '/api/rotate'}
        if path in local_only and not self.local():
            return self.send(403, {'error': '처음 설정은 메인 데스크탑에서 해 주세요.'})
        if path == '/api/config':
            app.configure(values)
        elif path == '/api/rotate':
            return self.send(200, app.rotate_camera(values.get('turn')))
        elif path == '/api/check':
            return self.send(200, app.camera_check())
        elif path == '/api/prepare':
            app.prepare()
        elif path == '/api/refresh':
            app.refresh_products()
        elif path == '/api/start':
            app.start()
        elif path == '/api/pause':
            app.pause()
        elif path == '/api/approve':
            app.approve(values.get('proposal'), values.get('product'), values.get('confirmed'))
        elif path == '/api/dismiss':
            app.dismiss(values.get('proposal'))
        elif path == '/api/undo':
            app.undo_switch(values.get('confirmed'))
        elif path == '/api/remember':
            app.remember(values.get('product'), values.get('confirmed'))
        else:
            return self.send(404, {'error': '지원하지 않는 동작이에요.'})
        return self.send(200, {'ok': True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8772)
    parser.add_argument('--helper-port', type=int, default=8770)
    parser.add_argument('--data-dir', type=Path, default=default_data())
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    app = Assistant(args.data_dir, helper=Helper(args.helper_port))
    try:
        server = Server(('0.0.0.0', args.port), app)
    except OSError as exc:
        app.close()
        if exc.errno == errno.EADDRINUSE or getattr(exc, 'winerror', None) == 10048:
            print(f'{args.port} 포트를 다른 프로그램이 사용하고 있어요.')
        else:
            print(f'카메라 도우미의 {args.port} 포트를 열지 못했어요.')
        print(f'오류 내용: {exc}', flush=True)
        print('이 창의 오류 내용이 보이게 사진을 보내 주세요.', flush=True)
        return 1
    # Reduce contention with OBS / messaging apps; two inference threads only.
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    auto_update.background(args.data_dir, app.stop_event)
    threading.Thread(target=app.loop, daemon=True).start()
    print('FOX 카메라 도우미 · 동의한 경우에만 착용샷을 바꿉니다.', flush=True)
    print(f'이 컴퓨터: http://127.0.0.1:{server.server_port}', flush=True)
    print('다른 기기 연결번호:', server.pin, flush=True)
    if not args.no_browser:
        webbrowser.open(f'http://127.0.0.1:{server.server_port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
