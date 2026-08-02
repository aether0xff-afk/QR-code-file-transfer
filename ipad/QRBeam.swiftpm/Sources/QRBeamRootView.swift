import SwiftUI

struct QRBeamRootView: View {
    var body: some View {
        TabView {
            OfflineTransferView()
                .tabItem { Label("오프라인", systemImage: "wifi") }
            SendView()
                .tabItem { Label("QR 보내기", systemImage: "qrcode") }
            ReceiveView()
                .tabItem { Label("QR 받기", systemImage: "viewfinder") }
            AboutView()
                .tabItem { Label("정보", systemImage: "info.circle") }
        }
    }
}
