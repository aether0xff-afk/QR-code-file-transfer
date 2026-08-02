import AVFoundation
import SwiftUI
import UIKit

final class CameraScanner: NSObject, ObservableObject, AVCaptureMetadataOutputObjectsDelegate {
    let session = AVCaptureSession()
    @Published private(set) var status = "카메라 준비 중…"
    @Published private(set) var isRunning = false

    var onCode: ((String) -> Void)?

    private let sessionQueue = DispatchQueue(label: "QRBeam.camera.session")
    private let metadataQueue = DispatchQueue(label: "QRBeam.camera.metadata")
    private var configured = false
    private var lastCode = ""
    private var lastCodeTime = Date.distantPast

    func start() {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            configureAndStart()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                DispatchQueue.main.async {
                    if granted {
                        self?.configureAndStart()
                    } else {
                        self?.status = "카메라 권한이 필요합니다."
                    }
                }
            }
        default:
            status = "설정에서 카메라 권한을 허용하세요."
        }
    }

    func stop() {
        sessionQueue.async { [weak self] in
            guard let self, self.session.isRunning else { return }
            self.session.stopRunning()
            DispatchQueue.main.async {
                self.isRunning = false
                self.status = "카메라가 중지되었습니다."
            }
        }
    }

    private func configureAndStart() {
        sessionQueue.async { [weak self] in
            guard let self else { return }
            do {
                if !self.configured {
                    try self.configureSession()
                    self.configured = true
                }
                guard !self.session.isRunning else { return }
                self.session.startRunning()
                DispatchQueue.main.async {
                    self.isRunning = true
                    self.status = "QR 코드를 비추세요."
                }
            } catch {
                DispatchQueue.main.async {
                    self.status = "카메라 오류: \(error.localizedDescription)"
                }
            }
        }
    }

    private func configureSession() throws {
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        session.sessionPreset = .high

        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            throw NSError(domain: "QRBeam", code: 10, userInfo: [NSLocalizedDescriptionKey: "후면 카메라를 찾을 수 없습니다."])
        }
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else {
            throw NSError(domain: "QRBeam", code: 11, userInfo: [NSLocalizedDescriptionKey: "카메라 입력을 추가할 수 없습니다."])
        }
        session.addInput(input)

        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else {
            throw NSError(domain: "QRBeam", code: 12, userInfo: [NSLocalizedDescriptionKey: "QR 인식 출력을 추가할 수 없습니다."])
        }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: metadataQueue)
        output.metadataObjectTypes = [.qr]
    }

    func metadataOutput(
        _ output: AVCaptureMetadataOutput,
        didOutput metadataObjects: [AVMetadataObject],
        from connection: AVCaptureConnection
    ) {
        guard let object = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
              object.type == .qr,
              let value = object.stringValue,
              value.hasPrefix(qrWirePrefix) else { return }

        let now = Date()
        if value == lastCode && now.timeIntervalSince(lastCodeTime) < 0.18 { return }
        lastCode = value
        lastCodeTime = now
        DispatchQueue.main.async { [weak self] in
            self?.onCode?(value)
        }
    }
}

final class CameraPreviewUIView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }

    var previewLayer: AVCaptureVideoPreviewLayer {
        layer as! AVCaptureVideoPreviewLayer
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> CameraPreviewUIView {
        let view = CameraPreviewUIView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: CameraPreviewUIView, context: Context) {
        uiView.previewLayer.session = session
    }
}
