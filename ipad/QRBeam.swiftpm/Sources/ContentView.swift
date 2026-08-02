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
                    VStack(spacing: 12) {
                        Group {
                            if let qrImage = model.qrImage {
                                Image(uiImage: qrImage)
                                    .interpolation(.none)
                                    .resizable()
                                    .scaledToFit()
                                    .padding(18)
                                    .background(.white)
                                    .clipShape(RoundedRectangle(cornerRadius: 22))
                                    .shadow(radius: 6)
                            } else {
                                ContentUnavailableView(
                                    "파일을 선택하세요",
                                    systemImage: "doc.badge.plus",
                                    description: Text("복구 QR이 포함된 고속 애니메이션 QR로 전송합니다.")
                                )
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)

                        ProgressView(value: model.progress)
                        Text(model.frameLabel)
                            .font(.footnote.monospacedDigit())
                            .foregroundStyle(.secondary)
                        Text(model.timingText)
                            .font(.footnote.monospacedDigit())
                        Text(model.throughputText)
                            .font(.footnote.monospacedDigit())
                    }
                    .frame(width: max(440, proxy.size.width * 0.62))

                    Form {
                        Section("파일") {
                            Button("파일 선택") { importerPresented = true }
                            Text(model.filename)
                                .lineLimit(2)
                            Text(model.detail)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }

                        Section("안정적인 고속 전송") {
                            Picker("속도 프로필", selection: $model.speedProfile) {
                                ForEach(SendSpeedProfile.allCases) { profile in
                                    Text(profile.title).tag(profile)
                                }
                            }
                            .pickerStyle(.menu)
                            .disabled(model.isPlaying)

                            Text(model.speedProfile.note)
                                .font(.footnote)
                                .foregroundStyle(
                                    model.speedProfile == .turbo ? AnyShapeStyle(.orange) : AnyShapeStyle(.secondary)
                                )

                            Stepper(
                                "청크: \(model.chunkSize) bytes",
                                value: $model.chunkSize,
                                in: 600...1400,
                                step: 100
                            )
                            .disabled(model.qrImage != nil)

                            Text("기본 안정 모드는 60Hz 화면에서 같은 QR을 4프레임 유지합니다. 데이터 8개마다 XOR 복구 QR을 추가합니다.")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }

                        Section("개인정보") {
                            Text(model.privacyText)
                                .font(.footnote)
                            Button("파일 정보 QR 보내기 · 3초") {
                                model.showMetadataTemporarily()
                            }
                            .disabled(model.qrImage == nil)
                        }

                        Section {
                            Button(model.isPlaying ? "일시정지" : "전송 시작") {
                                model.toggle()
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(model.qrImage == nil)
                        }

                        Section("무결성") {
                            Text("프레임마다 CRC32를 검사하고, 완성된 파일의 SHA-256이 원본과 같을 때만 완료 처리합니다.")
                                .font(.footnote)
                        }
                    }
                    .frame(maxWidth: 400)
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
                        Section("수신 상태") {
                            Text(model.filename)
                                .font(.headline)
                            ProgressView(value: model.progress)
                            Text(model.receivedText)
                                .font(.body.monospacedDigit())
                            Text(model.timingText)
                                .font(.footnote.monospacedDigit())
                            Text(model.throughputText)
                                .font(.footnote.monospacedDigit())
                            Text(model.metadataText)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                            Text(model.status)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }

                        Section("저장 이름") {
                            TextField("파일 이름", text: $model.exportFilename)
                                .textInputAutocapitalization(.never)
                            Text("파일 정보 QR을 받지 않아도 원하는 이름으로 저장할 수 있습니다.")
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
                            Section("완료 · SHA-256 확인됨") {
                                Button("파일 앱에 저장") { exporterPresented = true }
                                    .buttonStyle(.borderedProminent)
                                Text("\(file.data.count.formatted()) bytes")
                                    .font(.footnote)
                            }
                        }
                    }
                    .frame(maxWidth: 410)
                }
                .padding(24)
            }
            .navigationTitle("QRBeam 받기")
            .fileExporter(
                isPresented: $exporterPresented,
                document: ReceivedFileDocument(data: model.completedFile?.data ?? Data()),
                contentType: .data,
                defaultFilename: safeFilename(model.exportFilename)
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
                Section("QRBeam 0.2") {
                    Text("기본 청크 1,400바이트와 60Hz 기반 15 QR/s 안정 모드를 사용합니다.")
                    Text("고속 30 QR/s와 터보 60 QR/s는 환경이 안정적일 때 선택할 수 있습니다.")
                }
                Section("안정성") {
                    Text("데이터 8개마다 XOR 복구 QR을 보내 한 그룹에서 누락 하나를 즉시 복원합니다.")
                    Text("CRC32와 최종 SHA-256 검증을 모두 통과해야만 파일을 내보낼 수 있습니다.")
                }
                Section("개인정보") {
                    Text("파일명·확장자·MIME·수정 시각은 기본 데이터 흐름에 포함되지 않습니다.")
                    Text("송신자가 ‘파일 정보 QR 보내기’를 누른 경우에만 원래 파일 정보를 받습니다.")
                }
            }
            .navigationTitle("정보")
        }
    }
}
