"""Trusted GitHub channel; stage while running, apply only on the next launch.

No OBS commands, credentials, product records or images enter this module.
Transport trust is HTTPS to the fixed user-owned repository; hashes detect
damaged packages. This is not a separately signed release channel.
"""
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import venv
import webbrowser
import zipfile

VERSION = '1.1.0'
APP = 'fox-camera-assist'
BASE = 'https://raw.githubusercontent.com/jopdrules-prog/fox/main/fox-camera/'
FEED = BASE + 'latest.json'
REQUIRED = {'auto_update.py', 'bootstrap.py', 'camera_server.py', 'assist.py',
            'connections.py', 'vision.py', 'requirements.txt', 'web/index.html',
            'web/app.js', 'web/style.css', 'START_CAMERA.cmd'}


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})', value):
        raise ValueError('업데이트 버전 형식이 맞지 않아요.')
    return tuple(map(int, value.split('.')))


def read_json(path, default=None):
    try:
        if path.stat().st_size > 100000:
            return default
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open('a+b')
    acquired = False
    try:
        if f.tell() == 0:
            f.write(b'0'); f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield
    finally:
        if acquired:
            f.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('업데이트 주소가 변경돼 다운로드를 멈췄어요.')


def fetch(url, limit):
    if url != FEED and not re.fullmatch(re.escape(BASE) + r'releases/\d+\.\d+\.\d+/package\.json', url):
        raise ValueError('허용되지 않은 업데이트 주소예요.')
    client = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(url, headers={'User-Agent': 'FOX-Camera-Updater/1.1', 'Cache-Control': 'no-cache'})
    with client.open(req, timeout=6) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('업데이트 파일이 너무 커요.')
    return raw


def unpack(raw, destination, expected_version):
    """Fully validate before writing any archive member."""
    package = json.loads(raw)
    if package.get('app') != APP or package.get('schema') != 1 or package.get('version') != expected_version:
        raise ValueError('카메라 도우미 업데이트 파일이 아니에요.')
    data = base64.b64decode(package['zip_base64'], validate=True)
    if hashlib.sha256(data).hexdigest() != package['zip_sha256']:
        raise ValueError('업데이트 파일 검사에 실패했어요.')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names, total = set(), 0
        for member in z.infolist():
            name = member.filename
            p = PurePosixPath(name)
            if (name in names or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name
                    or member.is_dir() or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('잘못된 업데이트 경로예요.')
            allowed = (len(p.parts) == 1 and p.suffix in ('.py', '.cmd', '.txt', '.md')) or (
                len(p.parts) == 2 and p.parts[0] in ('web', 'tests') and p.suffix in ('.py', '.js', '.css', '.html'))
            if not allowed or len(names) >= 100:
                raise ValueError('지원하지 않는 업데이트 파일이에요.')
            names.add(name); total += member.file_size
            if member.file_size > 1000000 or total > 6000000:
                raise ValueError('압축 파일이 너무 커요.')
        if not REQUIRED <= names:
            raise ValueError('업데이트에 필요한 파일이 빠졌어요.')
        files = {n: z.read(n) for n in names}
        if f"VERSION = '{expected_version}'" not in files['auto_update.py'].decode('utf-8'):
            raise ValueError('프로그램과 업데이트 버전이 달라요.')
        destination.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        atomic_json(destination / '.release.json', {n: hashlib.sha256(b).hexdigest() for n, b in files.items()})


def verified(root):
    hashes = read_json(root / '.release.json', {})
    try:
        return REQUIRED <= hashes.keys() and all(
            not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts
            and '\\' not in n and ':' not in n and (root / n).is_file()
            and hashlib.sha256((root / n).read_bytes()).hexdigest() == h
            for n, h in hashes.items())
    except OSError:
        return False


def status(data, **values):
    atomic_json(data / 'update-status.json', dict(checked_at=int(time.time()), **values))


def public_status(data):
    info = read_json(data / 'update-status.json', {})
    return {'current': VERSION, 'enabled': (data / 'auto-installed.json').is_file(),
            'pending': info.get('pending'), 'message': info.get('message', '새 버전 자동 확인 대기 중')}


def check(data, current, getter=fetch):
    """Downloads only. Never modifies installed.json, active code or user data."""
    try:
        with lock(data / 'update-download.lock'):
            manifest = json.loads(getter(FEED, 32000))
            candidate = manifest.get('version')
            if manifest.get('app') != APP or manifest.get('schema') != 1:
                raise ValueError('업데이트 안내 형식이 맞지 않아요.')
            if version(candidate) <= version(current):
                pending = read_json(data / 'update-pending.json', {}).get('version')
                status(data, pending=pending, message='새 버전을 받아 두었어요. 다음 실행 때 적용해요.' if pending else '자동 업데이트가 켜져 있어요. 현재 최신 버전이에요.')
                return None
            if candidate in read_json(data / 'update-blocked.json', []):
                status(data, message='실행에 실패한 새 버전을 건너뛰고 기존 버전을 사용해요.')
                return None
            url = BASE + f'releases/{candidate}/package.json'
            if manifest.get('url') != url:
                raise ValueError('업데이트 주소가 맞지 않아요.')
            raw = getter(url, 8000000)
            if hashlib.sha256(raw).hexdigest() != manifest.get('sha256'):
                raise ValueError('다운로드 검사에 실패했어요.')
            versions = data / 'versions'
            versions.mkdir(parents=True, exist_ok=True)
            staged = Path(tempfile.mkdtemp(prefix='.stage-', dir=versions))
            try:
                unpack(raw, staged, candidate)
                target = versions / candidate
                if target.exists():
                    if not verified(target):
                        raise ValueError('기존 다운로드 폴더 검사가 실패했어요.')
                else:
                    os.replace(staged, target)
                atomic_json(data / 'update-pending.json', {'version': candidate})
                status(data, pending=candidate, message=f'{candidate} 준비 완료 · 다음 실행 때 자동 적용해요.')
                return candidate
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
    except Exception:
        # Do not copy network errors / paths / credentials into operator UI.
        pending = read_json(data / 'update-pending.json', {}).get('version')
        status(data, pending=pending, message='업데이트 확인을 잠시 못 했어요. 현재 프로그램은 계속 사용할 수 있어요.')
        return None


def background(data, stop):
    if not (data / 'auto-installed.json').is_file():
        return
    def run():
        if stop.wait(45):
            return
        while not stop.is_set():
            check(data, VERSION)
            if stop.wait(3600):
                return
    threading.Thread(target=run, daemon=True, name='fox-update-download').start()


def health():
    try:
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with client.open('http://127.0.0.1:8772/api/health', timeout=1) as response:
            value = json.load(response)
        return value if value.get('app') == APP else None
    except (OSError, ValueError):
        return None


def runtime(data, root):
    signature = hashlib.sha256((root / 'requirements.txt').read_bytes()).hexdigest()
    legacy = data / 'runtime'
    marker = legacy / 'fox-camera-ready'
    if marker.is_file() and marker.read_text() == signature:
        return legacy / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    directory = data / 'runtimes' / signature
    python = directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    marker = directory / 'fox-camera-ready'
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(directory)
    if not marker.is_file() or marker.read_text() != signature:
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '-r', str(root / 'requirements.txt')], check=True)
        marker.write_text(signature)
    return python


def launch(data):
    if health():
        print('카메라 도우미가 이미 켜져 있어요. 현재 화면을 엽니다.', flush=True)
        webbrowser.open('http://127.0.0.1:8772'); return 0
    try:
        with lock(data / 'auto-launch.lock'):
            return launch_locked(data)
    except BlockingIOError:
        print('카메라 도우미가 시작 중이에요. 잠시 후 다시 열어 주세요.', flush=True)
        return 0


def launch_locked(data):
    installed = read_json(data / 'auto-installed.json', {})
    current = installed['current']; version(current)
    check(data, current)
    candidate = read_json(data / 'update-pending.json', {}).get('version', current)
    if version(candidate) <= version(current) or candidate in read_json(data / 'update-blocked.json', []):
        candidate = current
    old_root, root = data / 'versions' / current, data / 'versions' / candidate
    if not verified(root):
        candidate, root = current, old_root
    if not verified(root):
        raise ValueError('프로그램 파일 검사가 실패했어요. 기존 설치 폴더로 실행해 주세요.')
    backup = data / 'update-backups' / str(time.time_ns())
    if candidate != current:
        backup.mkdir(parents=True)
        for n in ('config.json', 'remembered.json'):
            if (data / n).is_file():
                shutil.copy2(data / n, backup / n)
    process = None
    try:
        process = start_and_confirm(data, root, candidate)
    except Exception:
        if candidate == current:
            raise
        blocked = read_json(data / 'update-blocked.json', [])
        atomic_json(data / 'update-blocked.json', sorted(set(blocked + [candidate])))
        for n in ('config.json', 'remembered.json'):
            if (backup / n).exists():
                shutil.copy2(backup / n, data / n)
            else:
                (data / n).unlink(missing_ok=True)
        (data / 'update-pending.json').unlink(missing_ok=True)
        status(data, message='새 버전 시작에 실패해 이전 버전으로 되돌렸어요.')
        process = start_and_confirm(data, old_root, current)
        candidate = current
    else:
        atomic_json(data / 'auto-installed.json', {'current': candidate, 'previous': current if candidate != current else installed.get('previous')})
        (data / 'update-pending.json').unlink(missing_ok=True)
        status(data, message='자동 업데이트가 켜져 있어요. 방송 중에는 재시작하지 않아요.')
    webbrowser.open('http://127.0.0.1:8772')
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        return 0


def start_and_confirm(data, root, expected):
    python = runtime(data, root)
    p = subprocess.Popen([str(python), '-u', str(root / 'camera_server.py'), '--data-dir', str(data), '--no-browser'], cwd=root)
    try:
        end = time.monotonic() + 30
        while time.monotonic() < end:
            if p.poll() is not None:
                raise RuntimeError('카메라 도우미 시작에 실패했어요.')
            h = health()
            if h and h.get('version') == expected:
                return p
            time.sleep(.3)
        raise RuntimeError('카메라 도우미 시작을 확인하지 못했어요.')
    except BaseException:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()
        raise
