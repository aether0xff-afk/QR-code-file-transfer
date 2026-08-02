# QRBeam Wire Protocol v1

QRBeam은 파일 자체를 네트워크 주소로 연결하지 않고, 파일 바이트를 여러 QR 프레임으로 나눠 광학적으로 전송합니다.

## 전송 파이프라인

```text
file bytes
  -> fixed-size chunks
  -> binary packet + CRC32
  -> Base64URL ASCII wrapper
  -> QR Code (error correction M)
  -> camera scan
  -> packet reassembly
  -> SHA-256 verification
  -> restored file
```

Base64URL 래퍼는 Windows OpenCV와 iPadOS `AVCaptureMetadataOutput`이 같은 QR 내용을 안정적으로 문자열로 읽게 하기 위해 사용합니다. 원본 파일은 여전히 바이트 단위로 분할되며, Base64URL 때문에 프레임 크기가 약 4/3배가 됩니다.

## QR 문자열

모든 프레임은 다음 접두사로 시작합니다.

```text
QRF1:<unpadded-base64url-packet>
```

## Binary packet

모든 정수는 big-endian입니다.

| Offset | Size | Field |
|---:|---:|---|
| 0 | 4 | ASCII magic `QRF1` |
| 4 | 1 | protocol version (`1`) |
| 5 | 1 | packet type: manifest=`1`, data=`2` |
| 6 | 16 | random transfer ID |
| 22 | 4 | chunk index |
| 26 | 4 | total chunk count |
| 30 | 2 | payload length |
| 32 | N | payload |
| 32+N | 4 | CRC32 of every preceding packet byte |

## Manifest payload

Manifest payload는 UTF-8 JSON입니다.

```json
{
  "name": "example.bin",
  "size": 12345,
  "chunk": 700,
  "total": 18,
  "sha256": "64 lowercase hex characters"
}
```

Manifest는 매 24개 데이터 청크마다 다시 표시됩니다. 수신기는 같은 청크를 여러 번 받아도 첫 번째 정상 패킷만 보관합니다.

## Completion rules

수신기는 다음 조건을 모두 만족해야 파일을 완료로 처리합니다.

1. Manifest를 받음
2. `0 ... total-1` 모든 청크를 받음
3. 합친 데이터 길이가 manifest의 `size`와 같음
4. SHA-256이 manifest의 `sha256`과 같음

## Fixed test vector

다음 프레임은 아래 패킷을 나타냅니다.

- transfer ID: `000102030405060708090a0b0c0d0e0f`
- type: data
- index: 2
- total: 3
- payload hex: `68656c6c6f00776f726c64`

```text
QRF1:UVJGMQECAAECAwQFBgcICQoLDA0ODwAAAAIAAAADAAtoZWxsbwB3b3JsZId87qg
```
