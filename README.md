# QRBeam 0.1

Windows PC와 iPad 사이에서 인터넷, Wi-Fi, Bluetooth, 서버 없이 파일을 애니메이션 QR 코드로 전송하는 MVP입니다.

두 앱 모두 **보내기와 받기**를 지원하며 같은 프로토콜을 사용합니다.

## 포함된 항목

```text
QRBeam/
├─ windows/                  Python + Tkinter + OpenCV 앱
│  ├─ app.py
│  ├─ protocol.py
│  ├─ receiver_state.py
│  ├─ run.bat
│  ├─ build_exe.bat
│  └─ tests/
├─ ipad/
│  ├─ QRBeam.swiftpm/        iPad Swift Playgrounds 앱 프로젝트
│  └─ xcodegen/              macOS/Xcode 대체 프로젝트 생성 설정
└─ PROTOCOL.md               양쪽 공통 바이너리 프로토콜
```

## Windows 실행

Windows 10/11과 Python 3.11을 권장합니다.

1. `windows` 폴더를 엽니다.
2. `run.bat`을 실행합니다.
3. 최초 실행 시 가상환경과 패키지를 설치한 뒤 앱이 열립니다.

직접 실행할 경우:

```powershell
cd windows
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### Windows EXE 빌드

```text
windows\build_exe.bat
```

완료되면 다음 파일이 생성됩니다.

```text
windows\dist\QRBeam.exe
```

현재 작업 환경은 Linux이므로 이 ZIP 안에 Windows용으로 컴파일된 EXE는 포함하지 않았습니다. 배치 파일이 실제 Windows에서 PyInstaller로 빌드합니다.

## iPad 실행 — Swift Playgrounds

1. iPad에 **Swift Playgrounds**를 설치합니다.
2. ZIP을 파일 앱에서 압축 해제합니다.
3. `ipad/QRBeam.swiftpm`을 탭해 Swift Playgrounds로 엽니다.
4. 프로젝트 설정의 Capabilities에서 Camera가 켜져 있는지 확인합니다.
5. Run을 누릅니다.

프로젝트가 파일 앱에서 일반 폴더로 보인다면 폴더 이름 끝이 정확히 `.swiftpm`인지 확인하세요.

### macOS + Xcode 대체 경로

`ipad/xcodegen` 폴더에서:

```bash
xcodegen generate
open QRBeam.xcodeproj
```

그 뒤 Signing & Capabilities에서 본인의 Apple Team을 선택하고 iPad에서 실행합니다.


## GitHub Actions 빌드

`main` 브랜치에 푸시하거나 Actions의 **Build QRBeam** 워크플로를 수동 실행하면 다음 artifact가 생성됩니다.

- `QRBeam-Windows`: 바로 실행 가능한 `QRBeam.exe`
- `QRBeam-iPad-unsigned`: 서명되지 않은 `QRBeam-unsigned.ipa`

iPad용 IPA는 Apple 개발자 인증서로 서명되지 않았으므로 AltStore, SideStore 등의 일반 설치 흐름에서는 사용자의 인증서로 다시 서명해야 합니다. Swift Playgrounds 프로젝트는 `ipad/QRBeam.swiftpm`에 그대로 포함되어 있습니다.

## 사용법

### 보내기

1. 보내기 탭에서 파일 선택
2. 처음에는 5 FPS, 700-byte chunk 유지
3. 전송 시작
4. 상대 기기에서 받기 탭의 카메라로 QR을 정면에서 촬영

### 받기

수신기는 중복 프레임을 무시하고 빠진 청크만 채웁니다. 모든 청크를 받은 뒤 SHA-256이 맞으면 파일을 복원합니다.

- Windows: 지정한 저장 폴더에 자동 저장
- iPad: `파일 앱에 저장` 버튼으로 내보내기

## 첫 테스트 권장값

- TXT 또는 PNG 파일
- 파일 크기 10KB~1MB
- 5 FPS
- 700-byte chunk
- 두 화면 밝기 높게
- QR이 화면에 크게 보이도록 정면 배치

안정성이 확인되면 FPS를 8~12로 높이거나 chunk를 800~1000으로 올릴 수 있습니다. 카메라, 화면 해상도, 거리, 잔상에 따라 최적값이 달라집니다.

## 현재 구현

- 임의 파일 송신/수신
- Windows ↔ iPad 양방향 호환
- 16-byte 전송 ID
- CRC32 프레임 무결성 검사
- SHA-256 전체 파일 검증
- Manifest 주기적 재전송
- QR 전체 반복 재생
- 중복 청크 제거
- UTF-8 및 한글 파일명 지원
- 최대 파일 크기: iPad 앱에서 10MB 제한

## 아직 없는 기능

- AES-GCM 암호화
- 수신기가 누락 번호 QR을 표시하고 송신기가 그 청크만 재전송하는 역방향 제어
- Fountain/LT/Raptor 오류 복구 코드
- 여러 QR을 한 화면에 동시에 표시하는 고속 모드
- 백그라운드 전송

## 테스트 결과

현재 소스에서 확인한 항목:

- Python 프로토콜 단위 테스트 3개 통과
- 임의 바이너리 파일 분할 → 재조립 → SHA-256 검증 통과
- Python에서 생성한 QR을 OpenCV로 다시 읽어 원본 패킷 복원 통과
- Python 고정 테스트 벡터를 Swift 프로토콜 코드로 디코딩하고 다시 동일 문자열로 인코딩 통과
- 모든 Swift 소스 구문 파싱 통과

실제 iPad 카메라와 Windows 카메라를 마주 보게 한 통합 테스트는 이 실행 환경에서는 수행할 수 없습니다.
