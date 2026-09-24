"""Independent runtime in LocalAppData; no changes to FOX helper or its venv."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent


def main():
    if not (3, 10) <= sys.version_info[:2] < (3, 14):
        raise RuntimeError('Python 3.10~3.13이 필요해요. 기존 도우미에서 사용하는 Python 3.12로 실행해 주세요.')
    data = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'FOX_Camera_Assist'
    if (data / 'auto-installed.json').is_file():
        from auto_update import launch
        return launch(data)
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with client.open('http://127.0.0.1:8772/api/health', timeout=2) as response:
            running = json.load(response)
            if running.get('app') == 'fox-camera-assist':
                if running.get('version') != '1.1.0':
                    print('이전 카메라 도우미가 켜져 있어요. FOX Camera Assist라고 적힌 검은 창을 닫고 다시 실행해 주세요.', flush=True)
                    return 1
                webbrowser.open('http://127.0.0.1:8772'); return 0
    except (OSError, ValueError):
        pass
    data = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'FOX_Camera_Assist'
    runtime = data / 'runtime'
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        print('카메라 도우미 실행 도구를 준비합니다. 기존 방송 도우미는 변경하지 않아요.', flush=True)
        venv.EnvBuilder(with_pip=True).create(runtime)
    marker = runtime / 'fox-camera-ready'
    signature = hashlib.sha256((ROOT / 'requirements.txt').read_bytes()).hexdigest()
    if not marker.is_file() or marker.read_text() != signature:
        print('처음 실행에는 무료 실행 도구를 내려받습니다. 인터넷 연결을 유지해 주세요.', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '-r', str(ROOT / 'requirements.txt')], check=True)
        marker.write_text(signature)
    return subprocess.call([str(python), '-u', str(ROOT / 'camera_server.py'), '--data-dir', str(data)], cwd=ROOT)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('시작하지 못했어요:', str(exc), flush=True)
        raise SystemExit(1)
