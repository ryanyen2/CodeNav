import CoreGraphics
import Foundation
// winlist [ownerFilter] — id, owner, bounds, title of every on-screen window
let filter = CommandLine.arguments.count > 1 ? CommandLine.arguments[1].lowercased() : ""
let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
guard let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else { exit(1) }
for w in list {
    let owner = (w[kCGWindowOwnerName as String] as? String) ?? ""
    if !filter.isEmpty && !owner.lowercased().contains(filter) { continue }
    let id = (w[kCGWindowNumber as String] as? Int) ?? 0
    let title = (w[kCGWindowName as String] as? String) ?? ""
    let b = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let x = (b["X"] as? Double) ?? 0, y = (b["Y"] as? Double) ?? 0
    let ww = (b["Width"] as? Double) ?? 0, hh = (b["Height"] as? Double) ?? 0
    if ww < 300 || hh < 200 { continue }
    print("\(id)\t\(owner)\t\(Int(x)),\(Int(y)) \(Int(ww))x\(Int(hh))\t\(title)")
}
