# QRBeam Offline 0.3

QRBeam Offline는 인터넷이나 외부 공유기 없이 Windows 모바일 핫스팟과 iPad를 직접 연결해 파일을 전송하는 모드다.

## 구성

- Windows: `QRBeam-Offline.exe`
  - 모바일 핫스팟 연결 정보를 QR로 표시
  - 로컬 HTTP 수신 서버 실행
  - 파일을 `.part` 임시 파일로 스트리밍 저장
  - 최종 SHA-256이 송신 측 값과 일치할 때만 파일 확정
- iPad: QRBeam의 `오프라인` 탭
  - 연결 QR 스캔
  - Hotspot Configuration API로 Windows 핫스팟 연결 요청
  - 파일 선택 후 로컬 HTTP 업로드
  - 진행률, MB/s, 경과 시간, ETA 표시

## 사용법

1. Windows에서 `QRBeam-Offline.exe`를 실행한다.
2. `Windows 핫스팟 설정 열기`를 누르고 모바일 핫스팟을 켠다.
3. Windows 설정에 표시된 실제 핫스팟 이름과 비밀번호를 QRBeam Offline에 입력한다.
4. Windows 주소는 일반적으로 `192.168.137.1`이다. 연결되지 않으면 앱에 표시된 다른 사설 IPv4 주소를 시험한다.
5. 저장 폴더를 선택하고 `오프라인 수신 시작`을 누른다.
6. Windows 방화벽 팝업이 나타나면 개인 네트워크에서 허용한다.
7. iPad QRBeam에서 `오프라인` 탭을 열고 `연결 QR 스캔`을 누른다.
8. Wi-Fi 연결 승인을 수락한다.
9. 파일을 선택하고 `Windows로 보내기`를 누른다.

자동 핫스팟 연결이 서명 또는 entitlement 문제로 실패해도 연결 정보는 앱에 남는다. 이 경우 iPad 설정에서 Windows 핫스팟에 수동으로 연결한 뒤 `수신 서버 확인`을 누르면 전송할 수 있다.

## 보안 모델

파일 본문에는 TLS 또는 별도 AES 암호화를 적용하지 않는다.

접근 제한:

- Windows 모바일 핫스팟의 WPA 비밀번호
- 수신 서버 실행 때마다 새로 생성되는 일회용 전송 토큰
- 로컬 네트워크에서만 접근 가능한 수신 서버

무결성:

- iPad가 전송 전에 SHA-256 계산
- Windows가 수신과 동시에 SHA-256 재계산
- 해시가 다르면 `.part` 파일 삭제 및 HTTP 422 반환
- 해시가 같을 때만 최종 파일 이름으로 변경

## 현재 한계

- 0.3은 iPad에서 Windows로 보내는 방향을 우선 지원한다.
- Windows 모바일 핫스팟 자체는 앱이 자동으로 켜지 않고 Windows 설정에서 사용자가 켠다.
- 중단 지점부터 이어받기는 아직 없다.
- 실제 속도는 Windows Wi-Fi 어댑터, 2.4/5GHz 대역, iPad 모델, 저장 장치, 방화벽 검사에 따라 달라진다.
- unsigned IPA는 설치 전에 사용자의 Apple 인증서로 다시 서명해야 한다.
- 일부 사이드로딩 서명 방식은 Hotspot Configuration entitlement를 제거할 수 있다. 그 경우 수동 Wi-Fi 연결 폴백을 사용한다.

## 검증

GitHub Actions에서 다음 항목을 통과했다.

- Windows 오프라인 서버 정상 업로드 테스트
- 잘못된 SHA-256 파일 거부 테스트
- 일회용 토큰 인증
- 기존 QR 프로토콜 테스트와 광학 왕복 테스트
- `QRBeam.exe` 빌드
- `QRBeam-Offline.exe` 빌드
- iPad Swift 컴파일
- unsigned IPA 패키징
