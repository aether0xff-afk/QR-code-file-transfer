import Foundation

let qrWirePrefix = "QRF2:"
let qrMagic = Data("QRF2".utf8)
let qrProtocolVersion: UInt8 = 2
let qrDefaultChunkSize = 1400
let qrCoreInterval = 96
let qrParityGroupSize = 8

private let qrHeaderSize = 4 + 1 + 1 + 16 + 4 + 4 + 2
private let qrCRCSize = 4

enum QRPacketType: UInt8 {
    case core = 1
    case data = 2
    case metadata = 3
    case parity = 4
}

struct QRPacket: Equatable {
    let type: QRPacketType
    let transferID: Data
    let index: UInt32
    let total: UInt32
    let payload: Data

    init(type: QRPacketType, transferID: Data, index: UInt32, total: UInt32, payload: Data) throws {
        guard transferID.count == 16 else { throw QRProtocolError.invalidTransferID }
        guard payload.count <= Int(UInt16.max) else { throw QRProtocolError.payloadTooLarge }
        self.type = type
        self.transferID = transferID
        self.index = index
        self.total = total
        self.payload = payload
    }
}

enum QRProtocolError: LocalizedError {
    case invalidPrefix
    case invalidBase64
    case frameTooShort
    case crcMismatch
    case wrongMagic
    case unsupportedVersion(UInt8)
    case unknownPacketType(UInt8)
    case invalidTransferID
    case payloadTooLarge
    case payloadLengthMismatch
    case indexOutOfRange
    case invalidCore
    case invalidMetadata
    case incompleteTransfer
    case sizeMismatch
    case hashMismatch

    var errorDescription: String? {
        switch self {
        case .invalidPrefix: return "QRBeam 프레임이 아닙니다."
        case .invalidBase64: return "Base64URL 데이터가 잘못되었습니다."
        case .frameTooShort: return "QR 프레임이 너무 짧습니다."
        case .crcMismatch: return "CRC32 검증에 실패했습니다."
        case .wrongMagic: return "프로토콜 식별자가 다릅니다."
        case .unsupportedVersion(let version): return "지원하지 않는 프로토콜 버전입니다: \(version)"
        case .unknownPacketType(let type): return "알 수 없는 패킷 종류입니다: \(type)"
        case .invalidTransferID: return "전송 ID가 잘못되었습니다."
        case .payloadTooLarge: return "QR 페이로드가 너무 큽니다."
        case .payloadLengthMismatch: return "페이로드 길이가 맞지 않습니다."
        case .indexOutOfRange: return "청크 번호가 범위를 벗어났습니다."
        case .invalidCore: return "필수 복원 정보가 잘못되었습니다."
        case .invalidMetadata: return "파일 정보 프레임이 잘못되었습니다."
        case .incompleteTransfer: return "아직 모든 청크를 받지 못했습니다."
        case .sizeMismatch: return "복원된 파일 크기가 다릅니다."
        case .hashMismatch: return "SHA-256 검증에 실패했습니다."
        }
    }
}

struct CoreManifest: Codable, Equatable {
    let size: Int
    let chunkSize: Int
    let total: Int
    let sha256: String
    let parityGroup: Int

    enum CodingKeys: String, CodingKey {
        case size
        case chunkSize = "chunk"
        case total
        case sha256
        case parityGroup = "parity"
    }

    func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        return try encoder.encode(self)
    }

    static func decode(_ data: Data) throws -> CoreManifest {
        let manifest: CoreManifest
        do {
            manifest = try JSONDecoder().decode(CoreManifest.self, from: data)
        } catch {
            throw QRProtocolError.invalidCore
        }
        let expected = manifest.size == 0 ? 0 : (manifest.size + manifest.chunkSize - 1) / manifest.chunkSize
        guard manifest.size >= 0,
              (128...1800).contains(manifest.chunkSize),
              manifest.total == expected,
              (2...32).contains(manifest.parityGroup),
              manifest.sha256.count == 64,
              manifest.sha256.allSatisfy({ $0.isHexDigit }) else {
            throw QRProtocolError.invalidCore
        }
        return manifest
    }
}

struct TransferMetadata: Codable, Equatable {
    let name: String
    let mime: String?
    let modifiedUTC: String?

    enum CodingKeys: String, CodingKey {
        case name
        case mime
        case modifiedUTC = "modified"
    }

    func encoded() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        return try encoder.encode(self)
    }

    static func decode(_ data: Data) throws -> TransferMetadata {
        do {
            let value = try JSONDecoder().decode(TransferMetadata.self, from: data)
            return TransferMetadata(
                name: safeFilename(value.name),
                mime: value.mime,
                modifiedUTC: value.modifiedUTC
            )
        } catch {
            throw QRProtocolError.invalidMetadata
        }
    }
}

enum QRWireCodec {
    static func encode(_ packet: QRPacket) throws -> String {
        var body = Data()
        body.append(qrMagic)
        body.append(qrProtocolVersion)
        body.append(packet.type.rawValue)
        body.append(packet.transferID)
        body.appendUInt32BE(packet.index)
        body.appendUInt32BE(packet.total)
        body.appendUInt16BE(UInt16(packet.payload.count))
        body.append(packet.payload)
        body.appendUInt32BE(CRC32.checksum(body))
        return qrWirePrefix + base64URLEncode(body)
    }

    static func decode(_ text: String) throws -> QRPacket {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasPrefix(qrWirePrefix) else { throw QRProtocolError.invalidPrefix }
        let encoded = String(trimmed.dropFirst(qrWirePrefix.count))
        guard let raw = base64URLDecode(encoded) else { throw QRProtocolError.invalidBase64 }
        guard raw.count >= qrHeaderSize + qrCRCSize else { throw QRProtocolError.frameTooShort }

        let body = raw.prefix(raw.count - qrCRCSize)
        let expectedCRC = try raw.readUInt32BE(at: raw.count - qrCRCSize)
        guard CRC32.checksum(Data(body)) == expectedCRC else { throw QRProtocolError.crcMismatch }

        guard Data(body.prefix(4)) == qrMagic else { throw QRProtocolError.wrongMagic }
        let version = body[body.startIndex + 4]
        guard version == qrProtocolVersion else { throw QRProtocolError.unsupportedVersion(version) }
        let typeRaw = body[body.startIndex + 5]
        guard let type = QRPacketType(rawValue: typeRaw) else { throw QRProtocolError.unknownPacketType(typeRaw) }

        let transferID = Data(body[(body.startIndex + 6)..<(body.startIndex + 22)])
        let bodyData = Data(body)
        let index = try bodyData.readUInt32BE(at: 22)
        let total = try bodyData.readUInt32BE(at: 26)
        let payloadLength = Int(try bodyData.readUInt16BE(at: 30))
        let payloadStart = qrHeaderSize
        let payloadEnd = payloadStart + payloadLength
        guard payloadEnd == body.count else { throw QRProtocolError.payloadLengthMismatch }
        if type == .data && index >= total { throw QRProtocolError.indexOutOfRange }
        if type == .parity && total != 0 && index >= total { throw QRProtocolError.indexOutOfRange }
        let payload = Data(body[body.index(body.startIndex, offsetBy: payloadStart)..<body.endIndex])
        return try QRPacket(type: type, transferID: transferID, index: index, total: total, payload: payload)
    }

    private static func base64URLEncode(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private static func base64URLDecode(_ value: String) -> Data? {
        var standard = value
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        let remainder = standard.count % 4
        if remainder != 0 {
            standard += String(repeating: "=", count: 4 - remainder)
        }
        return Data(base64Encoded: standard)
    }
}

enum CRC32 {
    static func checksum(_ data: Data) -> UInt32 {
        var crc: UInt32 = 0xFFFF_FFFF
        for byte in data {
            crc ^= UInt32(byte)
            for _ in 0..<8 {
                let mask = UInt32(bitPattern: -Int32(crc & 1))
                crc = (crc >> 1) ^ (0xEDB8_8320 & mask)
            }
        }
        return crc ^ 0xFFFF_FFFF
    }
}

extension Data {
    mutating func appendUInt16BE(_ value: UInt16) {
        append(UInt8((value >> 8) & 0xFF))
        append(UInt8(value & 0xFF))
    }

    mutating func appendUInt32BE(_ value: UInt32) {
        append(UInt8((value >> 24) & 0xFF))
        append(UInt8((value >> 16) & 0xFF))
        append(UInt8((value >> 8) & 0xFF))
        append(UInt8(value & 0xFF))
    }

    func readUInt16BE(at offset: Int) throws -> UInt16 {
        guard offset >= 0, offset + 2 <= count else { throw QRProtocolError.frameTooShort }
        let byte0 = UInt16(self[index(startIndex, offsetBy: offset)])
        let byte1 = UInt16(self[index(startIndex, offsetBy: offset + 1)])
        return (byte0 << 8) | byte1
    }

    func readUInt32BE(at offset: Int) throws -> UInt32 {
        guard offset >= 0, offset + 4 <= count else { throw QRProtocolError.frameTooShort }
        let byte0 = UInt32(self[index(startIndex, offsetBy: offset)])
        let byte1 = UInt32(self[index(startIndex, offsetBy: offset + 1)])
        let byte2 = UInt32(self[index(startIndex, offsetBy: offset + 2)])
        let byte3 = UInt32(self[index(startIndex, offsetBy: offset + 3)])
        let high = (byte0 << 24) | (byte1 << 16)
        let low = (byte2 << 8) | byte3
        return high | low
    }
}

func xorParity(chunks: [Data], chunkSize: Int) -> Data {
    var parity = [UInt8](repeating: 0, count: chunkSize)
    for chunk in chunks {
        for (index, value) in chunk.enumerated() where index < chunkSize {
            parity[index] ^= value
        }
    }
    return Data(parity)
}

func safeFilename(_ value: String) -> String {
    let last = URL(fileURLWithPath: value).lastPathComponent
    let invalid = CharacterSet(charactersIn: "<>:\"/\\|?*").union(.controlCharacters)
    let scalars = last.unicodeScalars.map { invalid.contains($0) ? "_" : String($0) }.joined()
    let result = scalars.trimmingCharacters(in: CharacterSet(charactersIn: " ."))
    return result.isEmpty ? "received_file.bin" : result
}

func genericFilename(for transferID: Data) -> String {
    "received_\(transferID.prefix(6).map { String(format: \"%02x\", $0) }.joined()).bin"
}

func randomTransferID() -> Data {
    Data((0..<16).map { _ in UInt8.random(in: 0...255) })
}

func formatDuration(_ seconds: TimeInterval?) -> String {
    guard let seconds, seconds >= 0, seconds.isFinite else { return "계산 중…" }
    var value = Int(seconds.rounded())
    let hours = value / 3600
    value %= 3600
    let minutes = value / 60
    let secs = value % 60
    if hours > 0 {
        return String(format: "%02d:%02d:%02d", hours, minutes, secs)
    }
    return String(format: "%02d:%02d", minutes, secs)
}
