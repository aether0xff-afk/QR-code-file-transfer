import Foundation
import CoreImage
import CoreImage.CIFilterBuiltins
import UIKit

final class QRImageGenerator {
    private let context = CIContext()
    private let cache = NSCache<NSString, UIImage>()

    init() {
        cache.countLimit = 64
    }

    func image(for text: String) -> UIImage? {
        let key = text as NSString
        if let cached = cache.object(forKey: key) {
            return cached
        }
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage else { return nil }
        let scaled = output.transformed(by: CGAffineTransform(scaleX: 9, y: 9))
        guard let cgImage = context.createCGImage(scaled, from: scaled.extent) else { return nil }
        let image = UIImage(cgImage: cgImage)
        cache.setObject(image, forKey: key)
        return image
    }

    func clearCache() {
        cache.removeAllObjects()
    }
}
