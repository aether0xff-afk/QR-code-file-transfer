# QRBeam Offline 0.4

QRBeam Offline는 인터넷이나 외부 공유기 없이 Windows와 iPad 사이에 전용 Wi-Fi를 만들고 파일을 직접 전송한다.

## 0.4의 핵심 변경

Windows 설정의 **모바일 핫스팟**은 공유할 인터넷 연결이 없으면 켜지지 않는 경우가 있다. 0.4는 이 설정 화면을 사용하지 않고, Windows의 `WiFiDirectAdvertisementPublisher` 레거시 모드로 일반 Wi-Fi 클라이언트가 접속할 수 있는 로컬 SoftAP를 앱에서 직접 시작한다.

기본 흐름:

```text
QRBeam-Offline.exe 실행
→ 전용 Wi-Fi + 수신 시작
→ QRBeam-XXXX 네트워크 자동 생성
→ 로컬 HTTP 수신 서버 시작
→ 연결 QR 표시
→ iPad가 네트워크에 연결하고 파일 업로드
```

인터넷 연결, 외부 공유기, 휴대전화 핫스팟 부트스트랩이 필요하지 않다.

## 구성

### Windows: `QRBeam-Offline.exe`

- 임의의 SSID와 14자 비밀번호 자동 생성
- Wi-Fi Direct 자율 그룹 소유자 시작
- 레거시 모드로 iPad가 일반 Wi-Fi처럼 접속 가능한 AP 생성
- 로컬 HTTP 수신 서버 실행
- 연결 정보와 일회용 전송 토큰을 QR로 표시
- 파일을 `.part` 임시 파일로 스트리밍 저장
- 최종 SHA-256이 송신 측 값과 일치할 때만 파일 확정
- 앱 종료 또는 중지 시 SoftAP도 자동 종료

### iPad: QRBeam의 `오프라인` 탭

- 연결 QR 스캔
- Hotspot Configuration API로 Windows의 전용 Wi-Fi 연결 요청
- 파일 선택 후 로컬 HTTP 업로드
- 진행률, MB/s, 경과 시간, ETA 표시

## 사용법

1. Windows에서 `QRBeam-Offline.exe`를 실행한다.
2. Windows의 Wi-Fi가 켜져 있는지 확인한다.
3. Windows 모바일 핫스팟이 켜져 있다면 끈다. 모바일 핫스팟과 Wi-Fi Direct는 동시에 실행할 수 없다.
4. 기본값인 `인터넷 없는 전용 Wi-Fi 자동 생성`을 유지한다.
5. `전용 Wi-Fi + 수신 시작`을 누른다.
6. Windows 방화벽 팝업이 나타나면 개인 네트워크에서 허용한다.
7. iPad QRBeam에서 `오프라인` 탭을 열고 `연결 QR 스캔`을 누른다.
8. Wi-Fi 연결 요청을 승인한다.
9. 파일을 선택하고 `Windows로 보내기`를 누른다.

SSID와 비밀번호는 매 실행 때 자동으로 생성할 수 있으며 `새 이름·비밀번호 생성` 버튼으로 다시 만들 수 있다.

## 자동 AP가 실패하는 경우

모든 Windows Wi-Fi 어댑터와 드라이버가 Wi-Fi Direct 레거시 AP를 지원하는 것은 아니다. 실패 원인은 앱에 표시된다.

- `Wi-Fi가 꺼짐`: Windows Wi-Fi를 켠 뒤 다시 시작
- `리소스 사용 중`: Windows 모바일 핫스팟과 다른 Wi-Fi Direct 기능을 끈 뒤 재시도
- `지원하지 않는 어댑터/드라이버`: `인터넷 없는 전용 Wi-Fi 자동 생성`을 끄고 수동 핫스팟 또는 기존 로컬 Wi-Fi 폴백 사용

자동 모드가 실패하더라도 기존 수동 방식은 제거하지 않는다.

## 보안 모델

파일 본문에는 TLS 또는 별도 AES 암호화를 적용하지 않는다.

접근 제한:

- 자동 생성되는 WPA 비밀번호
- 수신 서버 실행 때마다 새로 생성되는 일회용 전송 토큰
- 로컬 네트워크에서만 접근 가능한 수신 서버

무결성:

- iPad가 전송 전에 SHA-256 계산
- Windows가 수신과 동시에 SHA-256 재계산
- 해시가 다르면 `.part` 파일 삭제 및 HTTP 422 반환
- 해시가 같을 때만 최종 파일 이름으로 변경

## 현재 한계

- 0.4는 iPad에서 Windows로 보내는 방향을 우선 지원한다.
- 중단 지점부터 이어받기는 아직 없다.
- 실제 속도는 Windows Wi-Fi 어댑터, 사용 대역, iPad 모델, 저장 장치, 방화벽 검사에 따라 달라진다.
- unsigned IPA는 설치 전에 사용자의 Apple 인증서로 다시 서명해야 한다.
- 일부 사이드로딩 서명 방식은 Hotspot Configuration entitlement를 제거할 수 있다. 그 경우 iPad 설정에서 QR에 표시된 SSID에 수동 연결한 뒤 `수신 서버 확인`을 누른다.

## 빌드 구조

`QRBeam-Offline.exe`에는 .NET 8 기반 `QRBeam-SoftAP.exe` helper가 PyInstaller 번들로 포함된다. 실행 시 임시 번들 디렉터리에서 helper를 시작하고, helper 프로세스가 살아 있는 동안 Wi-Fi Direct 레거시 AP가 유지된다.

## 검증

GitHub Actions에서 다음 항목을 검증한다.

- .NET SoftAP helper 컴파일
- helper 인수 및 SSID/비밀번호 검증 실행
- Windows 오프라인 서버 정상 업로드 테스트
- 잘못된 SHA-256 파일 거부 테스트
- 일회용 토큰 인증
- 기존 QR 프로토콜 테스트와 광학 왕복 테스트
- `QRBeam.exe` 빌드
- SoftAP helper가 포함된 `QRBeam-Offline.exe` 빌드
- iPad Swift 컴파일
- unsigned IPA 패키징

CI 환경에는 실제 Wi-Fi 무선 장치가 없으므로, **실제 AP 생성과 iPad 접속은 사용자 Windows 장치에서 최종 실측해야 한다.**
