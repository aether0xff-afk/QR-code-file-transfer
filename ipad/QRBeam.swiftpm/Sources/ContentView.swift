import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    var body: some View {
        TabView {
            SendView()
                .tabItem { Label("보내기", systemImage: "qrcode") }
            ReceiveView()
                .tabItem { Label("받기", systemImage: "viewfinder") }
            AboutView()
                .tabItem { Label("정보", systemImage: "info.circle") }
        }
    }
}

struct SendView: View {
    @StateObject private var model = SendViewModel()
    @State private var importerPresented = false

    var body: some View {
        NavigationStack {
            GeometryReader { proxy in
                HStack(spacing: 24) {
                    VStack(spacing: 16) {
                        Group {
                            if let qrImage = model.qrImage {
                                Image(uiImage: qrImage)
                                    .interpolation(.none)
                                    .resizable()
                                    .scaledToFit()
                                    .padding(20)
                                    .background(.white)
                                    .clipShape(RoundedRectangle(cornerRadius: 22))
                                    .shadow(radius: 6)
                            } else {
                                ContentUnavailableView(
                                    "파일을 선택하세요",
                                    systemImage: "doc.badge.plus",
                                    description: Text("파일 바이트를 여러 QR 프레임으로 나눠 표시합니다.")
                                )
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)

                        ProgressView(value: model.progress)
                        Text(model.frameLabel)
                            .font(.footnote.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    .frame(width: max(420, proxy.size.width * 0.62))

                    Form {
                        Section("파일") {
                            Button("파일 선택") { importerPresented = true }
                            Text(model.filename)
                                .lineLimit(2)
                            Text(model.detail)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }

                        Section("전송 설정") {
                            VStack(alignment: .leading) {
                                Text("속도: \(Int(model.fps)) FPS")
                                Slider(value: $model.fps, in: 1...12, step: 1)
                            }
                            Stepper("청크: \(model.chunkSize) bytes", value: $model.chunkSize, in: 256...1000, step: 50)
                                .disabled(model.qrImage != nil)
                        }

                        Section {
                            Button(model.isPlaying ? "일시정지" : "전송 시작") {
                                model.toggle()
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(model.qrImage == nil)
                        }

                        Section("사용 팁") {
                            Text("화면 밝기를 높이고 두 기기를 정면으로 맞추세요. 일부 QR을 놓쳐도 전체 프레임이 반복됩니다.")
                                .font(.footnote)
                        }
                    }
                    .frame(maxWidth: 380)
                }
                .padding(24)
            }
            .navigationTitle("QRBeam 보내기")
            .fileImporter(
                isPresented: $importerPresented,
                allowedContentTypes: [.item],
                allowsMultipleSelection: false
            ) { result in
                switch result {
                case .success(let urls):
                    if let url = urls.first { model.load(url: url) }
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
            .onDisappear { model.stop() }
        }
    }
}

struct ReceiveView: View {
    @StateObject private var model = ReceiveViewModel()
    @State private var exporterPresented = false

    var body: some View {
        NavigationStack {
            GeometryReader { proxy in
                HStack(spacing: 24) {
                    ZStack {
                        CameraPreview(session: model.scanner.session)
                            .clipShape(RoundedRectangle(cornerRadius: 22))
                        RoundedRectangle(cornerRadius: 16)
                            .stroke(.white.opacity(0.9), lineWidth: 4)
                            .frame(width: min(440, proxy.size.width * 0.42), height: min(440, proxy.size.width * 0.42))
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
                        Section("수신 상태") {
                            Text(model.filename)
                                .font(.headline)
                            ProgressView(value: model.progress)
                            Text(model.receivedText)
                                .font(.body.monospacedDigit())
                            Text(model.status)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }

                        Section {
                            Button(model.scanner.isRunning ? "카메라 중지" : "카메라 시작") {
                                model.scanner.isRunning ? model.stop() : model.start()
                            }
                            .buttonStyle(.borderedProminent)

                            Button("수신 기록 초기화", role: .destructive) {
                                model.reset()
                            }
                        }

                        if let file = model.completedFile {
                            Section("완료") {
                                Button("파일 앱에 저장") { exporterPresented = true }
                                    .buttonStyle(.borderedProminent)
                                Text("\(file.name) · \(file.data.count.formatted()) bytes")
                                    .font(.footnote)
                            }
                        }
                    }
                    .frame(maxWidth: 390)
                }
                .padding(24)
            }
            .navigationTitle("QRBeam 받기")
            .fileExporter(
                isPresented: $exporterPresented,
                document: ReceivedFileDocument(data: model.completedFile?.data ?? Data()),
                contentType: .data,
                defaultFilename: model.completedFile?.name ?? "received_file.bin"
            ) { result in
                if case .failure(let error) = result {
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
            .onAppear { model.start() }
            .onDisappear { model.stop() }
        }
    }
}

struct AboutView: View {
    var body: some View {
        NavigationStack {
            List {
                Section("QRBeam 0.1") {
                    Text("파일을 700바이트 안팎의 청크로 분할해 애니메이션 QR로 전송합니다.")
                    Text("각 프레임은 CRC32로 검사하고, 완성된 파일은 SHA-256으로 원본과 같은지 확인합니다.")
                }
                Section("현재 한계") {
                    Text("초기 버전은 속도보다 안정성을 우선합니다. 1~5MB 파일에서 먼저 시험하는 것을 권장합니다.")
                    Text("암호화와 누락 청크 역방향 요청은 다음 버전에서 추가할 수 있습니다.")
                }
            }
            .navigationTitle("정보")
        }
    }
}
