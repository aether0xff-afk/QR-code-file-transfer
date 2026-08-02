import CryptoKit
import Foundation
import UniformTypeIdentifiers
import SwiftUI

final class IncomingTransferState {
    let transferID: Data
    var core: CoreManifest?
    var metadata: TransferMetadata?
    var chunks: [UInt32: Data] = [:]
    var parity: [UInt32: Data] = [:]
    var finished = false
    let firstSeen = Date()
    var lastUniqueAt = Date()
    var uniqueEvents: [(Date, Int)] = []

    init(transferID: Data) {
        self.transferID = transferID
    }

    var key: String {
        transferID.map { String(format: "%02x", $0) }.joined()
    }

    var displayName: String {
        metadata?.name ?? genericFilename(for: transferID)
    }

    var receivedCount: Int { chunks.count }

    var progress: Double {
        guard let core else { return 0 }
        if core.total == 0 { return 1 }
        return min(1, Double(chunks.count) / Double(core.total))
    }

    var elapsed: TimeInterval {
        Date().timeIntervalSince(firstSeen)
    }

    var chunkRate: Double {
        let now = Date()
        let cutoff = now.addingTimeInterval(-8)
        uniqueEvents.removeAll { $0.0 < cutoff }
        guard !uniqueEvents.isEmpty else { return 0 }
        let start = max(cutoff, uniqueEvents.first?.0 ?? firstSeen)
        let span = max(0.5, now.timeIntervalSince(start))
        return Double(uniqueEvents.reduce(0) { $0 + $1.1 }) / span
    }

    var byteRate: Double {
        guard let core else { return 0 }
        return chunkRate * Double(core.chunkSize)
    }

    var eta: TimeInterval? {
        guard let core else { return nil }
        let remaining = max(0, core.total - chunks.count)
        if remaining == 0 { return 0 }
        let rate = chunkRate
        if rate < 0.15 || Date().timeIntervalSince(lastUniqueAt) > 3 {
            return nil
        }
        return Double(remaining) / rate
    }

    @discardableResult
    func accept(_ packet: QRPacket) throws -> Int {
        guard packet.transferID == transferID else { throw QRProtocolError.invalidTransferID }
        var added = 0

        switch packet.type {
        case .core:
            let decoded = try CoreManifest.decode(packet.payload)
            guard decoded.total == Int(packet.total) else { throw QRProtocolError.invalidCore }
            core = decoded
            added += recoverAvailable()
        case .metadata:
            metadata = try TransferMetadata.decode(packet.payload)
        case .data:
            if chunks[packet.index] == nil {
                chunks[packet.index] = packet.payload
                added += 1
            }
            added += recoverAvailable()
        case .parity:
            if parity[packet.index] == nil {
                parity[packet.index] = packet.payload
            }
            added += recoverAvailable()
        }

        if added > 0 {
            let now = Date()
            lastUniqueAt = now
            uniqueEvents.append((now, added))
        }
        return added
    }

    private func recoverAvailable() -> Int {
        guard let core, core.total > 0 else { return 0 }
        var recovered = 0

        for (groupStartRaw, parityPayload) in parity {
            let groupStart = Int(groupStartRaw)
            let groupEnd = min(core.total, groupStart + core.parityGroup)
            let missing = (groupStart..<groupEnd).filter { chunks[UInt32($0)] == nil }
            guard missing.count == 1, parityPayload.count == core.chunkSize else { continue }

            var restored = [UInt8](parityPayload)
            var canRecover = true
            for index in groupStart..<groupEnd where index != missing[0] {
                guard let chunk = chunks[UInt32(index)] else {
                    canRecover = false
                    break
                }
                for (offset, value) in chunk.enumerated() where offset < restored.count {
                    restored[offset] ^= value
                }
            }
            if canRecover {
                chunks[UInt32(missing[0])] = Data(restored)
                recovered += 1
            }
        }
        return recovered
    }

    func isComplete() -> Bool {
        guard let core else { return false }
        if core.total == 0 { return true }
        guard chunks.count == core.total else { return false }
        return (0..<core.total).allSatisfy { chunks[UInt32($0)] != nil }
    }

    func assemble() throws -> Data {
        guard isComplete(), let core else { throw QRProtocolError.incompleteTransfer }
        var output = Data()
        output.reserveCapacity(core.size)
        for index in 0..<core.total {
            guard let chunk = chunks[UInt32(index)] else { throw QRProtocolError.incompleteTransfer }
            output.append(chunk)
        }
        if output.count > core.size {
            output = Data(output.prefix(core.size))
        }
        guard output.count == core.size else { throw QRProtocolError.sizeMismatch }
        let digest = SHA256.hash(data: output).map { String(format: "%02x", $0) }.joined()
        guard digest == core.sha256.lowercased() else { throw QRProtocolError.hashMismatch }
        return output
    }
}

struct ReceivedFile: Identifiable {
    let id = UUID()
    let name: String
    let data: Data
}

struct ReceivedFileDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.data] }
    var data: Data

    init(data: Data = Data()) {
        self.data = data
    }

    init(configuration: ReadConfiguration) throws {
        data = configuration.file.regularFileContents ?? Data()
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: data)
    }
}

@MainActor
final class ReceiveViewModel: ObservableObject {
    @Published var status = "카메라를 시작하고 송신 화면의 QR을 비추세요."
    @Published var filename = "수신 대기 중"
    @Published var progress: Double = 0
    @Published var receivedText = "0 / 0 chunks"
    @Published var timingText = "경과 00:00 · ETA 계산 전"
    @Published var throughputText = "0.0 chunks/s · 0.0 KB/s"
    @Published var metadataText = "파일 정보: 아직 받지 않음"
    @Published var completedFile: ReceivedFile?
    @Published var exportFilename = "received_file.bin"
    @Published var errorMessage: String?

    let scanner = CameraScanner()
    private var transfers: [String: IncomingTransferState] = [:]
    private var activeKey: String?

    init() {
        scanner.onCode = { [weak self] code in
            self?.process(code)
        }
    }

    func start() { scanner.start() }
    func stop() { scanner.stop() }

    func reset() {
        transfers.removeAll()
        activeKey = nil
        completedFile = nil
        progress = 0
        filename = "수신 대기 중"
        exportFilename = "received_file.bin"
        receivedText = "0 / 0 chunks"
        timingText = "경과 00:00 · ETA 계산 전"
        throughputText = "0.0 chunks/s · 0.0 KB/s"
        metadataText = "파일 정보: 아직 받지 않음"
        status = "수신 기록을 초기화했습니다."
    }

    private func process(_ text: String) {
        do {
            let packet = try QRWireCodec.decode(text)
            let key = packet.transferID.map { String(format: "%02x", $0) }.joined()
            let transfer = transfers[key] ?? IncomingTransferState(transferID: packet.transferID)
            transfers[key] = transfer
            activeKey = key
            try transfer.accept(packet)
            updateUI(for: transfer)

            if transfer.isComplete() && !transfer.finished {
                let data = try transfer.assemble()
                transfer.finished = true
                let name = safeFilename(transfer.displayName)
                completedFile = ReceivedFile(name: name, data: data)
                exportFilename = name
                status = "파일 복원과 SHA-256 검증이 완료되었습니다."
                progress = 1
            } else if transfer.finished, let file = completedFile {
                let updatedName = safeFilename(transfer.displayName)
                if file.name != updatedName {
                    completedFile = ReceivedFile(name: updatedName, data: file.data)
                    exportFilename = updatedName
                }
            }
        } catch QRProtocolError.invalidPrefix {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func updateUI(for transfer: IncomingTransferState) {
        let total = transfer.core?.total ?? 0
        filename = transfer.displayName
        exportFilename = transfer.metadata == nil ? exportFilename : transfer.displayName
        progress = transfer.progress
        receivedText = "\(transfer.receivedCount.formatted()) / \(total.formatted()) chunks"
        timingText = "경과 \(formatDuration(transfer.elapsed)) · ETA \(formatDuration(transfer.eta))"
        throughputText = String(
            format: "%.1f chunks/s · %.1f KB/s",
            transfer.chunkRate,
            transfer.byteRate / 1024
        )
        metadataText = transfer.metadata == nil
            ? "파일 정보: 숨겨짐 · 저장 이름을 직접 수정할 수 있습니다."
            : "파일 정보: 별도 QR로 수신됨"
        status = transfer.core == nil
            ? "필수 복원 정보 QR을 기다리는 중…"
            : "누락 청크를 반복 QR과 XOR 복구 프레임으로 채우는 중…"
    }
}
