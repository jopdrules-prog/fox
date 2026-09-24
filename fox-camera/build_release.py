"""Build clean public package and an offline, one-run migration installer."""
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'src'
VERSION = '1.1.0'
OUT = ROOT / 'release'
OUT.mkdir(exist_ok=True)
files = {}
for p in sorted(APP.rglob('*')):
    if p.is_file() and '__pycache__' not in p.parts and not p.name.startswith('.'):
        files[p.relative_to(APP).as_posix()] = p.read_bytes()
stream = io.BytesIO()
with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
    for n, b in files.items():
        info = zipfile.ZipInfo(n, (2026, 9, 24, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        z.writestr(info, b)
raw = stream.getvalue()
package = {'schema': 1, 'app': 'fox-camera-assist', 'version': VERSION,
           'zip_sha256': hashlib.sha256(raw).hexdigest(),
           'zip_base64': base64.b64encode(raw).decode()}
body = json.dumps(package, ensure_ascii=False, separators=(',', ':')).encode()
(OUT / 'package.json').write_bytes(body)
manifest = {'schema': 1, 'app': 'fox-camera-assist', 'version': VERSION,
            'url': f'https://raw.githubusercontent.com/jopdrules-prog/fox/main/fox-camera/releases/{VERSION}/package.json',
            'sha256': hashlib.sha256(body).hexdigest()}
(OUT / 'latest.json').write_text(json.dumps(manifest, indent=2)+'\n')

launcher = '''from pathlib import Path
import json, sys, re
data = Path(__file__).resolve().parent
try:
    current = json.loads((data / 'auto-installed.json').read_text(encoding='utf-8'))['current']
    if not re.fullmatch(r'\\d+\\.\\d+\\.\\d+', current):
        raise ValueError('Invalid installed version')
    sys.path.insert(0, str(data / 'versions' / current))
    from auto_update import launch
    raise SystemExit(launch(data))
except Exception as exc:
    print('카메라 도우미 시작 오류:', str(exc), flush=True)
    raise SystemExit(1)
'''
start = files['START_CAMERA.cmd'].decode('utf-8-sig').replace('bootstrap.py', 'launcher.py')
installer = '''import base64, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
VERSION = '1.1.0'
PAYLOAD = __PAYLOAD__
EXPECTED = __HASH__
LAUNCHER = __LAUNCHER__
START = __START__
def main():
    if os.name != 'nt':
        raise RuntimeError('Windows 메인 데스크탑에서 실행해 주세요.')
    if not (3, 10) <= sys.version_info[:2] < (3, 14):
        raise RuntimeError('Python 3.10~3.13이 필요해요.')
    data = Path(os.environ['LOCALAPPDATA']) / 'FOX_Camera_Assist'
    versions = data / 'versions'; versions.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(PAYLOAD, validate=True)
    if hashlib.sha256(raw).hexdigest() != EXPECTED:
        raise ValueError('설정 파일 검사에 실패했어요.')
    temp = Path(tempfile.mkdtemp(prefix='.setup-', dir=versions))
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for n in z.namelist():
                p = Path(n)
                if p.is_absolute() or '..' in p.parts or ':' in n or '\\\\' in n:
                    raise ValueError('Invalid package path')
                t = temp / p; t.parent.mkdir(parents=True, exist_ok=True); t.write_bytes(z.read(n))
        sys.path.insert(0, str(temp))
        from auto_update import atomic_json, verified, version, health
        atomic_json(temp / '.release.json', {n: hashlib.sha256((temp / n).read_bytes()).hexdigest() for n in z.namelist()})
        target = versions / VERSION
        if target.exists():
            if not verified(target):
                raise ValueError('이미 설치된 프로그램 검사에 실패했어요. 기존 폴더는 변경하지 않았어요.')
        else:
            os.replace(temp, target)
        state = data / 'auto-installed.json'
        if not state.exists():
            atomic_json(state, {'current': VERSION, 'previous': None})
        else:
            old = json.loads(state.read_text(encoding='utf-8'))
            if version(old['current']) < version(VERSION):
                atomic_json(data / 'update-pending.json', {'version': VERSION})
        (data / 'launcher.py').write_text(LAUNCHER, encoding='utf-8')
        (data / 'START_CAMERA.cmd').write_bytes(START.replace('\\r\\n','\\n').replace('\\n','\\r\\n').encode('utf-8'))
        # Current user's Desktop, including OneDrive redirection. No elevation.
        import ctypes
        desktop = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, desktop)
        if result != 0:
            raise RuntimeError('바탕 화면 위치를 찾지 못했어요.')
        shortcut = Path(desktop.value) / 'FOX 카메라 도우미.cmd'
        line = '@echo off\\r\\ncall "%LOCALAPPDATA%\\\\FOX_Camera_Assist\\\\START_CAMERA.cmd"\\r\\n'
        shortcut.write_bytes(line.encode('utf-8'))
        print('자동 업데이트 설정 파일을 설치했어요.', flush=True)
        print('앞으로 바탕 화면의 FOX 카메라 도우미로 실행하세요.', flush=True)
        if health():
            print('현재 켜진 카메라 도우미는 그대로 두었어요.', flush=True)
            print('방송이 끝난 뒤 FOX Camera Assist 창만 닫고, 바탕 화면의 새 아이콘을 실행해 주세요.', flush=True)
        else:
            subprocess.Popen(['cmd.exe', '/c', 'start', '', str(data / 'START_CAMERA.cmd')])
    finally:
        if temp.exists(): shutil.rmtree(temp)
if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print('설정하지 못했어요:', str(exc), flush=True)
        raise SystemExit(1)
'''
installer = installer.replace('__PAYLOAD__', repr(base64.b64encode(raw).decode())).replace('__HASH__', repr(hashlib.sha256(raw).hexdigest())).replace('__LAUNCHER__', repr(launcher)).replace('__START__', repr(start))
compile(installer, 'installer.py', 'exec')
(OUT / 'installer.py').write_text(installer, encoding='utf-8')
header = r'''@echo off
chcp 65001 >nul
setlocal
title FOX Camera Auto Update Setup
set "FOX_CAMERA_SETUP_SELF=%~f0"
set "FOX_CAMERA_SETUP_PY=%TEMP%\FOX_Camera_Setup_%RANDOM%_%RANDOM%.py"
powershell -NoProfile -Command "$s=[IO.File]::ReadAllText($env:FOX_CAMERA_SETUP_SELF); $b=($s -split '(?m)^:FOX_INSTALL_SCRIPT\r?$',2)[1].Trim(); [IO.File]::WriteAllBytes($env:FOX_CAMERA_SETUP_PY,[Convert]::FromBase64String($b))"
if errorlevel 1 goto failed
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%FOX_CAMERA_SETUP_PY%"
  goto finished
)
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.12 "%FOX_CAMERA_SETUP_PY%"
  goto finished
)
python -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14)" >nul 2>&1
if not errorlevel 1 (
  python "%FOX_CAMERA_SETUP_PY%"
  goto finished
)
py -3 -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14)" >nul 2>&1
if not errorlevel 1 (
  py -3 "%FOX_CAMERA_SETUP_PY%"
  goto finished
)
echo Python 3.12 was not found. Please send a photo of this window.
goto finished
:failed
echo Setup could not start. Please send a photo of this window.
:finished
del "%FOX_CAMERA_SETUP_PY%" >nul 2>&1
pause
exit /b
:FOX_INSTALL_SCRIPT
'''
content = header.replace('\n','\r\n') + base64.b64encode(installer.encode()).decode()+'\r\n'
(OUT / 'FOX_Camera_Auto_Update_Setup.cmd').write_bytes(content.encode('utf-8'))
print('Built', len(files), 'files;', len(content), 'byte one-run installer')
