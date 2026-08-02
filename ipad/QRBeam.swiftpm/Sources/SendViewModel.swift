import Combine
import CryptoKit
import Foundation
import QuartzCore
import UniformTypeIdentifiers
import UIKit

enum SendSpeedProfile: String, CaseIterable, Identifiable {
    case stable
    case fast
    case turbo

    var id: String { rawValue }

    var title: String {
        switch self {
        case .stable: return "안정 · 15 QR/s"
        case .fast: return "고속 · 30 QR/s"
        case .turbo: return "터보 · 60 QR/s"
        }
    }

    var framesPerQR: Int {
        switch self {
        case .stable: return 4
        case .fast: return 2
        case .turbo: return 1
        }
    }

    var qrRate: Double { 60.0 / Double(framesPerQR) }

    var note: String {
        switch self {
        case .stable: return "기본값 · QR 하나를 화면 4프레임 동안 유지"
        case .fast: return "고정된 기기와 밝은 화면에서 권장"
        case .turbo: return "실험 모드 · 누락이 늘면 즉시 낮추세요"
        }
    }
}

final class DisplayLinkDriver {
    private var displayLink: CADisplayLink?
    private var tickCount = 0
    private var framesPerQR = 4
    var onQRFrame: (() -> Void)?

    func start(framesPerQR: Int) {
        stop()
        self.framesPerQR = max(1, framesPerQR)
        tickCount = 0
        let link = CADisplayLink(target: self, selector: #selector(tick))
        link.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: 60, preferred: 60)
        link.add(to: .main, forMode: .common)
        displayLink = link
    }

    func stop() {
        displayLink?.invalidate()
        displayLink = nil
    }

    @objc private func tick() {
        tickCount += 1
        if tickCount % framesPerQR == 0 {
            onQRFrame?()
        }
    }
}

@MainActor
final class SendViewModel: ObservableObject {
    @Published var filename = "선택된 파일 없음"
    @Published var detail = "파일 앱에서 보낼 파일을 선택하세요."
    @Published var qrImage: UIImage?
    @Published var isPlaying = false
    @Published var speedProfile: SendSpeedProfile = .stable
    @Published var chunkSize = qrDefaultChunkSize
    @Published var frameLabel = "대기 중"
    @Published var timingText = "경과 00:00 · ETA 계산 전"
    @Published var throughputText = "0.0 QR/s · 0.0 KB/s"
    @Published var privacyText = "파일명과 부가 정보는 기본적으로 전송하지 않습니다."
    @Published var progress: Double = 0
    @Published var errorMessage: String?

    private var frames: [String] = []
    private var metadataFrame: String?
    private var frameCounter = 0
    private var startedAt: Date?
    private var elapsedBeforeStart: TimeInterval = 0
    private var metadataWorkItem: DispatchWorkItem?
    private let generator = QRImageGenerator()
    private let displayDriver = DisplayLinkDriver()

    init() {
        displayDriver.onQRFrame = { [weak self] in
            Task { @MainActor in
                self?.advanceFrame()
            }
        }
    }

    deinit {
        displayDriver.stop()
        metadataWorkItem?.cancel()
    }

    func load(url: URL) {
        stop(resetElapsed: true)
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }

        do {
            let data = try Data(contentsOf: url, options: [.mappedIfSafe])
            guard data.count <= 25 * 1024 * 1024 else {
                throw NSError(
                    domain: "QRBeam",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "현재 버전은 25MB 이하 파일만 지원합니다."]
                )
            }
            let values = try? url.resourceValues(forKeys: [.contentModificationDateKey, .typeIdentifierKey])
            let typeIdentifier = values?.typeIdentifier
            let mime = typeIdentifier.flatMap { UTType($0)?.preferredMIMEType }
            let modified = values?.contentModificationDate.map { ISO8601DateFormatter().string(from: $0) }
            try prepare(
                data: data,
                filename: url.lastPathComponent,
                mime: mime,
                modifiedUTC: modified
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func prepare(data: Data, filename: String, mime: String? = nil, modifiedUTC: String? = nil) throws {
        stop(resetElapsed: true)
        let selectedChunkSize = min(1400, max(600, chunkSize))
        chunkSize = selectedChunkSize
        let total = data.isEmpty ? 0 : (data.count + selectedChunkSize - 1) / selectedChunkSize
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let core = CoreManifest(
            size: data.count,
            chunkSize: selectedChunkSize,
            total: total,
            sha256: digest,
            parityGroup: qrParityGroupSize
        )
        let metadata = TransferMetadata(
            name: safeFilename(filename),
            mime: mime,
            modifiedUTC: modifiedUTC
        )
        let transferID = randomTransferID()
        let corePacket = try QRPacket(
            type: .core,
            transferID: transferID,
            index: 0,
            total: UInt32(total),
            payload: try core.encoded()
        )
        let coreFrame = try QRWireCodec.encode(corePacket)
        let privatePacket = try QRPacket(
            type: .metadata,
            transferID: transferID,
            index: 0,
            total: UInt32(total),
            payload: try metadata.encoded()
        )
        metadataFrame = try QRWireCodec.encode(privatePacket)

        func chunk(at index: Int) -> Data {
            let start = index * selectedChunkSize
            let end = min(data.count, start + selectedChunkSize)
            return data.subdata(in: start..<end)
        }

        var newFrames: [String] = [coreFrame]
        let parityCount = total == 0 ? 0 : (total + qrParityGroupSize - 1) / qrParityGroupSize
        newFrames.reserveCapacity(max(1, total + parityCount + total / qrCoreInterval + 2))
        var sinceCore = 0

        if total > 0 {
            for groupStart in stride(from: 0, to: total, by: qrParityGroupSize) {
                let groupEnd = min(total, groupStart + qrParityGroupSize)
                var groupChunks: [Data] = []
                for index in groupStart..<groupEnd {
                    if sinceCore >= qrCoreInterval {
                        newFrames.append(coreFrame)
                        sinceCore = 0
                    }
                    let payload = chunk(at: index)
                    groupChunks.append(payload)
                    let packet = try QRPacket(
                        type: .data,
                        transferID: transferID,
                        index: UInt32(index),
                        total: UInt32(total),
                        payload: payload
                    )
                    newFrames.append(try QRWireCodec.encode(packet))
                    sinceCore += 1
                }
                let parityPacket = try QRPacket(
                    type: .parity,
                    transferID: transferID,
                    index: UInt32(groupStart),
                    total: UInt32(total),
                    payload: xorParity(chunks: groupChunks, chunkSize: selectedChunkSize)
                )
                newFrames.append(try QRWireCodec.encode(parityPacket))
                sinceCore += 1
            }
        }

        frames = newFrames
        frameCounter = 0
        generator.clearCache()
        self.filename = metadata.name
        detail = "\(data.count.formatted()) bytes · \(total.formatted()) data chunks · XOR \(qrParityGroupSize)+1"
        privacyText = "파일명·확장자·MIME·수정 시각은 ‘파일 정보 QR 보내기’를 누를 때만 전송됩니다."
        showCurrentFrame()
    }

    func toggle() {
        isPlaying ? stop() : start()
    }

    func start() {
        guard !frames.isEmpty else { return }
        if startedAt == nil {
            startedAt = Date()
        }
        isPlaying = true
        UIApplication.shared.isIdleTimerDisabled = true
        displayDriver.start(framesPerQR: speedProfile.framesPerQR)
        updateStatistics()
    }

    func stop(resetElapsed: Bool = false) {
        displayDriver.stop()
        if let startedAt {
            elapsedBeforeStart += Date().timeIntervalSince(startedAt)
            self.startedAt = nil
        }
        if resetElapsed {
            elapsedBeforeStart = 0
            frameCounter = 0
        }
        isPlaying = false
        UIApplication.shared.isIdleTimerDisabled = false
        updateStatistics()
    }

    func showMetadataTemporarily() {
        guard let metadataFrame else { return }
        let resume = isPlaying
        stop()
        metadataWorkItem?.cancel()
        qrImage = generator.image(for: metadataFrame)
        frameLabel = "파일 정보 QR 표시 중 · 3초"
        timingText = "파일 데이터 송출을 잠시 멈췄습니다."
        throughputText = "파일명·확장자·MIME·수정 시각 포함"
        privacyText = "이 QR을 읽은 수신기만 원래 파일 정보를 표시합니다."

        let item = DispatchWorkItem { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                self.showCurrentFrame()
                if resume {
                    self.start()
                }
            }
        }
        metadataWorkItem = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: item)
    }

    private func advanceFrame() {
        guard isPlaying, !frames.isEmpty else { return }
        frameCounter += 1
        showCurrentFrame()
    }

    private func elapsed() -> TimeInterval {
        elapsedBeforeStart + (startedAt.map { Date().timeIntervalSince($0) } ?? 0)
    }

    private func showCurrentFrame() {
        guard !frames.isEmpty else { return }
        let position = frameCounter % frames.count
        qrImage = generator.image(for: frames[position])
        progress = Double(position + 1) / Double(frames.count)
        frameLabel = "프레임 \(position + 1) / \(frames.count) · \(speedProfile.title)"
        updateStatistics()
    }

    private func updateStatistics() {
        guard !frames.isEmpty else { return }
        let elapsedValue = elapsed()
        let position = frameCounter % frames.count
        let actualRate = elapsedValue > 0.5 ? Double(frameCounter) / elapsedValue : speedProfile.qrRate
        let remaining = max(0, frames.count - position - 1)
        let eta = Double(remaining) / max(0.1, actualRate)
        let dataRatio = Double(max(0, frames.count - 1)) > 0
            ? Double(max(0, frames.count - 1 - (frames.count / (qrParityGroupSize + 1)))) / Double(frames.count)
            : 0
        let estimatedBytes = actualRate * Double(chunkSize) * max(0.1, min(1, dataRatio))
        timingText = "경과 \(formatDuration(elapsedValue)) · 한 바퀴 ETA \(formatDuration(eta))"
        throughputText = String(
            format: "실제 %.1f QR/s · 약 %.1f KB/s",
            actualRate,
            estimatedBytes / 1024
        )
    }
}
