import AppKit
import Foundation
// sheet <out.png> <cols> <cellW> <label...> <img...>  — tiles images with index labels
let a = CommandLine.arguments
guard a.count > 4 else { print("usage: sheet out cols cellW imgs..."); exit(1) }
let out = URL(fileURLWithPath: a[1])
let cols = Int(a[2])!, cellW = Int(a[3])!
let paths = Array(a.dropFirst(4))
let imgs = paths.compactMap { NSImage(contentsOfFile: $0) }
guard let first = imgs.first else { exit(1) }
let ar = first.size.height / first.size.width
let cellH = Int(Double(cellW) * ar) + 22
let rows = (imgs.count + cols - 1) / cols
let W = cols * cellW, H = rows * cellH
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: W, pixelsHigh: H, bitsPerSample: 8,
    samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
    bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSColor.white.setFill(); NSRect(x: 0, y: 0, width: W, height: H).fill()
let attrs: [NSAttributedString.Key: Any] = [
    .font: NSFont.monospacedSystemFont(ofSize: 13, weight: .bold), .foregroundColor: NSColor.black]
for (i, img) in imgs.enumerated() {
    let r = i / cols, c = i % cols
    let x = c * cellW, y = H - (r + 1) * cellH
    img.draw(in: NSRect(x: x, y: y, width: cellW, height: cellH - 22))
    let label = (paths[i] as NSString).lastPathComponent
        .replacingOccurrences(of: "f", with: "").replacingOccurrences(of: ".png", with: "")
        .replacingOccurrences(of: "_", with: ".")
    (label as NSString).draw(at: NSPoint(x: x + 6, y: y + cellH - 20), withAttributes: attrs)
    NSColor.lightGray.setStroke()
    NSBezierPath(rect: NSRect(x: x, y: y, width: cellW, height: cellH)).stroke()
}
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: out)
print("wrote \(out.path) \(W)x\(H)")
