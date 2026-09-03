import AVFoundation
import Foundation
import CoreImage
import AppKit

// grab <video> <outdir> <t1> <t2> ...   — writes frame-<t>.png at each second offset
let args = CommandLine.arguments
guard args.count >= 4 else { print("usage: grab <video> <outdir> <seconds...>"); exit(1) }
let url = URL(fileURLWithPath: args[1])
let outDir = URL(fileURLWithPath: args[2])
let asset = AVURLAsset(url: url)
let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.requestedTimeToleranceBefore = .zero
gen.requestedTimeToleranceAfter = .zero
gen.maximumSize = CGSize(width: 3200, height: 3200)

let sem = DispatchSemaphore(value: 0)
Task {
    do {
        let dur = try await asset.load(.duration)
        FileHandle.standardError.write("duration \(CMTimeGetSeconds(dur))s\n".data(using: .utf8)!)
        for s in args.dropFirst(3) {
            guard let t = Double(s) else { continue }
            let time = CMTime(seconds: t, preferredTimescale: 600)
            let img = try await gen.image(at: time).image
            let rep = NSBitmapImageRep(cgImage: img)
            guard let data = rep.representation(using: .png, properties: [:]) else { continue }
            let name = String(format: "f%07.2f.png", t).replacingOccurrences(of: ".", with: "_")
            try data.write(to: outDir.appendingPathComponent(name + ".png"))
            print("\(t) -> \(img.width)x\(img.height)")
        }
    } catch { print("error: \(error)") }
    sem.signal()
}
sem.wait()
