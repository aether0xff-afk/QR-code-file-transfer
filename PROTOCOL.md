# QRBeam Wire Protocol v2

QRBeam v2는 파일 바이트를 여러 QR 프레임으로 나눠 광학적으로 전송하며, 속도 향상과 누락 복구, 메타데이터 프라이버시를 함께 제공한다.

## 전송 파이프라인

```text
file bytes
  -> fixed-size data chunks
  -> XOR recovery chunk per group
  -> binary packet + CRC32
  -> Base64URL ASCII wrapper
  -> QR Code, error correction M
  -> camera scan
  -> duplicate removal and XOR recovery
  -> packet reassembly
  -> SHA-256 verification
  -> restored file
```

Base64URL은 Windows OpenCV와 iPadOS `AVCaptureMetadataOutput`이 동일한 문자열을 읽도록 유지한다. 원본 파일은 바이트 단위로 분할된다.

## QR 문자열

```text
QRF2:<unpadded-base64url-packet>
```

v1의 `QRF1:` 프레임과 의도적으로 구분한다.

## Binary packet

모든 정수는 big-endian이다.

| Offset | Size | Field |
|---:|---:|---|
| 0 | 4 | ASCII magic `QRF2` |
| 4 | 1 | protocol version `2` |
| 5 | 1 | packet type |
| 6 | 16 | random transfer ID |
| 22 | 4 | chunk index or parity group start |
| 26 | 4 | total data chunk count |
| 30 | 2 | payload length |
| 32 | N | payload |
| 32+N | 4 | CRC32 of all preceding packet bytes |

## Packet types

| Value | Name | Purpose |
|---:|---|---|
| 1 | core | 파일 복원에 필수인 최소 정보 |
| 2 | data | 파일 데이터 청크 |
| 3 | metadata | 사용자가 별도 버튼을 눌렀을 때만 전송하는 파일 정보 |
| 4 | parity | 한 그룹에서 누락 하나를 복원하는 XOR 데이터 |

## Core payload

UTF-8 JSON이다.

```json
{
  "size": 12345,
  "chunk": 1400,
  "total": 9,
  "sha256": "64 lowercase hex characters",
  "parity": 8
}
```

`core`에는 파일명이나 확장자가 들어가지 않는다. 데이터 흐름 중 최대 96개 프레임 간격으로 다시 송출한다.

## Private metadata payload

사용자가 `파일 정보 QR 보내기`를 눌렀을 때만 송출하는 UTF-8 JSON이다.

```json
{
  "name": "example.png",
  "mime": "image/png",
  "modified": "2026-08-02T12:34:56Z"
}
```

`mime`과 `modified`는 생략할 수 있다. 메타데이터를 받지 않은 수신자는 transfer ID에서 만든 임시 이름을 사용한다.

## Data packet

- `index`: `0 ... total-1`
- `payload`: 해당 파일 구간의 원본 바이트
- 마지막 청크만 `chunk`보다 짧을 수 있다.

기본 청크 크기는 1,400바이트다.

## XOR parity packet

기본 그룹 크기는 8이다.

```text
data 0 ... data 7 -> parity index 0
data 8 ... data 15 -> parity index 8
```

모든 데이터 청크를 `chunk` 길이까지 0으로 패딩했다고 보고 바이트별 XOR을 계산한다. parity 패킷의 `index`는 그룹 시작 데이터 인덱스다.

수신기가 그룹 내에서 정확히 하나의 데이터 청크만 잃었다면:

```text
missing = parity XOR all received chunks in the group
```

으로 복원한다. 둘 이상 누락된 그룹은 복원하지 않고 다음 반복에서 추가 데이터가 도착하기를 기다린다.

마지막 그룹에서 복구된 마지막 데이터는 조립 뒤 `size`에 맞춰 잘라낸다.

## Sender speed profiles

화면 출력은 60Hz를 기준으로 한다.

| Profile | Frames per QR | QR changes per second |
|---|---:|---:|
| stable | 4 | 15 |
| fast | 2 | 30 |
| turbo | 1 | 60 |

프로토콜은 속도 프로필과 독립적이다. 송신기가 프레임을 더 오래 유지해도 패킷 내용은 변하지 않는다.

## Receiver rules

수신기는 다음을 수행한다.

1. `QRF2:` 접두사 확인
2. Base64URL 디코딩
3. CRC32 확인
4. transfer ID별 상태 분리
5. 중복 데이터 무시
6. 가능한 XOR 그룹 즉시 복구
7. `0 ... total-1` 모든 데이터 확보
8. `size` 길이로 조립
9. SHA-256 비교

SHA-256이 일치하지 않으면 파일을 완료 또는 저장 가능한 상태로 표시해서는 안 된다.

## ETA

송신 ETA는 현재 전체 프레임 주기를 한 번 송출하는 데 남은 시간이다.

수신 ETA는 최근 8초 동안 들어온 고유 또는 XOR 복구 청크 수의 이동평균으로 계산한다. 3초 이상 새 청크가 없으면 ETA를 알 수 없음으로 표시한다.

## Security and privacy notes

- 파일명과 부가 메타데이터는 core에 포함되지 않는다.
- SHA-256은 무결성 검증용이며 암호화가 아니다.
- 화면을 볼 수 있는 제3자는 QR 내용을 촬영할 수 있다.
- 기밀성은 향후 AES-GCM 계층에서 제공해야 한다.
