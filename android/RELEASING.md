# FOX Producer Android 배포

## 확인한 기존 상태 (2026-10-01)

- 화면 제목: `FOX Producer Android 1.1`, Android 패키지 정보: `versionCode 10` / `versionName 1.0`.
- 2026-09-30 Android Actions run #9 (`36651861867`)는 성공했지만 `assembleDebug` 후 APK artifact만 업로드했다.
- 당시 GitHub Releases는 비어 있었고, 고정 release signing 설정도 없었다.
- 이 변경의 범위는 `android/`와 Android Actions, 서명 파일 유출 방지용 `.gitignore`다. FOX Center와 웹/PWA 코드는 수정하지 않는다.

## 변경된 구조

| 항목 | 기준 |
| --- | --- |
| 앱 ID / namespace | `com.fox.producer` 유지 |
| 단일 버전 원본 | `android/version.properties` — 첫 release `versionCode=11`, `versionName=1.1` |
| 화면 버전 | Gradle이 `fox-version.js`를 생성하며 같은 값으로 앱 제목과 화면을 표시 |
| 일반 CI | `Check FOX Producer Android`: PR/main 변경 검사, debug 빌드, 서명 누락 차단 테스트, 일회용 테스트 키로 release 빌드 검증 |
| 실제 배포 | `Release FOX Producer Android`: main에서 수동 실행, 고정 Actions secrets로 `assembleRelease` |
| 게시물 | `fox-producer-android-v1.1` 태그의 GitHub Release |
| 설치 파일 | `FOX-Producer-1.1-11.apk` (일반화: 버전명-버전코드) |
| 함께 게시 | `release-metadata.json`, `SHA256SUMS` |

CI의 `FOX-Producer-TEST-ONLY-debug` artifact는 개발 확인용이다. 운영 휴대폰에 설치할 파일은 **GitHub Releases의 APK**다. CI의 일회용 키와 서명 테스트 APK는 업로드하지 않으며 실제 배포 키로 쓰지 않는다.

## 기존 앱과 데이터 보존 — 첫 배포 전에 확인

Android에서 기존 설치 위에 업데이트하려면 앱 ID와 서명 인증서가 같아야 한다. 이 구조는 APK마다 같은 키를 쓰고 versionCode를 올리지만 **새 키로 과거 debug 앱의 서명을 바꿀 수는 없다.**

1. 기존 설치 APK의 원래 keystore가 있으면 먼저 그 키/인증서를 확인한다. 파일 이름이나 비밀번호가 같아도 인증서가 다르면 다른 키다.
2. 과거 GitHub Actions의 임시 debug keystore를 보관하지 않았다면 APK에 든 공개 인증서만으로 개인 키를 복원할 수 없다. 새 키로 만든 release는 그 debug 설치에 덮어쓸 수 없다.
3. **기존 앱 삭제, 앱 데이터 초기화, 강제 재설치는 하지 않는다.** 기존 키를 찾거나 검증된 데이터 이전 방법을 마련해야 한다. 키가 없는 기존 debug 설치의 무손실 이전은 이 배포 변경만으로 해결되지 않는다.
4. 앱의 `백업 저장` 버튼만으로 안전한 백업이 됐다고 가정하지 않는다. 현재 WebView는 Blob 다운로드 처리와 JSON 파일 선택을 보장하지 않으며 파일 선택기가 `image/*`로 고정돼 있다. 기존 백업 포맷도 모든 localStorage 키를 포함하지 않는다. 실제 파일의 저장·내용·복원 검증 없이 제거하면 안 된다.

이 변경은 `file:///android_asset/index.html` 주소, WebView 데이터 위치, `foxProducts` / `foxPick` / `foxBudget` / `foxMemo:*` 등 저장 키와 데이터 코드를 유지한다. 앱 데이터를 지우는 작업은 추가하지 않는다. 같은 서명으로 정상 업데이트할 때 기존 데이터를 유지하는 구조이며, 실제 휴대폰 업데이트 검증은 별도 필요하다.

기존 APK의 인증서는 Android SDK 도구로 확인할 수 있다:

```text
apksigner verify --verbose --print-certs existing-FOX-Producer.apk
```

설치했던 원본 APK가 없다면 개발자용 USB 연결에서 `adb shell pm path com.fox.producer`로 실제 경로를 확인한 뒤, 반환된 `base.apk` 경로를 `adb pull`로 복사하여 검사한다. 이 과정은 앱을 삭제하지 않는다. 추출된 인증서 지문은 공개 정보이며 keystore 개인 키와는 다르다.

## 필요한 GitHub Actions secrets (최초 1회)

등록 위치: [저장소 Settings → Secrets and variables → Actions](https://github.com/jopdrules-prog/fox/settings/secrets/actions) → **New repository secret**.

| 이름 | 값 |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64` | 앞으로 계속 사용할 keystore 파일 전체를 Base64로 인코딩한 값 |
| `ANDROID_KEYSTORE_PASSWORD` | keystore 비밀번호 |
| `ANDROID_KEY_ALIAS` | 해당 키의 alias (새로 만드는 예: `fox-producer`) |
| `ANDROID_KEY_PASSWORD` | 해당 키의 비밀번호 |
| `ANDROID_SIGNING_CERT_SHA256` | 그 alias의 인증서 SHA-256 지문 (콜론 유무 무관) |

`GITHUB_TOKEN`은 Actions가 자동 제공한다. 별도 PAT나 token secret은 필요 없다. 공개 인증서 지문도 여기서는 설정의 일관성을 위해 secret으로 등록한다. 인증서 지문은 APK와 Release metadata에서 확인 가능하며 개인 키가 아니다.

keystore/비밀번호/Base64는 저장소, 이 문서, 채팅, artifact에 넣지 않는다. `.gitignore`는 실수 방지 장치일 뿐이며 GitHub Secrets 외에 keystore 원본과 비밀번호를 본인 보관 장소에 안전하게 백업한다. 향후 업데이트마다 키를 다시 만들면 안 된다.

### 키가 전혀 없을 때: Windows에서 새 배포 키 준비

**기존 debug 설치와 호환된다는 뜻은 아니다.** 앞의 기존 데이터 보존 조건을 먼저 확인한다. 이미 올바른 키가 있다면 새로 생성하지 말고 그 키를 등록한다.

JDK 17 이상이 설치된 PowerShell에서, 저장소 밖의 경로에 한 번만 생성한다:

```powershell
$foxSigningDir = Join-Path $env:USERPROFILE 'FOX-signing'
New-Item -ItemType Directory -Force -Path $foxSigningDir | Out-Null
$foxKeystore = Join-Path $foxSigningDir 'fox-producer-release.jks'
keytool -genkeypair -v -storetype JKS -keystore $foxKeystore -alias fox-producer -keyalg RSA -keysize 3072 -validity 10000
```

비밀번호와 인증서 정보를 물으면 직접 입력한다. 입력한 keystore 비밀번호/키 비밀번호와 alias를 위 secret 이름으로 등록한다. 키 비밀번호 입력에서 Enter로 keystore 비밀번호를 재사용했다면 두 password secret에 같은 값을 등록한다.

Base64를 클립보드로 복사한 뒤 `ANDROID_KEYSTORE_BASE64` 값 칸에 붙여 넣는다:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes($foxKeystore)) | Set-Clipboard
```

다음 명령의 **SHA256 인증서 지문**을 `ANDROID_SIGNING_CERT_SHA256`에 등록한다. keystore 파일의 SHA-256 해시가 아니다:

```powershell
keytool -list -v -keystore $foxKeystore -alias fox-producer
Set-Clipboard -Value ''
```

## 마지막 배포 실행

1. 위 5개 secrets를 등록한다. 기존 키를 계속 쓰는 경우 설치 APK와 인증서 지문을 먼저 대조한다.
2. [Actions → Release FOX Producer Android](https://github.com/jopdrules-prog/fox/actions/workflows/release-android.yml)에서 **Run workflow → main → Run workflow**를 누른다.
3. 성공하면 [Releases](https://github.com/jopdrules-prog/fox/releases)에 APK가 게시된다. 예: `fox-producer-android-v1.1` → `FOX-Producer-1.1-11.apk`.
4. 기존 서명과 일치하는 휴대폰에서 앱을 지우지 않고 APK를 열어 업데이트한다. 상품·사진·사입 상태·메모·예산이 남았는지 확인한다. 서명 오류가 나면 삭제하지 말고 기존 키부터 확인한다.

Secrets가 없는 경우 작업은 **누락된 이름을 표시하고 실패**한다. 임시 키 생성이나 unsigned/debug APK 게시로 넘어가지 않는다.

이 workflow는 설치 파일을 게시한다. 휴대폰에 자동 설치하거나 사용자 승인 없이 업데이트하는 기능은 추가하지 않는다.

## 다음 버전

1. `android/version.properties`의 두 값만 변경한다. 예: `versionCode=12`, `versionName=1.2` (패치 버전 `1.1.1`도 가능).
2. 변경을 main에 반영하고 `Check FOX Producer Android` 성공을 확인한다.
3. `Release FOX Producer Android`를 main에서 다시 실행한다. 같은 secrets/동일 키를 계속 사용한다.

검사 대상은 이전의 **모든** `fox-producer-android-v*` 공개 Release(prerelease 포함)다. 더 높은 versionCode와 versionName, 동일 서명 지문을 요구한다. 태그/Release가 이미 있으면 덮어쓰지 않는다. 이전 Release의 metadata가 없거나 잘못됐거나 GitHub API 오류가 나도 게시를 멈춘다.

파일을 draft Release에 모두 올린 뒤 공개하므로 중간 업로드 실패물이 정상 배포처럼 노출되지 않는다. 실패한 draft가 남으면 무조건 재실행하지 말고 태그의 커밋, APK 서명·metadata·SHA256SUMS와 3개 첨부 파일을 확인한다. 이미 공개된 버전은 변경하지 말고 버전을 올린다. 미완성 draft의 정리 또는 검증 후 공개는 관리자가 결정한다.

Release 삭제는 버전/서명 이력 검사를 약화시키므로 배포 이력을 보존한다. 최초 공개 때에는 비교할 이전 release metadata가 없어서 등록된 인증서 지문을 기준으로 검증한다. 설치 기기와의 호환성을 자동으로 추정하지 않는다.

## 개발 검증

```bash
python3 -m unittest discover -s android/scripts/tests -v
python3 android/scripts/release.py version
cd android
gradle --no-daemon --no-configuration-cache :app:assembleDebug
```

JDK 17, Gradle 8.7, Android SDK platform 35 / build-tools 35.0.0을 사용한다. 로컬 release 빌드는 환경변수 `ANDROID_KEYSTORE_PATH`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`가 필요하다. 출력 검증은 `ANDROID_SIGNING_CERT_SHA256`를 설정하고 `scripts/release.py verify-apk`를 사용한다.

공식 기준: [Android 앱 서명](https://developer.android.com/studio/publish/app-signing), [Android 버전 관리](https://developer.android.com/studio/publish/versioning), [GitHub Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).
