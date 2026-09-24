"""Local image retrieval. No image or embedding is sent to a cloud service."""
import hashlib
import io
import os
from pathlib import Path
import threading
import urllib.request

import numpy as np
from PIL import Image, ImageOps

REVISION = 'd15189d7028b43f1d3e65039190477f6af591c2a'
MODEL_URL = f'https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/{REVISION}/onnx/vision_model_quantized.onnx'
MODEL_SHA = '583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299'


def decode(raw):
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError('사진이 너무 커요.')
        return ImageOps.exif_transpose(image).convert('RGB')


def jpeg(image):
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=82)
    return out.getvalue()


def orient(image, rotation):
    """Clockwise quarter turns, shared by preview and all recognition paths."""
    turns = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
             270: Image.Transpose.ROTATE_90}
    return image.transpose(turns[rotation]) if rotation in turns else image


def crop(image, box):
    x1, y1, x2, y2 = box
    return image.crop((int(x1 * image.width), int(y1 * image.height),
                       max(int(x2 * image.width), int(x1 * image.width) + 1),
                       max(int(y2 * image.height), int(y1 * image.height) + 1)))


def motion_signature(image):
    return np.asarray(image.resize((32, 32)), dtype=np.float32) / 255


def movement(a, b):
    return float(np.abs(a - b).mean())


def usable(image):
    pixels = np.asarray(image.resize((64, 64)).convert('L'), dtype=np.float32)
    return pixels.std() > 10 and pixels.mean() > 15 and pixels.mean() < 246


class Vision:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'clip-vision.onnx'
        self.session = None
        self.lock = threading.Lock()

    def ready(self):
        return self.path.is_file()

    def install(self, progress):
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.path.is_file() and hashlib.sha256(self.path.read_bytes()).hexdigest() == MODEL_SHA:
            progress('인식 도구가 준비됐어요.'); return
        partial = self.path.with_suffix('.part')
        try:
            request = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'FOX-Camera-Assist/1.0'})
            with urllib.request.urlopen(request, timeout=40) as source, partial.open('wb') as target:
                total, digest = 0, hashlib.sha256()
                while True:
                    chunk = source.read(512 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 100_000_000:
                        raise ValueError('인식 도구 파일 크기가 예상과 달라요.')
                    target.write(chunk); digest.update(chunk)
                    progress(f'무료 인식 도구 받는 중 · {total // 1_000_000} / 약 89 MB')
                if digest.hexdigest() != MODEL_SHA:
                    raise ValueError('다운로드 확인에 실패했어요. 다시 준비해 주세요.')
            os.replace(partial, self.path)
            progress('인식 도구가 준비됐어요.')
        finally:
            partial.unlink(missing_ok=True)

    def encode(self, image):
        with self.lock:
            if self.session is None:
                if not self.ready():
                    raise ValueError('먼저 무료 인식 도구 준비를 눌러 주세요.')
                if hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA:
                    raise ValueError('인식 도구 파일을 다시 준비해 주세요.')
                import onnxruntime as ort
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                opts.inter_op_num_threads = 1
                opts.log_severity_level = 3
                self.session = ort.InferenceSession(str(self.path), sess_options=opts,
                                                   providers=['CPUExecutionProvider'])
            image = ImageOps.fit(image.convert('RGB'), (224, 224), method=Image.Resampling.BICUBIC)
            array = np.asarray(image, dtype=np.float32) / 255.0
            array = (array - np.array([.48145466, .4578275, .40821073], np.float32)) / np.array([.26862954, .26130258, .27577711], np.float32)
            outputs = self.session.run(['image_embeds'], {'pixel_values': array.transpose(2, 0, 1)[None]})
            result = outputs[0][0]
            return result / max(float(np.linalg.norm(result)), 1e-10)

    def references(self, raw, original=False):
        key = hashlib.sha256(b'fox-camera-crops-v1' + bytes([original]) + raw).hexdigest()
        cached = self.directory / (key + '.npy')
        if cached.is_file():
            try:
                values = np.load(cached, allow_pickle=False)
                if values.ndim == 2 and values.shape[1] == 512 and np.isfinite(values).all():
                    return values
            except (ValueError, OSError):
                pass
        image = decode(raw)
        boxes = [(0, 0, 1, 1)] if original else [(.18, .17, .82, .63), (.18, .40, .82, .95)]
        values = np.stack([self.encode(crop(image, box)) for box in boxes])
        self.directory.mkdir(parents=True, exist_ok=True)
        np.save(cached, values, allow_pickle=False)
        return values
