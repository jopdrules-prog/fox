"""Approval-only camera assistant. Existing FOX files are never modified."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time

import numpy as np
from connections import Helper, OBS, valid_obs_host, error_text
from vision import Vision, crop, decode, jpeg, motion_signature, movement, usable, orient


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, path)


def product_key(products):
    fields = [{k: p.get(k, '') for k in ('id', 'name', 'code', 'original', 'editorial')} for p in products]
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def card(p):
    return {k: p.get(k, '') for k in ('id', 'name', 'code', 'editorial', 'original')}


class Assistant:
    INTERVAL = 4.0
    TTL = 20.0
    STABLE_SAMPLES = 3

    def __init__(self, data, helper=None, vision=None, clock=time.monotonic):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.helper = helper or Helper()
        self.vision = vision or Vision(self.data / 'models')
        self.clock = clock
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.stop_event = threading.Event()
        self.running = False
        self.epoch = 0
        self.preparing = False
        self.status = 'OBS 카메라를 연결하고 상품 사진을 준비해 주세요.'
        self.error = ''
        self.config = {'host': '', 'port': 4455, 'source': 'DroidCamOBS', 'roi': [.15, .08, .85, .95], 'rotation': 0}
        self.password = ''  # Memory only: never returned, logged, or persisted.
        path = self.data / 'config.json'
        if path.exists():
            try:
                self.config.update(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass
        self.products = []
        self.index = {}
        self.index_key = ''
        self.active = None
        self.pending = None
        self.previous_frame = None
        self.previous_winner = None
        self.stable = 0
        self.suppressed = None
        self.suppressed_until = 0
        self.latest = None
        self.undo = None
        self.learnt = {}
        path = self.data / 'remembered.json'
        if path.exists():
            try:
                values = json.loads(path.read_text(encoding='utf-8'))
                for key, records in values.items():
                    valid = [v for v in records if isinstance(v, list) and len(v) == 512 and np.isfinite(v).all()]
                    self.learnt[key] = valid[-6:]
            except (OSError, ValueError, TypeError):
                pass

    def invalidate(self, message=None):
        self.pending = None
        self.previous_frame = None
        self.previous_winner = None
        self.stable = 0
        if message:
            self.status = message

    def pause(self):
        with self.lock:
            self.running = False
            self.epoch += 1
            self.invalidate('찾기를 멈췄어요. 현재 착용샷은 그대로예요.')

    def configure(self, values):
        host = valid_obs_host(values.get('host', ''))
        port = int(values.get('port', 4455))
        source = str(values.get('source', ''))
        roi = values.get('roi', [.15, .08, .85, .95])
        rotation = values.get('rotation', self.config.get('rotation', 0))
        if type(rotation) is not int or rotation not in (0, 90, 180, 270):
            raise ValueError('카메라 방향을 다시 선택해 주세요.')
        if not 1024 <= port <= 65535 or not source.strip() or len(source) > 200:
            raise ValueError('OBS 포트와 카메라 소스 이름을 확인해 주세요.')
        if (not isinstance(roi, list) or len(roi) != 4 or
                any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in roi) or
                roi[2] - roi[0] < .1 or roi[3] - roi[1] < .1):
            raise ValueError('옷을 확인할 영역을 조금 더 크게 잡아 주세요.')
        self.pause()
        with self.operation, self.lock:
            self.config = dict(host=host, port=port, source=source, roi=roi, rotation=rotation)
            if 'password' in values and values['password']:
                self.password = str(values['password'])[:512]
            save_json(self.data / 'config.json', self.config)
            self.latest = None
            self.error = ''
            self.status = '설정을 저장했어요. 카메라 연결 확인을 눌러 주세요.'

    def rotate_camera(self, turn):
        if type(turn) is not int or turn not in (-90, 90):
            raise ValueError('왼쪽 또는 오른쪽 회전 버튼을 눌러 주세요.')
        self.pause()
        with self.operation, self.lock:
            config = copy.deepcopy(self.config)
            config['rotation'] = (config.get('rotation', 0) + turn) % 360
            x1, y1, x2, y2 = config['roi']
            config['roi'] = ([1-y2, x1, 1-y1, x2] if turn == 90
                             else [y1, 1-x2, y2, 1-x1])
            save_json(self.data / 'config.json', config)
            self.config = config
            self.latest = None
            self.error = ''
            self.status = '카메라 방향을 저장했어요. 옷 영역을 확인한 뒤 찾기를 시작해 주세요.'
            return {'rotation': config['rotation'], 'roi': config['roi'][:]}

    def obs(self):
        with self.lock:
            values, password = copy.deepcopy(self.config), self.password
        return OBS(values['host'], values['port'], password)

    def capture(self):
        with self.obs() as obs:
            raw = obs.capture(self.config['source'])
        image = orient(decode(raw), self.config.get('rotation', 0))
        region = crop(image, self.config['roi'])
        return image, region

    def camera_check(self):
        self.pause()
        with self.operation:
            with self.lock:
                self.latest = None
            names = None
            stage = 'OBS 연결'
            try:
                with self.obs() as obs:
                    stage = '카메라 소스 목록 읽기'
                    sources = obs.call('GetInputList').get('inputs', [])
                    names = [i['inputName'] for i in sources if isinstance(i.get('inputName'), str)]
                    stage = '카메라 소스 이름 확인'
                    if self.config['source'] not in names:
                        raise ValueError('OBS에 입력한 카메라 이름이 없어요. 아래 실제 소스 목록에서 카메라를 선택하고 설정을 저장해 주세요.')
                    stage = '카메라 사진 가져오기'
                    raw = obs.capture(self.config['source'])
                stage = '카메라 사진 해독'
                image = decode(raw)
                stage = '카메라 방향 적용'
                image = orient(image, self.config.get('rotation', 0))
                stage = '미리보기 만들기'
                preview = jpeg(image)
                with self.lock:
                    self.latest = preview
                    self.error = ''
                    self.status = '카메라가 연결됐어요. 아래 미리보기에서 옷 영역을 확인해 주세요.'
                return {'sources': names, 'camera_ready': True}
            except Exception as exc:
                message = f'{stage} 실패 · {error_text(exc, self.password)}'
                with self.lock:
                    self.error = message
                    self.status = ('OBS 접속은 됐어요. 아래 카메라 확인 결과를 확인해 주세요.'
                                   if names is not None else 'OBS 연결을 확인해 주세요.')
                return {'sources': names or [], 'camera_ready': False, 'error': message}

    def refresh_products(self):
        state = self.helper.state()
        with self.lock:
            self.products = [card(p) for p in state['products'] if p.get('editorial')]
            self.active = state.get('active')
        return state

    def prepare(self):
        with self.lock:
            if self.preparing:
                return
            self.pause()
            self.preparing = True
            self.error = ''
        def task():
            try:
                with self.operation:
                    self.vision.install(self.message)
                    self.build_index()
                self.message('준비됐어요. 자동 찾기 시작을 눌러 주세요.')
            except Exception as exc:
                self.fail(exc)
            finally:
                with self.lock:
                    self.preparing = False
        threading.Thread(target=task, daemon=True).start()

    def build_index(self):
        state = self.refresh_products()
        products = self.products[:]
        if not products:
            raise ValueError('방송 도우미에 착용샷이 있는 상품을 먼저 등록해 주세요.')
        result = {}
        for i, p in enumerate(products):
            if self.stop_event.is_set():
                return
            self.message(f'상품 사진 준비 중 · {i + 1} / {len(products)}')
            filename = p.get('original') or p['editorial']
            raw = self.helper.photo(filename)
            values = self.vision.references(raw, original=bool(p.get('original')))
            saved = self.learnt.get(self.reference_id(p), [])
            if saved:
                values = np.concatenate([values, np.asarray(saved, dtype=np.float32)])
            result[p['id']] = values
        with self.lock:
            self.index = result
            self.index_key = product_key(state['products'])

    @staticmethod
    def reference_id(p):
        return p['id'] + ':' + p['editorial']

    def start(self):
        with self.operation:
            if self.preparing:
                raise ValueError('상품 사진을 준비하고 있어요. 잠시 기다려 주세요.')
            if not self.index:
                raise ValueError('화면 위의 상품 준비를 먼저 눌러 주세요.')
            state = self.refresh_products()
            if product_key(state['products']) != self.index_key:
                raise ValueError('상품 목록이 바뀌었어요. 상품 준비를 다시 눌러 주세요.')
            self.capture()  # Check that the selected camera actually works.
            with self.lock:
                self.epoch += 1
                self.running = True
                self.error = ''
                self.invalidate('옷이 잠깐 멈추면 후보를 찾아드릴게요.')

    def rank(self, vector):
        with self.lock:
            ranked = [(pid, float(np.max(values @ vector))) for pid, values in self.index.items()]
        return sorted(ranked, key=lambda x: x[1], reverse=True)[:3]

    def tick(self):
        with self.operation:
            with self.lock:
                if not self.running:
                    return
                epoch = self.epoch
            state = self.refresh_products()
            if product_key(state['products']) != self.index_key:
                self.pause()
                raise ValueError('상품 사진이 바뀌어 찾기를 멈췄어요. 상품 준비를 다시 눌러 주세요.')
            image, region = self.capture()
            signature = motion_signature(region)
            vector = self.vision.encode(region) if usable(region) else None
            now = self.clock()
            with self.lock:
                if not self.running or epoch != self.epoch:
                    return
                self.latest = jpeg(image)
                self.error = ''
                if vector is None:
                    self.invalidate('옷이 잘 보일 때까지 기다리고 있어요.'); return
                ranked = self.rank(vector)
                if not ranked or ranked[0][1] < .58:
                    self.invalidate('비슷한 착용샷을 찾지 못했어요. 옷을 정면으로 보여 주세요.'); return
                winner = ranked[0][0]
                if self.pending:
                    p = self.pending
                    if (now > p['expires'] or state['active'] != p['base_active'] or
                            float(p['vector'] @ vector) < .96 or
                            movement(p['signature'], signature) > .085):
                        self.invalidate('옷이나 선택이 바뀌었어요. 다시 확인할게요.')
                        return
                    self.status = '후보를 확인하고 바꿀 사진을 눌러 주세요.'
                    return
                if self.previous_frame is None or movement(self.previous_frame, signature) > .065 or self.previous_winner != winner:
                    self.stable = 1
                else:
                    self.stable += 1
                self.previous_frame, self.previous_winner = signature, winner
                if self.suppressed is not None:
                    if now < self.suppressed_until and float(self.suppressed @ vector) >= .94:
                        self.status = '그대로 유지 중이에요. 다른 옷이 보이면 다시 찾을게요.'; return
                    self.suppressed = None
                if self.stable < self.STABLE_SAMPLES:
                    self.status = f'옷을 확인하는 중 · {self.stable} / {self.STABLE_SAMPLES}'; return
                if winner == state['active'] and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] > .03):
                    self.status = '현재 착용샷과 비슷해요. 그대로 유지하고 있어요.'; return
                lookup = {p['id']: p for p in self.products}
                candidates = [dict(lookup[pid], score=round(score, 3)) for pid, score in ranked if pid in lookup]
                self.pending = {'id': secrets.token_urlsafe(20), 'created': now, 'expires': now + self.TTL,
                    'base_active': state['active'], 'base_revision': state['revision'], 'epoch': epoch,
                    'candidates': candidates, 'vector': vector, 'signature': signature,
                    'photo': jpeg(region)}
                self.status = '이 옷에 맞는 착용샷인가요? 사진을 확인해 주세요.'

    def approve(self, proposal_id, product_id, confirmed):
        if confirmed is not True:
            raise ValueError('바꿀 착용샷을 보고 동의 버튼을 눌러 주세요.')
        with self.operation:
            with self.lock:
                p = self.pending
                if not self.running or not p or not secrets.compare_digest(str(proposal_id), p['id']):
                    raise ValueError('이미 지나간 후보예요. 새 후보를 확인해 주세요.')
                if self.clock() > p['expires'] or p['epoch'] != self.epoch:
                    self.invalidate(); raise ValueError('후보 확인 시간이 지났어요. 다시 찾아드릴게요.')
                chosen = next((c for c in p['candidates'] if c['id'] == product_id), None)
                if chosen is None:
                    raise ValueError('화면에 보이는 후보 중에서 골라 주세요.')
            # A fresh camera sample is mandatory; no approval while disconnected.
            try:
                _, region = self.capture()
                current_vector = self.vision.encode(region)
                state = self.helper.state()
                with self.lock:
                    if (not self.running or self.epoch != p['epoch'] or self.pending is not p or
                            self.clock() > p['expires'] or not usable(region) or
                            float(current_vector @ p['vector']) < .96 or
                            movement(motion_signature(region), p['signature']) > .085 or
                            state['active'] != p['base_active'] or state['revision'] != p['base_revision'] or
                            product_key(state['products']) != self.index_key):
                        self.invalidate()
                        raise ValueError('카메라의 옷이나 상품 선택이 바뀌었어요. 최신 후보로 다시 확인해 주세요.')
                    # Consume before the request, including ambiguous HTTP failure. Never retry.
                    self.pending = None
                    self.helper.activate(product_id)
                    self.undo = {'from': product_id, 'to': state['active']}
                    self.active = product_id
                    self.suppressed, self.suppressed_until = current_vector, self.clock() + 30
                    self.invalidate('동의한 착용샷으로 바꿨어요. OBS에 연결된 사진도 따라 바뀝니다.')
            except Exception:
                with self.lock:
                    self.pending = None
                raise

    def dismiss(self, proposal_id):
        with self.lock:
            if self.pending and secrets.compare_digest(str(proposal_id), self.pending['id']):
                self.suppressed = self.pending['vector']
                self.suppressed_until = self.clock() + 60
                self.invalidate('현재 착용샷을 그대로 유지해요.')

    def undo_switch(self, confirmed):
        if confirmed is not True:
            raise ValueError('이전 착용샷으로 돌리기 버튼을 눌러 주세요.')
        with self.operation, self.lock:
            state = self.helper.state()
            if not self.undo or state['active'] != self.undo['from']:
                self.undo = None
                raise ValueError('다른 상품으로 이미 바뀌었어요. 방송 도우미에서 선택해 주세요.')
            old = self.undo['to']
            self.undo = None
            self.helper.activate(old)
            self.active = old
            self.invalidate('이전 착용샷으로 돌렸어요.')

    def remember(self, product_id, confirmed):
        if confirmed is not True:
            raise ValueError('옷과 착용샷을 확인한 뒤 기억하기를 눌러 주세요.')
        self.pause()
        with self.operation:
            self.refresh_products()
            p = next((p for p in self.products if p['id'] == product_id), None)
            if not p:
                raise ValueError('기억할 상품 사진을 골라 주세요.')
            _, region = self.capture()
            if not usable(region):
                raise ValueError('카메라에 옷이 잘 보이게 해 주세요.')
            vec = self.vision.encode(region)
            key = self.reference_id(p)
            with self.lock:
                self.learnt[key] = (self.learnt.get(key, []) + [vec.tolist()])[-6:]
                save_json(self.data / 'remembered.json', self.learnt)
                self.index = {}  # Rebuild the complete index; do not mix old metadata.
                self.status = '이 옷을 기억했어요. 이 화면 위의 상품 준비 → 자동 찾기 시작을 눌러 주세요.'

    def message(self, value):
        with self.lock:
            self.status = value

    def fail(self, exc):
        with self.lock:
            self.pending = None
            self.previous_frame = None
            self.stable = 0
            self.error = error_text(exc, self.password)
            self.status = '연결이나 준비 상태를 확인해 주세요. 착용샷은 그대로 유지해요.'

    def public_state(self):
        with self.lock:
            p = self.pending
            if p and self.clock() > p['expires']:
                self.invalidate('후보 확인 시간이 지났어요. 다시 확인할게요.'); p = None
            proposal = None if p is None else {'id': p['id'], 'remaining': max(0, p['expires'] - self.clock()),
                'candidates': p['candidates'], 'snapshot': 'data:image/jpeg;base64,' + base64.b64encode(p['photo']).decode()}
            return {'running': self.running, 'preparing': self.preparing, 'status': self.status,
                'error': self.error, 'config': copy.deepcopy(self.config), 'products': copy.deepcopy(self.products),
                'active': self.active, 'proposal': proposal, 'indexed': len(self.index),
                'model_ready': self.vision.ready(), 'password_set': bool(self.password), 'can_undo': bool(self.undo)}

    def loop(self):
        while not self.stop_event.is_set():
            if self.running:
                try:
                    self.tick()
                except Exception as exc:
                    self.fail(exc)
            elif not self.preparing:
                try:
                    self.refresh_products()
                except Exception as exc:
                    self.fail(exc)
            self.stop_event.wait(self.INTERVAL)

    def close(self):
        self.pause()
        self.stop_event.set()
