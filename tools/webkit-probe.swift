// Debug helper: loads a URL in WKWebView (the engine the app uses), waits, evaluates
// JavaScript and saves a snapshot.  usage: webkit-probe URL SECONDS OUT.png [JS] [SCROLL_JS] [PRINT.pdf]
// With PRINT.pdf the page is also printed (print media styles) into that PDF, as the app's Tisk does.
import AppKit
import WebKit

let args = CommandLine.arguments
let url = URL(string: args[1])!
let wait = Double(args[2]) ?? 10
let out = args[3]
let script = args.count > 4 ? args[4] : "document.title"
let scroll = args.count > 5 ? args[5] : ""
let printTo = args.count > 6 ? args[6] : ""

let app = NSApplication.shared
let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 900), styleMask: [.titled], backing: .buffered, defer: false)
let webView = WKWebView(frame: window.contentView!.bounds)
window.contentView = webView
// A window that is never shown gets no animation frames, and PDF.js renders pages in them. Off screen it does.
window.setFrameOrigin(NSPoint(x: -10000, y: -10000))
window.orderFrontRegardless()
webView.load(URLRequest(url: url))

DispatchQueue.main.asyncAfter(deadline: .now() + wait) {
    webView.evaluateJavaScript(scroll.isEmpty ? "0" : scroll) { _, _ in
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            webView.evaluateJavaScript("JSON.stringify((() => { return \(script); })())") { result, error in
                print(result as? String ?? "error: \(String(describing: error))")
                webView.takeSnapshot(with: nil) { image, _ in
                    if let image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                       let png = rep.representation(using: .png, properties: [:]) {
                        try? png.write(to: URL(fileURLWithPath: out))
                    }
                    if !printTo.isEmpty {
                        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
                        info.topMargin = 0; info.bottomMargin = 0; info.leftMargin = 0; info.rightMargin = 0
                        info.horizontalPagination = .fit
                        info.jobDisposition = .save
                        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = URL(fileURLWithPath: printTo)
                        let operation = webView.printOperation(with: info)
                        operation.showsPrintPanel = false
                        operation.showsProgressPanel = false
                        operation.view?.frame = webView.bounds
                        operation.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
                        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { exit(0) }
                        return
                    }
                    exit(0)
                }
            }
        }
    }
}
app.run()
