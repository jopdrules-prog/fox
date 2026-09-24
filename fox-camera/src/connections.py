"""Authenticated, narrow clients for the existing helper and OBS 5.x."""
import base64
import hashlib
import http.cookiejar
import io
import ipaddress
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid


def error_text(exc, password=''):
    detail = ' '.join(str(exc).split()) or '상세 오류 문구 없음'
    if password:
        detail = detail.replace(password, '[비밀번호 숨김]')
    return f'{type(exc).__name__}: {detail}'[:700]


class Helper:
    def __init__(self, port=8770):
        self.base = f'http://127.0.0.1:{port}'
        self.lock = threading.RLock()
        self.cookies = http.cookiejar.CookieJar()
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, path, data=None):
        with self.lock:
            if not list(self.cookies):
                with self.client.open(self.base + '/', timeout=5) as response:
                    response.read(512 * 1024)
            request = urllib.request.Request(self.base + path,
                data=json.dumps(data).encode() if data is not None else None,
                headers={'Content-Type': 'application/json', 'X-FOX-Request': '1', 'Origin': self.base})
            try:
                with self.client.open(request, timeout=8) as response:
                    raw = response.read(32 * 1024 * 1024 + 1)
                    if len(raw) > 32 * 1024 * 1024:
                        raise ValueError('도우미의 사진 크기를 확인해 주세요.')
                    return raw
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    self.cookies.clear()
                raise ValueError('방송 도우미 연결을 확인해 주세요. 복구 폴더의 START_FOX.cmd로 켜 주세요.') from exc
            except OSError as exc:
                raise ValueError('방송 도우미가 응답하지 않아요. 먼저 기존 도우미를 켜 주세요.') from exc

    def state(self):
        value = json.loads(self.request('/api/state'))
        if not isinstance(value.get('products'), list) or not isinstance(value.get('revision'), int):
            raise ValueError('방송 도우미의 상품 목록을 확인할 수 없어요.')
        return value

    def photo(self, filename):
        return self.request('/media/' + urllib.parse.quote(filename, safe=''))

    def activate(self, product_id):
        # Only the explicit approval handler calls this; never retry mutations.
        return self.request('/api/active', {'id': product_id})


def valid_obs_host(value):
    try:
        addr = ipaddress.ip_address(str(value))
    except ValueError as exc:
        raise ValueError('OBS 노트북의 IPv4 주소를 입력해 주세요. 예: 192.168.219.120') from exc
    allowed = addr.version == 4 and (addr.is_loopback or any(addr in ipaddress.ip_network(net)
        for net in ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']))
    if not allowed:
        raise ValueError('같은 공유기에 연결된 OBS 노트북 주소만 사용할 수 있어요.')
    return str(addr)


class OBSRequestError(ValueError):
    def __init__(self, request, code, comment):
        self.request, self.code = request, code
        super().__init__(f'OBS {request} 오류 {code}: {comment or "상세 내용 없음"}')


class OBS:
    READ_ONLY = {'GetInputList', 'GetVersion', 'GetSourceScreenshot', 'GetSourceActive'}

    def __init__(self, host, port, password):
        self.host, self.port, self.password = valid_obs_host(host), int(port), password
        if not 1024 <= self.port <= 65535:
            raise ValueError('OBS 포트 번호를 확인해 주세요.')
        self.socket = None

    def __enter__(self):
        import websocket
        try:
            self.socket = websocket.create_connection(f'ws://{self.host}:{self.port}', timeout=5,
                http_no_proxy=['*'], suppress_origin=True)
            hello = json.loads(self.socket.recv())
            if hello.get('op') != 0:
                raise ValueError('OBS WebSocket 5 이상 연결이 필요해요.')
            identify = {'rpcVersion': 1, 'eventSubscriptions': 0}
            auth = hello.get('d', {}).get('authentication')
            if auth:
                secret = base64.b64encode(hashlib.sha256((self.password + auth['salt']).encode()).digest()).decode()
                identify['authentication'] = base64.b64encode(hashlib.sha256((secret + auth['challenge']).encode()).digest()).decode()
            self.socket.send(json.dumps({'op': 1, 'd': identify}))
            if json.loads(self.socket.recv()).get('op') != 2:
                raise ValueError('OBS 비밀번호를 확인해 주세요.')
            return self
        except Exception as exc:
            if self.socket:
                self.socket.close()
            raise ValueError('OBS 연결에 실패했어요. 노트북 주소·WebSocket 켜짐·비밀번호를 확인해 주세요.') from exc

    def call(self, request, data=None):
        if request not in self.READ_ONLY:
            raise ValueError('이 프로그램은 OBS 카메라를 읽기만 해요.')
        request_id = uuid.uuid4().hex
        self.socket.send(json.dumps({'op': 6, 'd': {'requestType': request, 'requestId': request_id,
                                                   'requestData': data or {}}}))
        for _ in range(20):
            raw = self.socket.recv()
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('OBS 응답 크기가 너무 커요.')
            message = json.loads(raw)
            if message.get('op') == 7 and message.get('d', {}).get('requestId') == request_id:
                result = message['d']
                status = result.get('requestStatus', {})
                if not status.get('result'):
                    comment = str(status.get('comment', ''))
                    if self.password:
                        comment = comment.replace(self.password, '[비밀번호 숨김]')
                    comment = ' '.join(comment.split())[:160]
                    raise OBSRequestError(request, status.get('code', '?'), comment)
                return result.get('responseData', {})
        raise ValueError('OBS 응답을 다시 확인해 주세요.')

    def capture(self, source):
        from PIL import Image
        active = self.call('GetSourceActive', {'sourceName': source})
        if not active.get('videoShowing'):
            raise ValueError('OBS에서 카메라가 보이지 않아요. 카메라 소스의 눈 아이콘을 확인해 주세요.')
        supported = self.call('GetVersion').get('supportedImageFormats', [])
        if not isinstance(supported, list):
            raise ValueError('OBS에서 지원하는 사진 형식 목록을 확인할 수 없어요.')
        supported = {str(item).lower() for item in supported}
        # Prefer the smaller JPEG response. At most one alternative PNG request.
        formats = (['jpg'] if 'jpg' in supported else ['jpeg'] if 'jpeg' in supported else [])
        if 'png' in supported:
            formats.append('png')
        if not formats:
            raise ValueError('OBS에서 JPG 또는 PNG 사진 형식을 지원하지 않아요.')
        failures = []
        for image_format in formats:
            try:
                values = {'sourceName': source, 'imageFormat': image_format,
                          'imageWidth': 640, 'imageHeight': 640}
                if image_format != 'png':
                    values['imageCompressionQuality'] = 82
                value = self.call('GetSourceScreenshot', values).get('imageData', '')
                if not isinstance(value, str) or not value.startswith('data:image/') or ';base64,' not in value:
                    raise ValueError('OBS에서 유효한 사진 데이터가 오지 않았어요.')
                raw = base64.b64decode(value.split(',', 1)[1], validate=True)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width * image.height > 40_000_000:
                        raise ValueError('OBS 사진 크기가 너무 커요.')
                    image.load()
                return raw
            except Exception as exc:
                failures.append(f'{image_format.upper()} · {error_text(exc, self.password)}')
        raise ValueError('카메라 사진 읽기 실패. ' + ' / '.join(failures))

    def __exit__(self, *_):
        if self.socket:
            self.socket.close()
