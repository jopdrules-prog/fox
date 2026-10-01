import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("release", Path(__file__).resolve().parents[1] / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = {"versionCode": 12, "versionName": "1.2"}
        self.previous = {
            "versionCode": 11, "versionName": "1.1", "applicationId": release.APPLICATION_ID,
            "signingCertificateSha256": "a" * 64,
        }

    def test_version_rejects_invalid_or_injectable_values(self):
        path = self.root / "version.properties"
        for text in ["versionCode=0\nversionName=1.1", "versionCode=2100000001\nversionName=1.1",
                     "versionCode=11\nversionName=1.1\ntag=anything", "versionCode=11\nversionName=1.1;echo danger",
                     "versionCode=11\nversionName=01.1", "versionCode=11\nversionCode=12\nversionName=1.1"]:
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    release.read_version(path)

    def test_version_reads_single_source(self):
        path = self.root / "version.properties"
        path.write_text("# version\nversionCode=12\nversionName=1.2\n")
        self.assertEqual(release.read_version(path), self.current)

    def test_newer_version_same_key_is_allowed(self):
        release.check_history(self.current, "a" * 64, [self.previous])
        release.check_history(self.current, "a" * 64, [])

    def test_first_release_cannot_downgrade_existing_version_10(self):
        with self.assertRaisesRegex(ValueError, "baseline"):
            release.check_history({"versionCode": 10, "versionName": "1.1"}, "a" * 64, [])

    def test_reused_or_lower_code_is_rejected(self):
        for code in [10, 11]:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "versionCode"):
                release.check_history({**self.current, "versionCode": code}, "a" * 64, [self.previous])

    def test_reused_or_lower_name_is_rejected_even_with_higher_code(self):
        for name in ["1.0", "1.1", "1.1.0"]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "versionName"):
                release.check_history({**self.current, "versionName": name}, "a" * 64, [self.previous])

    def test_history_does_not_depend_on_release_list_order(self):
        newer = {**self.previous, "versionCode": 20, "versionName": "2.0"}
        for history in [[newer, self.previous], [self.previous, newer]]:
            with self.assertRaisesRegex(ValueError, "versionCode"):
                release.check_history(self.current, "a" * 64, history)

    def test_changed_key_or_application_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Signing key changed"):
            release.check_history(self.current, "b" * 64, [self.previous])
        with self.assertRaisesRegex(ValueError, "applicationId"):
            release.check_history(self.current, "a" * 64, [{**self.previous, "applicationId": "wrong.app"}])

    def test_missing_secrets_fail_before_creating_a_keystore(self):
        path = self.root / "missing.jks"
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "ANDROID_KEYSTORE_BASE64"):
            release.prepare_signing(path)
        self.assertFalse(path.exists())

    def test_invalid_base64_fails_without_printing_secret(self):
        environment = {key: "sensitive-placeholder" for key in release.SIGNING_SECRETS}
        environment["ANDROID_SIGNING_CERT_SHA256"] = "a" * 64
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "not valid Base64") as failure:
                release.prepare_signing(self.root / "invalid.jks")
            self.assertNotIn("sensitive-placeholder", str(failure.exception))

    def test_keystore_is_private_and_cannot_be_overwritten(self):
        environment = {key: "test-value" for key in release.SIGNING_SECRETS}
        environment["ANDROID_KEYSTORE_BASE64"] = base64.b64encode(b"test bytes, not a real key").decode()
        environment["ANDROID_SIGNING_CERT_SHA256"] = "AA:" * 31 + "AA"
        path = self.root / "test.jks"
        with patch.dict(os.environ, environment, clear=True):
            release.prepare_signing(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_bytes(), b"test bytes, not a real key")
            with self.assertRaises(FileExistsError):
                release.prepare_signing(path)

    def check_remote(self, pages, metadata=None, tag_returncode=1):
        env = {"GITHUB_REPOSITORY": "test/fox", "ANDROID_SIGNING_CERT_SHA256": "a" * 64}
        responses = [json.dumps(pages)]
        if metadata is not None:
            responses.append(json.dumps(metadata))
        with patch.dict(os.environ, env), patch.object(release, "run", side_effect=responses), \
                patch.object(release.subprocess, "run", return_value=SimpleNamespace(returncode=tag_returncode)):
            release.check_remote(self.current)

    def test_remote_first_release_and_unrelated_release_are_allowed(self):
        self.check_remote([[]])
        self.check_remote([[{"tag_name": "unrelated-v1", "draft": False}]])

    def test_existing_tag_or_draft_cannot_be_overwritten(self):
        with self.assertRaisesRegex(ValueError, "tag already exists"):
            self.check_remote([[]], tag_returncode=0)
        with self.assertRaisesRegex(ValueError, "Release already exists"):
            self.check_remote([[{"tag_name": release.TAG_PREFIX + "1.2", "draft": True}]])

    def test_previous_release_without_metadata_blocks_publish(self):
        with self.assertRaisesRegex(ValueError, "missing release-metadata"):
            self.check_remote([[{"tag_name": release.TAG_PREFIX + "1.1", "draft": False, "assets": []}]])

    def test_previous_release_metadata_must_match_tag(self):
        previous = {"tag_name": release.TAG_PREFIX + "1.0", "draft": False,
                    "assets": [{"name": "release-metadata.json", "id": 123}]}
        with self.assertRaisesRegex(ValueError, "tag and metadata disagree"):
            self.check_remote([[previous]], self.previous)

    def test_remote_api_failure_blocks_publish(self):
        env = {"GITHUB_REPOSITORY": "test/fox", "ANDROID_SIGNING_CERT_SHA256": "a" * 64}
        with patch.dict(os.environ, env), patch.object(release, "run", side_effect=ValueError("API unavailable")), \
                patch.object(release.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError, "API unavailable"):
                release.check_remote(self.current)


if __name__ == "__main__":
    unittest.main()
