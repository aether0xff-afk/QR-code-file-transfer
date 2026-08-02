import Combine
import CryptoKit
import Foundation
import UIKit

@MainActor
final class SendViewModel: ObservableObject {
    @Published var filename = "선택된 파일 없음"
    @Published var detail = "파일 앱에서 보낼 파일을 선택하세요."
    @Published var qrImage: UIImage?
    @Published var isPlaying = false
    @Published var fps: Double = 5
    @Published var chunkSize = qrDefaultChunkSize
    @Published var frameLabel = "대기 중"
    @Published var progress: Double = 0
    @Published var errorMessage: String?

    private var frames: [String] = []
    private var frameCounter = 0
    private var timer: Timer?
    private let generator = QRImageGenerator()

    deinit {
        timer?.invalidate()
    }

    func load(url: URL) {
        stop()
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }

        do {
            let data = try Data(contentsOf: url, options: [.mappedIfSafe])
            guard data.count <= 10 * 1024 * 1024 else {
                throw NSError(domain: "QRBeam", code: 1, userInfo: [NSLocalizedDescriptionKey: "첫 버전은 10MB 이하 파일만 지원합니다."])
            }
            try prepare(data: data, filename: url.lastPathComponent)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func prepare(data: Data, filename: String) throws {
        let selectedChunkSize = min(1000, max(256, chunkSize))
        chunkSize = selectedChunkSize
        let total = data.isEmpty ? 0 : (data.count + selectedChunkSize - 1) / selectedChunkSize
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let manifest = TransferManifest(
            name: safeFilename(filename),
            size: data.count,
            chunkSize: selectedChunkSize,
            total: total,
            sha256: digest
        )
        let transferID = randomTransferID()
        let manifestPacket = try QRPacket(
            type: .manifest,
            transferID: transferID,
            index: 0,
            total: UInt32(total),
            payload: try manifest.encoded()
        )
        let manifestFrame = try QRWireCodec.encode(manifestPacket)

        var newFrames: [String] = []
        newFrames.reserveCapacity(max(1, total + (total + qrManifestInterval - 1) / qrManifestInterval))
        if total == 0 {
            newFrames.append(manifestFrame)
        } else {
            for index in 0..<total {
                if index % qrManifestInterval == 0 { newFrames.append(manifestFrame) }
                let start = index * selectedChunkSize
                let end = min(data.count, start + selectedChunkSize)
                let packet = try QRPacket(
                    type: .data,
                    transferID: transferID,
                    index: UInt32(index),
                    total: UInt32(total),
                    payload: data.subdata(in: start..<end)
                )
                newFrames.append(try QRWireCodec.encode(packet))
            }
        }

        frames = newFrames
        frameCounter = 0
        self.filename = manifest.name
        detail = "\(data.count.formatted()) bytes · \(total.formatted()) chunks · ID \(transferID.hexPrefix)"
        showCurrentFrame()
    }

    func toggle() {
        isPlaying ? stop() : start()
    }

    func start() {
        guard !frames.isEmpty else { return }
        stopTimerOnly()
        isPlaying = true
        UIApplication.shared.isIdleTimerDisabled = true
        let interval = 1.0 / max(1, min(12, fps))
        let timer = Timer(timeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.frameCounter += 1
                self.showCurrentFrame()
            }
        }
        self.timer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    func stop() {
        stopTimerOnly()
        isPlaying = false
        UIApplication.shared.isIdleTimerDisabled = false
    }

    private func stopTimerOnly() {
        timer?.invalidate()
        timer = nil
    }

    private func showCurrentFrame() {
        guard !frames.isEmpty else { return }
        let position = frameCounter % frames.count
        qrImage = generator.image(for: frames[position])
        progress = Double(position + 1) / Double(frames.count)
        frameLabel = "프레임 \(position + 1) / \(frames.count) · 반복 재생"
    }
}

private extension Data {
    var hexPrefix: String {
        prefix(6).map { String(format: "%02x", $0) }.joined() + "…"
    }
}
