// swift-tools-version: 5.10

import PackageDescription
import AppleProductTypes

let package = Package(
    name: "QRBeam",
    platforms: [
        .iOS("17.0")
    ],
    products: [
        .iOSApplication(
            name: "QRBeam",
            targets: ["AppModule"],
            bundleIdentifier: "com.aether.qrbeam",
            teamIdentifier: "",
            displayVersion: "0.1.0",
            bundleVersion: "1",
            appIcon: .placeholder(icon: .star),
            accentColor: .presetColor(.blue),
            supportedDeviceFamilies: [.pad],
            supportedInterfaceOrientations: [
                .portrait,
                .portraitUpsideDown(.when(deviceFamilies: [.pad])),
                .landscapeLeft,
                .landscapeRight
            ],
            capabilities: [
                .camera(purposeString: "QR 파일 전송 프레임을 인식하기 위해 카메라를 사용합니다.")
            ]
        )
    ],
    targets: [
        .executableTarget(
            name: "AppModule",
            path: "Sources"
        )
    ]
)
