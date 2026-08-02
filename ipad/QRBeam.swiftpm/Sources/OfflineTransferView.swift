import SwiftUI
import UniformTypeIdentifiers

struct OfflineTransferView: View {
    @StateObject private var model = OfflineTransferViewModel()
    @State private var importerPresented = false

    var body: some View {
        NavigationStack {
            GeometryReader { proxy in
                HStack(spacing: 24) {
                    ZStack {
                        CameraPreview(session: model.scanner.session)
                            .clipShape(RoundedRectangle(cornerRadius: 22))
                        RoundedRectangle(cornerRadius: 16)
                            .stroke(.white.opacity(0.9), lineWidth: 4)
                            .frame(
                                width: min(440, proxy.size.width * 0.42),
                                height: min(440, proxy.size.width * 0.42)
                            )
                        VStack {
                            Spacer()
                            Text(model.scanner.status)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 8)
                                .background(.black.opacity(0.65), in: Capsule())
                                .foregroundStyle(.white)
                                .padding(.bottom, 20)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                    Form {
                        Section("Windows 연결") {
                            Text(model.connectionText)
                                .font(.footnote)
                            Button(model.scanner.isRunning ? "QR 스캔 중지" : "연결 QR 스캔") {
                                model.scanner.isRunning ? model.stopScanner() : model.startScanner()
                            }
                            .buttonStyle(.borderedProminent)
                            if model.pairing != nil {
                                Button("핫스팟 다시 연결") { model.joinHotspot() }
                                    .disabled(model.isConnecting)
                                Button("수신 서버 확인") { model.probeReceiver() }
                            }
                        }

                        Section("보낼 파일") {
                            Button("파일 선택") { importerPresented = true }
                                .disabled(model.isUploading)
                            Text(model.selectedFilename)
                                .lineLimit(2)
                            Text(model.fileDetail)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }

                        Section("전송 상태") {
                            ProgressView(value: model.progress)
                            Text(model.timingText)
                                .font(.body.monospacedDigit())
                            Text(model.throughputText)
                                .font(.body.monospacedDigit())
                            if model.completed {
                                Label("Windows에서 SHA-256 검증 완료", systemImage: "checkmark.seal.fill")
                                    .foregroundStyle(.green)
                            }
                        }

                        Section {
                            Button(model.isUploading ? "전송 중…" : "Windows로 보내기") {
                                model.startUpload()
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(model.isUploading || model.pairing == nil || model.selectedFilename == "선택된 파일 없음")

                            if model.isUploading {
                                Button("전송 취소", role: .destructive) { model.cancelUpload() }
                            }
                        }

                        Section("보안과 안정성") {
                            Text("파일 본문은 암호화하지 않는 로컬 HTTP로 전송합니다. Windows 핫스팟 비밀번호와 일회용 토큰으로 접근을 제한합니다.")
                            Text("Windows는 임시 파일로 수신한 뒤 SHA-256이 원본과 같을 때만 저장합니다.")
                        }
                        .font(.footnote)
                    }
                    .frame(maxWidth: 420)
                }
                .padding(24)
            }
            .navigationTitle("QRBeam Offline")
            .fileImporter(
                isPresented: $importerPresented,
                allowedContentTypes: [.item],
                allowsMultipleSelection: false
            ) { result in
                switch result {
                case .success(let urls):
                    if let url = urls.first { model.loadFile(url: url) }
                case .failure(let error):
                    model.errorMessage = error.localizedDescription
                }
            }
            .alert("오류", isPresented: Binding(
                get: { model.errorMessage != nil },
                set: { if !$0 { model.errorMessage = nil } }
            )) {
                Button("확인", role: .cancel) { model.errorMessage = nil }
            } message: {
                Text(model.errorMessage ?? "")
            }
            .onDisappear {
                model.stopScanner()
                if model.isUploading { model.cancelUpload() }
            }
        }
    }
}
