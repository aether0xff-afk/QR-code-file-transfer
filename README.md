# QRBeam 0.2

Windows PC와 iPad 사이에서 인터넷, Wi-Fi, Bluetooth, 서버 없이 파일 바이트를 애니메이션 QR로 전송합니다.

0.2의 목표는 **속도를 높이되 파일 무결성을 절대 포기하지 않는 것**입니다. 각 QR은 CRC32로 검사하며, 모든 청크를 조립한 뒤 SHA-256이 원본과 일치할 때만 완료 처리합니다.

## 핵심 변경

- 기본 청크 `700 → 1,400 bytes`
- 60Hz 화면 기반 속도 프로필
  - 안정: 15 QR/s, 같은 QR을 4프레임 유지
  - 고속: 30 QR/s, 같은 QR을 2프레임 유지
  - 터보: 60 QR/s, 매 프레임 QR 교체
- 데이터 8개마다 XOR 복구 QR 1개 추가
- 송신·수신 경과 시간, 실제 속도, ETA 표시
- 파일명·확장자·MIME·수정 시각은 기본 전송에서 제거
- `파일 정보 QR 보내기` 버튼을 눌렀을 때만 메타데이터를 3초간 별도 송출
- Windows 수신 기능은 전면 카메라 한계 때문에 실험 기능으로 표시
- iPad 카메라는 현재 포맷이 지원할 때만 60FPS를 요청하며, 미지원 기기에서는 기존 포맷을 유지

## 권장 사용 흐름

### Windows → iPad

1. Windows 앱에서 파일 선택
2. 기본값인 `안정 · 15 QR/s`, `1,400 bytes` 유지
3. 전송 시작
4. iPad 후면 카메라로 QR을 정면에서 촬영
5. 파일명이 필요할 때만 Windows에서 `파일 정보 QR 보내기 · 3초` 선택
6. iPad에서 SHA-256 검증 성공 후 파일 앱에 저장

### 속도 프로필

| 프로필 | 화면 유지 | QR 변경률 | 이론상 순수 데이터량* | 용도 |
|---|---:|---:|---:|---|
| 안정 | 4 frames | 15 QR/s | 약 18KB/s | 기본값, 안정성 우선 |
| 고속 | 2 frames | 30 QR/s | 약 36KB/s | 고정된 기기와 좋은 조명 |
| 터보 | 1 frame | 60 QR/s | 약 72KB/s | 실험용 |

\* XOR 복구 QR, 필수 복원 정보 QR, Base64URL 오버헤드를 반영한 대략적인 값입니다. 실제 속도는 화면 주사율, 카메라 노출, 디코더 처리량에 따라 달라집니다.

터보에서 누락이 많아지면 완료 시간은 오히려 길어질 수 있습니다. 기본값은 안정 모드이며, 고속과 터보는 사용자가 명시적으로 선택해야 합니다.

## 누락 복구

전송 순서는 대략 다음과 같습니다.

```text
필수 복원 정보
데이터 0 ... 데이터 7
XOR 복구 0
데이터 8 ... 데이터 15
XOR 복구 8
...
```

한 그룹에서 데이터 QR 하나가 누락되면 나머지 7개와 XOR 복구 QR로 즉시 복원합니다. 두 개 이상 누락된 그룹은 다음 반복 주기에서 빠진 데이터가 들어올 때까지 기다립니다.

복구된 데이터도 최종 SHA-256 검증 대상입니다. 잘못 복구되거나 손상된 파일은 저장되지 않습니다.

## 개인정보 보호

기본 데이터 흐름에는 다음 정보가 들어가지 않습니다.

- 원래 파일명
- 확장자
- MIME 형식
- 수정 시각

수신기는 기본적으로 다음과 같은 임시 이름을 사용합니다.

```text
received_a1b2c3d4e5f6.bin
```

송신자가 `파일 정보 QR 보내기 · 3초` 버튼을 누르면 파일 정보가 별도 QR로 전송됩니다. 수신자는 파일 정보를 받지 않아도 저장 이름을 직접 입력할 수 있습니다.

파일 복원에 필수인 크기, 청크 크기, 전체 청크 수, SHA-256, 복구 그룹 크기는 개인정보 QR과 분리된 필수 복원 정보에 포함됩니다.

## 시간과 ETA

### 송신기

- 경과 시간
- 현재 프레임 / 한 바퀴 전체 프레임
- 실제 QR/s
- 예상 KB/s
- 한 바퀴 송출 완료 ETA

단방향 전송이므로 송신기 ETA는 수신 완료 시간이 아니라 현재 QR 전체를 한 번 송출하는 데 남은 시간입니다.

### 수신기

- 실제로 받은 고유 청크 수
- 최근 8초 이동평균 chunks/s와 KB/s
- 경과 시간
- 완료 ETA

3초 이상 새 청크가 들어오지 않으면 잘못된 숫자를 표시하지 않고 ETA를 `계산 중…`으로 바꿉니다.

## Windows 실행

Windows 10/11과 Python 3.11을 권장합니다.

```powershell
cd windows
run.bat
```

EXE 빌드:

```powershell
cd windows
build_exe.bat
```

결과:

```text
windows/dist/QRBeam.exe
```

Windows의 내장 전면 카메라는 화면 반사와 자동 노출 때문에 수신에 적합하지 않을 수 있습니다. Windows 수신 탭은 실험 기능이며 USB 웹캠 또는 캡처 장치를 권장합니다.

## iPad 실행

### Swift Playgrounds

1. `ipad/QRBeam.swiftpm`을 iPad 파일 앱에서 엽니다.
2. Swift Playgrounds에서 실행합니다.
3. 카메라 권한을 허용합니다.

### Xcode

```bash
cd ipad/xcodegen
xcodegen generate
open QRBeam.xcodeproj
```

Signing & Capabilities에서 본인의 Apple Team을 선택해 실행합니다.

## GitHub Actions

`main`에 푸시하거나 PR을 만들면 다음을 자동 검증합니다.

- Python 프로토콜 단위 테스트
- 실제 QR 이미지 생성 → OpenCV 디코딩 광학 왕복 테스트
- Windows PyInstaller EXE 빌드
- iPad unsigned IPA 빌드

생성 artifact:

- `QRBeam-Windows`
- `QRBeam-iPad-unsigned`

unsigned IPA는 설치 전에 사용자의 Apple 인증서로 다시 서명해야 합니다.

## 현재 검증 항목

- 패킷 인코딩·디코딩 왕복
- 필수 복원 정보와 비공개 메타데이터 분리
- 파일 분할·재조립·SHA-256 검증
- 8개 그룹에서 데이터 청크 하나를 제거한 뒤 XOR로 복원
- 완성 데이터 변조 시 SHA-256 검증 실패
- QR 이미지 생성과 OpenCV 재인식
- Windows EXE 빌드
- iPad Swift 컴파일 및 unsigned IPA 패키징

## 다음 후보

- Zstandard 자동 압축
- Base64URL을 제거한 원본 바이너리 QR
- 다중 QR 동시 표시
- Reed–Solomon 또는 RaptorQ
- 수신기가 누락 번호 QR을 띄우고 송신기가 해당 청크만 재전송하는 양방향 복구
- AES-GCM 암호화
