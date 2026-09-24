import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from assist import Assistant
from connections import OBS, OBSRequestError


class ReplySocket:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []

    def send(self, raw):
        self.sent.append(json.loads(raw)['d'])

    def recv(self):
        status, data = next(self.responses)
        return json.dumps({'op': 7, 'd': {'requestId': self.sent[-1]['requestId'],
            'requestStatus': status, 'responseData': data}})


class CameraOBS:
    def __init__(self, names, error=None):
        self.names, self.error = names, error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def call(self, name):
        if name != 'GetInputList':
            raise AssertionError(name)
        return {'inputs': [{'inputName': n} for n in self.names]}

    def capture(self, source):
        if self.error:
            raise self.error
        out = io.BytesIO()
        Image.new('RGB', (40, 60), 'green').save(out, 'PNG')
        return out.getvalue()


class CameraTests(unittest.TestCase):
    def test_obs_failure_retains_request_code_and_redacts_password(self):
        client = OBS('127.0.0.1', 4455, 'private-test-value')
        client.socket = ReplySocket([({'result': False, 'code': 600,
            'comment': 'No source private-test-value'}, {})])
        with self.assertRaises(OBSRequestError) as failure:
            client.call('GetSourceActive', {'sourceName': 'DroidCam'})
        self.assertIn('GetSourceActive', str(failure.exception))
        self.assertIn('600', str(failure.exception))
        self.assertNotIn('private-test-value', str(failure.exception))

    def test_supported_png_screenshot_and_read_only_requests(self):
        client = OBS('127.0.0.1', 4455, '')
        success = {'result': True, 'code': 100}
        raw = CameraOBS([]).capture('camera')
        client.socket = ReplySocket([(success, {'videoShowing': True}),
            (success, {'supportedImageFormats': ['png']}),
            (success, {'imageData': 'data:image/png;base64,' + base64.b64encode(raw).decode()})])
        self.assertEqual(client.capture('DroidCam '), raw)
        request = client.socket.sent[-1]
        self.assertEqual(request['requestData']['imageFormat'], 'png')
        self.assertEqual(request['requestData']['sourceName'], 'DroidCam ')
        with self.assertRaises(ValueError):
            client.call('SetCurrentProgramScene', {})

    def test_jpeg_is_preferred_when_obs_supports_it(self):
        client = OBS('127.0.0.1', 4455, '')
        out = io.BytesIO()
        Image.new('RGB', (80, 40), 'red').save(out, 'JPEG')
        raw = out.getvalue()
        client.call = Mock(side_effect=[{'videoShowing': True},
            {'supportedImageFormats': ['png', 'jpg', 'jpeg']},
            {'imageData': 'data:image/jpeg;base64,' + base64.b64encode(raw).decode()}])
        self.assertEqual(client.capture('DroidCamOBS'), raw)
        self.assertEqual(client.call.call_args.args[1]['imageFormat'], 'jpg')
        self.assertEqual(client.call.call_args.args[1]['imageCompressionQuality'], 82)

    def test_empty_jpeg_exception_falls_back_once_to_png(self):
        client = OBS('127.0.0.1', 4455, '')
        raw = CameraOBS([]).capture('camera')
        client.call = Mock(side_effect=[{'videoShowing': True},
            {'supportedImageFormats': ['jpg', 'png']}, AssertionError(),
            {'imageData': 'data:image/png;base64,' + base64.b64encode(raw).decode()}])
        self.assertEqual(client.capture('DroidCamOBS'), raw)
        self.assertEqual([call.args[1]['imageFormat'] for call in client.call.call_args_list[2:]], ['jpg', 'png'])

    def test_corrupt_jpeg_falls_back_to_valid_png(self):
        client = OBS('127.0.0.1', 4455, '')
        raw = CameraOBS([]).capture('camera')
        client.call = Mock(side_effect=[{'videoShowing': True},
            {'supportedImageFormats': ['jpeg', 'png']},
            {'imageData': 'data:image/jpeg;base64,' + base64.b64encode(b'broken').decode()},
            {'imageData': 'data:image/png;base64,' + base64.b64encode(raw).decode()}])
        self.assertEqual(client.capture('DroidCamOBS'), raw)
        self.assertEqual(client.call.call_count, 4)

    def test_failed_formats_report_nonempty_error_and_redact_password(self):
        client = OBS('127.0.0.1', 4455, 'private-test-value')
        client.call = Mock(side_effect=[{'videoShowing': True},
            {'supportedImageFormats': ['jpg', 'png']}, AssertionError(),
            ValueError('bad private-test-value')])
        with self.assertRaises(ValueError) as failure:
            client.capture('DroidCamOBS')
        message = str(failure.exception)
        self.assertIn('JPG', message)
        self.assertIn('PNG', message)
        self.assertIn('AssertionError', message)
        self.assertNotIn('private-test-value', message)
        self.assertEqual(client.call.call_count, 4)

    def test_invisible_camera_stops_without_screenshot_requests(self):
        client = OBS('127.0.0.1', 4455, '')
        client.call = Mock(return_value={'videoShowing': False})
        with self.assertRaisesRegex(ValueError, '눈 아이콘'):
            client.capture('DroidCamOBS')
        self.assertEqual(client.call.call_count, 1)

    def test_empty_exception_is_visible_with_stage_and_source_list(self):
        with tempfile.TemporaryDirectory() as data:
            helper = Mock()
            app = Assistant(data, helper=helper, vision=Mock())
            app.obs = lambda: CameraOBS(['DroidCamOBS'], AssertionError())
            result = app.camera_check()
            self.assertFalse(result['camera_ready'])
            self.assertIn('사진 가져오기', result['error'])
            self.assertIn('AssertionError', result['error'])
            self.assertEqual(result['sources'], ['DroidCamOBS'])
            self.assertIsNone(app.latest)
            self.assertEqual(app.error, result['error'])
            helper.activate.assert_not_called()
            app.close()

    def test_source_listing_survives_missing_source_and_capture_failure(self):
        with tempfile.TemporaryDirectory() as data:
            helper = Mock()
            app = Assistant(data, helper=helper, vision=Mock())
            app.latest = b'old-preview'
            app.running = True
            app.pending = {'old': True}
            app.obs = lambda: CameraOBS(['DroidCam ', '브라우저'])
            result = app.camera_check()
            self.assertFalse(result['camera_ready'])
            self.assertEqual(result['sources'], ['DroidCam ', '브라우저'])
            self.assertIsNone(app.latest)
            self.assertIsNone(app.pending)
            self.assertFalse(app.running)
            app.configure({'host': '127.0.0.1', 'source': 'DroidCam '})
            self.assertEqual(app.config['source'], 'DroidCam ')
            app.obs = lambda: CameraOBS(['DroidCam '], OBSRequestError('GetSourceScreenshot', 703, 'Capture failed'))
            result = app.camera_check()
            self.assertEqual(result['sources'], ['DroidCam '])
            self.assertFalse(result['camera_ready'])
            self.assertIn('703', app.error)
            app.obs = lambda: CameraOBS(['DroidCam '])
            result = app.camera_check()
            self.assertTrue(result['camera_ready'])
            self.assertTrue(app.latest)
            self.assertEqual(app.error, '')
            helper.activate.assert_not_called()
            app.close()


if __name__ == '__main__':
    unittest.main()
