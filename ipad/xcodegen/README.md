# Xcode 대체 빌드

Swift Playgrounds가 `.swiftpm` 패키지를 열지 못할 때 사용하는 대체 방법입니다.

1. macOS에 Xcode와 XcodeGen을 설치합니다.
2. 이 폴더에서 `xcodegen generate`를 실행합니다.
3. 생성된 `QRBeam.xcodeproj`를 Xcode로 엽니다.
4. Signing & Capabilities에서 본인의 Team을 선택하고 iPad에서 실행합니다.
