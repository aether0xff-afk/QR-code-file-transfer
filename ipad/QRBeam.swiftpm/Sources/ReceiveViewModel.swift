import CryptoKit
import Foundation
import UniformTypeIdentifiers
import SwiftUI

final class IncomingTransferState {
    let transferID: Data
    var manifest: TransferManifest?
    var chunks: [UInt32: Data] = [:]
    var finished = false

    init(transferID: Data) {
        self.transferID = transferID
    }

    var key: String {
        transferID.map { String(format: "%02x", $0) }.joined()
    }

    var receivedCount: Int { chunks.count }

    var progress: Double {
        guard let manifest else { return 0 }
        if manifest.total == 0 { return 1 }
        return min(1, Double(chunks.count) / Double(manifest.total))
    }

    func accept(_ packet: QRPacket) throws {
        guard packet.transferID == transferID else { throw QRProtocolError.invalidTransferID }
        switch packet.type {
        case .manifest:
            let decoded = try TransferManifest.decode(packet.payload)
            guard decoded.total == Int(packet.total) else { throw QRProtocolError.invalidManifest }
            manifest = decoded
        case .data:
            if chunks[packet.index] == nil {
                chunks[packet.index] = packet.payload
            }
        }
    }

    func isComplete() -> Bool {
        guard let manifest else { return false }
        if manifest.total == 0 { return true }
        guard chunks.count == manifest.total else { return false }
        return (0..<manifest.total).allSatisfy { chunks[UInt32($0)] != nil }
    }

    func assemble() throws -> Data {
        guard isComplete(), let manifest else { throw QRProtocolError.incompleteTransfer }
        var output = Data()
        output.reserveCapacity(manifest.size)
        for index in 0..<manifest.total {
            guard let chunk = chunks[UInt32(index)] else { throw QRProtocolError.incompleteTransfer }
            output.append(chunk)
        }
        if output.count > manifest.size {
            output = Data(output.prefix(manifest.size))
        }
        guard output.count == manifest.size else { throw QRProtocolError.sizeMismatch }
        let digest = SHA256.hash(data: output).map { String(format: "%02x", $0) }.joined()
        guard digest == manifest.sha256.lowercased() else { throw QRProtocolError.hashMismatch }
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
    @Published var completedFile: ReceivedFile?
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
        receivedText = "0 / 0 chunks"
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
                let name = transfer.manifest?.name ?? "received_file.bin"
                transfer.finished = true
                completedFile = ReceivedFile(name: safeFilename(name), data: data)
                status = "파일 복원이 완료되었습니다. ‘파일 앱에 저장’을 누르세요."
                progress = 1
            }
        } catch QRProtocolError.invalidPrefix {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func updateUI(for transfer: IncomingTransferState) {
        let total = transfer.manifest?.total ?? 0
        filename = transfer.manifest?.name ?? "파일 정보 수신 중…"
        progress = transfer.progress
        receivedText = "\(transfer.receivedCount.formatted()) / \(total.formatted()) chunks"
        status = transfer.manifest == nil ? "파일 정보 QR을 기다리는 중…" : "누락된 청크를 채우는 중…"
    }
}
