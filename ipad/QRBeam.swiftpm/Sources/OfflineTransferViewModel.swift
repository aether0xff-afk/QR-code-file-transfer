import Combine
import CryptoKit
import Foundation
import NetworkExtension

private let offlinePairingPrefix = "QRB3:"

struct OfflinePairingInfo: Codable, Equatable {
    let version: Int
    let ssid: String
    let password: String
    let host: String
    let port: Int
    let token: String
    let transport: String

    enum CodingKeys: String, CodingKey {
        case version, ssid, password, host, port, token
        case transport = "protocol"
    }

    var baseURL: URL? {
        URL(string: "\(transport)://\(host):\(port)")
    }

    static func decode(_ value: String) throws -> OfflinePairingInfo {
        guard value.hasPrefix(offlinePairingPrefix) else {
            throw NSError(domain: "QRBeamOffline", code: 1, userInfo: [NSLocalizedDescriptionKey: "QRBeam 오프라인 연결 QR이 아닙니다."])
        }
        let encoded = String(value.dropFirst(offlinePairingPrefix.count))
        guard let data = base64URLDecode(encoded) else {
            throw NSError(domain: "QRBeamOffline", code: 2, userInfo: [NSLocalizedDescriptionKey: "연결 QR 데이터가 손상되었습니다."])
        }
        let info = try JSONDecoder().decode(OfflinePairingInfo.self, from: data)
        guard info.version == 3,
              info.transport == "http",
              !info.ssid.isEmpty,
              info.password.count >= 8,
              !info.host.isEmpty,
              (1...65535).contains(info.port),
              !info.token.isEmpty else {
            throw NSError(domain: "QRBeamOffline", code: 3, userInfo: [NSLocalizedDescriptionKey: "지원하지 않거나 불완전한 연결 정보입니다."])
        }
        return info
    }
}

private func base64URLDecode(_ value: String) -> Data? {
    var standard = value
        .replacingOccurrences(of: "-", with: "+")
        .replacingOccurrences(of: "_", with: "/")
    standard += String(repeating: "=", count: (4 - standard.count % 4) % 4)
    return Data(base64Encoded: standard)
}

private func base64URLEncode(_ data: Data) -> String {
    data.base64EncodedString()
        .replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "=", with: "")
}

private func sha256File(_ url: URL) throws -> String {
    let handle = try FileHandle(forReadingFrom: url)
    defer { try? handle.close() }
    var hasher = SHA256()
    while true {
        let data = try handle.read(upToCount: 1024 * 1024) ?? Data()
        if data.isEmpty { break }
        hasher.update(data: data)
    }
    return hasher.finalize().map { String(format: "%02x", $0) }.joined()
}

final class OfflineTransferViewModel: NSObject, ObservableObject, URLSessionTaskDelegate, URLSessionDataDelegate {
    @Published var pairing: OfflinePairingInfo?
    @Published var connectionText = "Windows QRBeam Offline의 연결 QR을 스캔하세요."
    @Published var selectedFilename = "선택된 파일 없음"
    @Published var fileDetail = ""
    @Published var progress: Double = 0
    @Published var timingText = "대기 중"
    @Published var throughputText = "0.0 MB/s"
    @Published var isUploading = false
    @Published var isConnecting = false
    @Published var completed = false
    @Published var errorMessage: String?

    let scanner = CameraScanner()

    private var temporaryFileURL: URL?
    private var fileSize: Int64 = 0
    private var fileSHA256 = ""
    private var startedAt: Date?
    private var responseData = Data()
    private var activeTask: URLSessionUploadTask?
    private lazy var session: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 24 * 60 * 60
        configuration.waitsForConnectivity = true
        configuration.httpMaximumConnectionsPerHost = 1
        return URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
    }()

    override init() {
        super.init()
        scanner.acceptedPrefixes = [offlinePairingPrefix]
        scanner.onCode = { [weak self] value in
            self?.acceptPairingQR(value)
        }
    }

    deinit {
        scanner.stop()
        session.invalidateAndCancel()
        if let temporaryFileURL { try? FileManager.default.removeItem(at: temporaryFileURL) }
    }

    func startScanner() {
        scanner.acceptedPrefixes = [offlinePairingPrefix]
        scanner.start()
    }

    func stopScanner() {
        scanner.stop()
    }

    func acceptPairingQR(_ value: String) {
        do {
            let decoded = try OfflinePairingInfo.decode(value)
            pairing = decoded
            connectionText = "\(decoded.ssid) · \(decoded.host):\(decoded.port)"
            scanner.stop()
            joinHotspot()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func joinHotspot() {
        guard let pairing else { return }
        isConnecting = true
        completed = false
        connectionText = "‘\(pairing.ssid)’ 연결 승인 대기 중…"
        let configuration = NEHotspotConfiguration(
            ssid: pairing.ssid,
            passphrase: pairing.password,
            isWEP: false
        )
        configuration.joinOnce = false
        NEHotspotConfigurationManager.shared.apply(configuration) { [weak self] error in
            DispatchQueue.main.async {
                guard let self else { return }
                self.isConnecting = false
                if let error = error as NSError? {
                    if error.domain == NEHotspotConfigurationErrorDomain,
                       error.code == NEHotspotConfigurationError.alreadyAssociated.rawValue {
                        self.connectionText = "핫스팟 연결됨 · 수신 서버 확인 중…"
                        self.probeReceiver()
                    } else {
                        self.errorMessage = error.localizedDescription
                        self.connectionText = "핫스팟 연결 실패"
                    }
                } else {
                    self.connectionText = "핫스팟 연결됨 · 수신 서버 확인 중…"
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                        self.probeReceiver()
                    }
                }
            }
        }
    }

    func probeReceiver() {
        guard let pairing, let baseURL = pairing.baseURL else { return }
        var request = URLRequest(url: baseURL.appendingPathComponent("status"))
        request.setValue(pairing.token, forHTTPHeaderField: "X-QRBeam-Token")
        request.timeoutInterval = 8
        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            DispatchQueue.main.async {
                if let error {
                    self?.connectionText = "서버 연결 실패 · Windows 주소와 핫스팟을 확인하세요."
                    self?.errorMessage = error.localizedDescription
                    return
                }
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                self?.connectionText = status == 200 ? "오프라인 수신 서버 연결 완료" : "서버 응답 오류: \(status)"
            }
        }.resume()
    }

    func loadFile(url: URL) {
        guard !isUploading else { return }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            if let temporaryFileURL { try? FileManager.default.removeItem(at: temporaryFileURL) }
            let safeName = safeFilename(url.lastPathComponent)
            let temporary = FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString + "-" + safeName)
            try FileManager.default.copyItem(at: url, to: temporary)
            let attributes = try FileManager.default.attributesOfItem(atPath: temporary.path)
            let size = (attributes[.size] as? NSNumber)?.int64Value ?? 0
            let hash = try sha256File(temporary)
            temporaryFileURL = temporary
            fileSize = size
            fileSHA256 = hash
            selectedFilename = safeName
            fileDetail = "\(ByteCountFormatter.string(fromByteCount: size, countStyle: .file)) · SHA-256 준비됨"
            progress = 0
            timingText = "전송 준비 완료"
            throughputText = "0.0 MB/s"
            completed = false
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func startUpload() {
        guard let pairing,
              let baseURL = pairing.baseURL,
              let fileURL = temporaryFileURL,
              fileSize >= 0,
              !fileSHA256.isEmpty else {
            errorMessage = "연결 QR과 보낼 파일을 먼저 준비하세요."
            return
        }
        var request = URLRequest(url: baseURL.appendingPathComponent("upload"))
        request.httpMethod = "POST"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.setValue(pairing.token, forHTTPHeaderField: "X-QRBeam-Token")
        request.setValue(base64URLEncode(Data(selectedFilename.utf8)), forHTTPHeaderField: "X-File-Name")
        request.setValue(String(fileSize), forHTTPHeaderField: "X-File-Size")
        request.setValue(fileSHA256, forHTTPHeaderField: "X-File-SHA256")
        request.setValue(String(fileSize), forHTTPHeaderField: "Content-Length")
        request.timeoutInterval = 60

        responseData = Data()
        startedAt = Date()
        progress = 0
        isUploading = true
        completed = false
        timingText = "전송 시작"
        let task = session.uploadTask(with: request, fromFile: fileURL)
        activeTask = task
        task.resume()
    }

    func cancelUpload() {
        activeTask?.cancel()
        activeTask = nil
        isUploading = false
        timingText = "전송 취소됨"
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didSendBodyData bytesSent: Int64,
        totalBytesSent: Int64,
        totalBytesExpectedToSend: Int64
    ) {
        let elapsed = max(0.001, Date().timeIntervalSince(startedAt ?? Date()))
        let speed = Double(totalBytesSent) / elapsed
        let remaining = max(0, totalBytesExpectedToSend - totalBytesSent)
        let eta = speed > 0 ? Double(remaining) / speed : nil
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.progress = totalBytesExpectedToSend > 0
                ? Double(totalBytesSent) / Double(totalBytesExpectedToSend)
                : 0
            self.timingText = "경과 \(formatDuration(elapsed)) · ETA \(formatDuration(eta))"
            self.throughputText = String(format: "%.2f MB/s", speed / 1_048_576)
        }
    }

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive data: Data
    ) {
        responseData.append(data)
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        let status = (task.response as? HTTPURLResponse)?.statusCode ?? 0
        let body = responseData
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.activeTask = nil
            self.isUploading = false
            if let error {
                self.errorMessage = error.localizedDescription
                self.timingText = "전송 실패"
                return
            }
            guard status == 201,
                  let result = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                  result["ok"] as? Bool == true,
                  result["sha256"] as? String == self.fileSHA256 else {
                let message = String(data: body, encoding: .utf8) ?? "HTTP \(status)"
                self.errorMessage = "수신 검증 실패: \(message)"
                self.timingText = "전송 실패"
                return
            }
            self.progress = 1
            self.completed = true
            self.timingText = "완료 · Windows SHA-256 검증 성공"
        }
    }
}
