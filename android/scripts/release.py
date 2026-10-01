#!/usr/bin/env python3
"""FOX Producer release checks. Standard library only; never prints secret values."""
import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ANDROID = Path(__file__).resolve().parents[1]
APPLICATION_ID = "com.fox.producer"
TAG_PREFIX = "fox-producer-android-v"
VERSION_PATTERN = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))?"
SIGNING_SECRETS = (
    "ANDROID_KEYSTORE_BASE64", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_ALIAS",
    "ANDROID_KEY_PASSWORD", "ANDROID_SIGNING_CERT_SHA256",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version_tuple(name):
    require(isinstance(name, str) and re.fullmatch(VERSION_PATTERN, name),
            "versionName must be numeric, e.g. 1.1 or 1.1.1")
    parts = tuple(map(int, name.split(".")))
    return parts + (0,) * (3 - len(parts))


def read_version(path=ANDROID / "version.properties"):
    props = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        require(separator and key not in props, "Invalid/duplicate version property")
        props[key] = value
    require(set(props) == {"versionCode", "versionName"}, "Expected only versionCode and versionName")
    code = props["versionCode"]
    require(re.fullmatch(r"[1-9][0-9]{0,9}", code) and int(code) <= 2100000000,
            "versionCode must be 1..2100000000")
    version_tuple(props["versionName"])
    return {"versionCode": int(code), "versionName": props["versionName"]}


def fingerprint(value):
    digest = value.replace(":", "").strip().lower()
    require(re.fullmatch(r"[0-9a-f]{64}", digest), "Expected a SHA-256 signing certificate fingerprint")
    return digest


def prepare_signing(path):
    missing = [name for name in SIGNING_SECRETS if not os.environ.get(name)]
    require(not missing, "Missing GitHub Actions secrets: " + ", ".join(missing))
    fingerprint(os.environ["ANDROID_SIGNING_CERT_SHA256"])
    try:
        data = base64.b64decode("".join(os.environ["ANDROID_KEYSTORE_BASE64"].split()), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("ANDROID_KEYSTORE_BASE64 is not valid Base64") from None
    require(bool(data), "Decoded keystore is empty")
    # Refuse to replace an existing file/symlink. Runner temp storage only.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        output.write(data)


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    require(result.returncode == 0, "Command failed: " + command[0] + " (check tool access/configuration)")
    return result.stdout


def check_history(current, certificate, history):
    # The pre-release Android APK already shipped as versionCode 10 / 1.0.
    require(current["versionCode"] > 10, "Increase versionCode above the existing Android baseline (10)")
    require(version_tuple(current["versionName"]) > (1, 0, 0), "Increase versionName above the existing Android baseline (1.0)")
    for previous in history:
        require(previous.get("applicationId") == APPLICATION_ID, "Previous Android release has the wrong applicationId")
        code = previous.get("versionCode")
        require(type(code) is int and 0 < code <= 2100000000, "Previous release has invalid versionCode")
        require(current["versionCode"] > code, "Increase versionCode above every published Android release")
        require(version_tuple(current["versionName"]) > version_tuple(previous.get("versionName")),
                "Increase versionName above every published Android release")
        require(fingerprint(previous.get("signingCertificateSha256", "")) == certificate,
                "Signing key changed from a published release. Restore the original key; do not reinstall the app.")


def check_remote(version):
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "GITHUB_REPOSITORY is required")
    certificate = fingerprint(os.environ.get("ANDROID_SIGNING_CERT_SHA256", ""))
    tag = TAG_PREFIX + version["versionName"]
    # checkout fetch-depth: 0 fetches all tags; API lists all release pages.
    tag_check = subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/tags/" + tag])
    require(tag_check.returncode == 1, "Release tag already exists (or Git check failed). Do not overwrite published tags.")
    pages = json.loads(run(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/releases"]))
    releases = [release for page in pages for release in page]
    require(not any(r["tag_name"] == tag for r in releases),
            "Release already exists, including drafts. Inspect it before retrying; assets will not be overwritten.")
    history = []
    for release in releases:
        if release["draft"] or not release["tag_name"].startswith(TAG_PREFIX):
            continue
        matches = [a for a in release["assets"] if a["name"] == "release-metadata.json"]
        require(len(matches) == 1, "A previous FOX Android release is missing release-metadata.json; verify its identity before publishing")
        metadata = json.loads(run(["gh", "api", f"repos/{repo}/releases/assets/{matches[0]['id']}",
                                   "-H", "Accept: application/octet-stream"]))
        require(release["tag_name"] == TAG_PREFIX + metadata.get("versionName", ""),
                "Previous release tag and metadata disagree")
        history.append(metadata)
    check_history(version, certificate, history)
    print("Release history verified; no version or signing-key conflict.")


def verify_apk(apk, output, version):
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    require(sdk, "ANDROID_HOME or ANDROID_SDK_ROOT is required")
    build_tools = Path(sdk) / "build-tools" / "35.0.0"
    certificate = fingerprint(os.environ.get("ANDROID_SIGNING_CERT_SHA256", ""))
    verification = run([str(build_tools / "apksigner"), "verify", "--verbose", "--print-certs", str(apk)])
    digests = re.findall(r"Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]+)", verification)
    require(len(digests) == 1 and fingerprint(digests[0]) == certificate,
            "APK signature does not match ANDROID_SIGNING_CERT_SHA256")
    badging = run([str(build_tools / "aapt"), "dump", "badging", str(apk)])
    package = re.search(r"^package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'", badging, re.M)
    require(package is not None and package.groups() ==
            (APPLICATION_ID, str(version["versionCode"]), version["versionName"]),
            "APK applicationId/versionCode/versionName does not match the release configuration")
    require("application-debuggable" not in badging, "Refusing to publish a debuggable APK")
    with zipfile.ZipFile(apk) as archive:
        asset = archive.read("assets/fox-version.js").decode("utf-8")
        match = re.fullmatch(r"window.FOX_ANDROID_VERSION = (\{.*\});\n", asset)
        require(match is not None and json.loads(match[1]) == version, "APK screen version differs from Android version")
        require('src="fox-version.js"' in archive.read("assets/index.html").decode("utf-8"),
                "App screen does not load the generated version")
    commit = os.environ.get("GITHUB_SHA") or run(["git", "rev-parse", "HEAD"]).strip()
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "Expected a full commit SHA")
    output.mkdir(parents=True, exist_ok=True)
    filename = f"FOX-Producer-{version['versionName']}-{version['versionCode']}.apk"
    destination = output / filename
    shutil.copyfile(apk, destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    metadata = {
        "applicationId": APPLICATION_ID, **version,
        "signingCertificateSha256": certificate, "commitSha": commit,
        "apk": filename, "apkSha256": digest,
    }
    metadata_file = output / "release-metadata.json"
    metadata_file.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (output / "SHA256SUMS").write_text(
        f"{digest}  {filename}\n{hashlib.sha256(metadata_file.read_bytes()).hexdigest()}  release-metadata.json\n",
        encoding="utf-8")
    (output / "RELEASE_NOTES.md").write_text(
        f"FOX Producer Android {version['versionName']} (versionCode {version['versionCode']})\n\n"
        f"설치 파일: **{filename}** — APK를 다운로드해 열어 주세요.\n\n"
        "기존 앱과 서명 키가 같은 경우 앱을 삭제하지 않고 업데이트할 수 있으며 앱 데이터는 유지됩니다.\n"
        "과거 Actions debug APK와는 서명이 다를 수 있습니다. 설치가 거부되면 기존 앱을 삭제하거나 데이터를 초기화하지 마세요.\n"
        "기존 키/인증서 확인과 실제 백업·복원 검증을 먼저 진행해야 합니다.\n\n"
        f"패키지: `{APPLICATION_ID}`\n\n소스 커밋: `{commit}`\n\n"
        "서명 인증서와 파일 해시는 release-metadata.json 및 SHA256SUMS에서 확인할 수 있습니다.\n",
        encoding="utf-8")
    print("Verified signed release APK, package identity, versions, and non-debuggable manifest.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    signing = commands.add_parser("prepare-signing")
    signing.add_argument("path", type=Path)
    commands.add_parser("check-remote")
    verify = commands.add_parser("verify-apk")
    verify.add_argument("apk", type=Path)
    verify.add_argument("output", type=Path)
    args = parser.parse_args()
    version = read_version()
    if args.command == "version":
        print(f"version_name={version['versionName']}\nversion_code={version['versionCode']}\ntag={TAG_PREFIX}{version['versionName']}")
    elif args.command == "prepare-signing":
        prepare_signing(args.path)
    elif args.command == "check-remote":
        check_remote(version)
    elif args.command == "verify-apk":
        verify_apk(args.apk, args.output, version)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        print(f"Release check failed: {error}", file=sys.stderr)
        sys.exit(1)
