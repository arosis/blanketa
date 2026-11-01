import AppKit
import PDFKit
import WebKit
import UniformTypeIdentifiers

final class FormWindow: NSObject, NSWindowDelegate, WKScriptMessageHandler, WKUIDelegate, WKNavigationDelegate {
    let window: NSWindow
    let webView: WKWebView
    let server = Process()
    let file: URL?
    var onClose: (() -> Void)?
    var onOpen: ((URL?) -> Void)?
    var formServerPort: Int?

    init(file: URL?) {
        self.file = file
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: config)
        super.init()
        config.userContentController.add(self, name: "app")
        webView.uiDelegate = self
        webView.navigationDelegate = self
        // Swift owns the window; AppKit's own release on close would free it a second time.
        window.isReleasedWhenClosed = false
        window.title = file?.lastPathComponent ?? "Blanketa"
        window.representedURL = file
        window.contentView = webView
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("FormWindow")
        if file != nil {
            webView.loadHTMLString("<p style='font:14px -apple-system;padding:24px'>Otevírám formulář…</p>", baseURL: nil)
        }
        window.makeKeyAndOrderFront(nil)
        startFormServer(file: file)
    }

    private func startFormServer(file: URL?) {
        let resources = Bundle.main.resourceURL!
        server.executableURL = resources.appendingPathComponent("zfo-server/zfo-server")
        server.arguments = file.map { ["--file", $0.path, "0"] } ?? ["--empty", "0"]
        var env = ProcessInfo.processInfo.environment
        env["ZFO_MACSIGN"] = resources.appendingPathComponent("macsign").path
        server.environment = env
        let output = Pipe()
        server.standardOutput = output
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            guard let line = String(data: handle.availableData, encoding: .utf8),
                  let port = line.split(separator: "\n").first(where: { $0.hasPrefix("PORT ") })?.dropFirst(5) else { return }
            handle.readabilityHandler = nil
            DispatchQueue.main.async {
                self?.formServerPort = Int(port)
                self?.webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))
            }
        }
        do {
            try server.run()
        } catch {
            webView.loadHTMLString("<p style='font:14px -apple-system;padding:24px'>Nelze spustit: \(error)</p>", baseURL: nil)
        }
    }

    private func isFormServerPage(_ url: URL?) -> Bool {
        url?.scheme == "http" && url?.host == "127.0.0.1" && url?.port == formServerPort && formServerPort != nil
    }

    // A link or script must not take the window to another site: that page would reach the app through "app".
    // Web addresses open in the browser instead; the placeholder pages are about:blank.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = action.request.url
        if isFormServerPage(url) || url?.absoluteString == "about:blank" {
            return decisionHandler(.allow)
        }
        if let url, ["http", "https"].contains(url.scheme), action.navigationType == .linkActivated {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard message.frameInfo.isMainFrame, origin.protocol == "http", origin.host == "127.0.0.1", origin.port == formServerPort,
              let body = message.body as? [String: Any] else { return }
        switch body["action"] as? String {
        case "open": onOpen?(nil)
        case "openFile":
            if let path = body["path"] as? String { onOpen?(URL(fileURLWithPath: path)) }
        case "recent":
            let paths = RecentFiles.urls.map(\.path)
            let json = String(data: try! JSONEncoder().encode(paths), encoding: .utf8)!
            webView.evaluateJavaScript("window.zfoRecent && zfoRecent(\(json))")
        case "print": printPage()
        case "printPdf":
            if let path = body["path"] as? String { printPdfFile(URL(fileURLWithPath: path)) }
        case "reveal":
            if let path = body["path"] as? String {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
            }
        case "openUrl":
            if let link = body["url"] as? String, let url = URL(string: link), ["http", "https"].contains(url.scheme) {
                NSWorkspace.shared.open(url)
            }
        case "saveFile":
            if let name = body["name"] as? String, let content = body["content"] as? String {
                saveFile(name: name, folder: body["folder"] as? String, content: content)
            }
        default: break
        }
    }

    func sendActionToPage(_ action: String) {
        webView.evaluateJavaScript("window.zfoAction && zfoAction('\(action)')")
    }

    private func saveFile(name: String, folder: String?, content: String) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = name
        panel.allowedContentTypes = [.xml]
        if let folder { panel.directoryURL = URL(fileURLWithPath: folder, isDirectory: true) }
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url, let self else { return }
            do {
                try content.write(to: url, atomically: true, encoding: .utf8)
                let path = String(data: try JSONEncoder().encode(url.path), encoding: .utf8)!
                self.webView.evaluateJavaScript("window.zfoDataSaved && zfoDataSaved(\(path))")
            } catch {
                let alert = NSAlert(error: error)
                alert.beginSheetModal(for: self.window)
            }
        }
    }

    private func printPage() {
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        info.topMargin = 0; info.bottomMargin = 0; info.leftMargin = 0; info.rightMargin = 0
        info.horizontalPagination = .fit
        info.isVerticallyCentered = false
        info.jobDisposition = .spool
        let operation = webView.printOperation(with: info)
        operation.view?.frame = webView.bounds
        operation.jobTitle = window.title
        operation.runModal(for: window, delegate: self, didRun: #selector(pagePrintDidFinish(_:success:contextInfo:)), contextInfo: nil)
    }

    private func printPdfFile(_ url: URL) {
        guard let document = PDFDocument(url: url) else { return }
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        info.jobDisposition = .spool
        guard let operation = document.printOperation(for: info, scalingMode: .pageScaleToFit, autoRotate: true) else { return }
        operation.jobTitle = window.title
        operation.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
    }

    @objc private func pagePrintDidFinish(_ operation: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        webView.evaluateJavaScript("window.zfoPrinted && zfoPrinted()")
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.xml] + ["pfx", "p12"].compactMap { UTType(filenameExtension: $0) }
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "Pokračovat")
        alert.addButton(withTitle: "Zrušit")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    // prompt() in the page asks for secrets only (the password of a certificate file), so the field is masked.
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        alert.accessoryView = field
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Zrušit")
        alert.window.initialFirstResponder = field
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    func windowWillClose(_ notification: Notification) {
        webView.configuration.userContentController.removeScriptMessageHandler(forName: "app")
        if server.isRunning { server.terminate() }
        onClose?()
    }
}

enum RecentFiles {
    private static let key = "recentFiles"
    static var urls: [URL] {
        (UserDefaults.standard.stringArray(forKey: key) ?? [])
            .filter { FileManager.default.fileExists(atPath: $0) }.map { URL(fileURLWithPath: $0) }
    }
    static func add(_ url: URL) {
        let paths = [url.path] + (UserDefaults.standard.stringArray(forKey: key) ?? []).filter { $0 != url.path }
        UserDefaults.standard.set(Array(paths.prefix(10)), forKey: key)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var windows: [FormWindow] = []
    var openedFile = false

    func application(_ application: NSApplication, open urls: [URL]) {
        openedFile = true
        urls.forEach(open)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        DispatchQueue.main.async { if !self.openedFile { self.openEmptyWindow() } }
    }

    func applicationShouldOpenUntitledFile(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { openEmptyWindow() }
        return true
    }

    func openEmptyWindow() { track(FormWindow(file: nil)) }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        windows.forEach { if $0.server.isRunning { $0.server.terminate() } }
    }

    func open(_ url: URL) {
        let emptyWindows = windows.filter { $0.file == nil }
        track(FormWindow(file: url))
        RecentFiles.add(url)
        NSDocumentController.shared.noteNewRecentDocumentURL(url)
        // The request may come from the empty window's own page, so it closes once that call has returned.
        DispatchQueue.main.async { emptyWindows.forEach { $0.window.close() } }
    }

    private func track(_ form: FormWindow) {
        form.onClose = { [weak self, weak form] in self?.windows.removeAll { $0 === form } }
        form.onOpen = { [weak self] url in
            if let url { self?.open(url) } else { self?.openDocument(nil) }
        }
        windows.append(form)
    }

    @objc func openDocument(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "zfo") ?? .data, UTType(filenameExtension: "fo") ?? .data, .pdf]
        panel.allowsMultipleSelection = true
        if panel.runModal() == .OK {
            panel.urls.forEach(open)
        }
    }

    @objc func formAction(_ sender: NSMenuItem) {
        guard let action = sender.representedObject as? String,
              let form = windows.first(where: { $0.window.isKeyWindow }) ?? windows.last else { return }
        form.sendActionToPage(action)
    }

    private func formItem(_ title: String, _ action: String, _ key: String = "", _ modifiers: NSEvent.ModifierFlags = .command) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: #selector(formAction(_:)), keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        item.representedObject = action
        return item
    }

    private func submenuItem(title: String = "", _ entries: [NSMenuItem]) -> NSMenuItem {
        let item = NSMenuItem()
        item.submenu = NSMenu(title: title)
        entries.forEach(item.submenu!.addItem)
        return item
    }

    private func applicationMenu() -> NSMenuItem {
        submenuItem([
            formItem("Podpořit Blanketu…", "support"),
            .separator(),
            NSMenuItem(title: "Ukončit Blanketu", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"),
        ])
    }

    private func fileMenu() -> NSMenuItem {
        submenuItem(title: "Soubor", [
            NSMenuItem(title: "Otevřít…", action: #selector(openDocument(_:)), keyEquivalent: "o"),
            .separator(),
            formItem("Uložit bez podpisu", "save-plain", "s"),
            formItem("Podepsat a uložit…", "save", "s", [.command, .shift]),
            .separator(),
            formItem("Tisk…", "print", "p"),
            .separator(),
            NSMenuItem(title: "Zavřít", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w"),
        ])
    }

    private func editMenu() -> NSMenuItem {
        submenuItem(title: "Úpravy", [
            NSMenuItem(title: "Zpět", action: Selector(("undo:")), keyEquivalent: "z"),
            NSMenuItem(title: "Vyjmout", action: #selector(NSText.cut(_:)), keyEquivalent: "x"),
            NSMenuItem(title: "Kopírovat", action: #selector(NSText.copy(_:)), keyEquivalent: "c"),
            NSMenuItem(title: "Vložit", action: #selector(NSText.paste(_:)), keyEquivalent: "v"),
            NSMenuItem(title: "Vybrat vše", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"),
            .separator(),
            formItem("Najít…", "find", "f"),
            formItem("Najít další", "find-next", "g"),
            formItem("Najít předchozí", "find-previous", "g", [.command, .shift]),
        ])
    }

    private func formMenu() -> NSMenuItem {
        let f9Key = String(Character(UnicodeScalar(NSF9FunctionKey)!))
        let backspaceKey = String(Character(UnicodeScalar(NSBackspaceCharacter)!))
        return submenuItem(title: "Formulář", [
            formItem("Přepočítat hodnoty", "recalc", f9Key, []),
            formItem("Zkontrolovat formulář", "check", f9Key, .shift),
            .separator(),
            formItem("Obnovit výchozí hodnotu pole", "reset-field", backspaceKey, [.command, .shift]),
            formItem("Obnovit výchozí hodnoty v celém formuláři…", "reset"),
            .separator(),
            formItem("Import dat do formuláře…", "import-data"),
            formItem("Uložit data do souboru…", "export-data"),
        ])
    }

    private func viewMenu() -> NSMenuItem {
        submenuItem(title: "Zobrazení", [
            formItem("Zvětšit", "zoom-in", "+"),
            formItem("Zmenšit", "zoom-out", "-"),
            formItem("Přizpůsobit šířce okna", "zoom-fit"),
            .separator(),
            formItem("Nápověda k poli", "help", "1", [.command, .option]),
            formItem("Informace o formuláři a certifikátech", "panel", "2", [.command, .option]),
        ])
    }

    private func buildMenu() {
        let main = NSMenu()
        [applicationMenu(), fileMenu(), editMenu(), formMenu(), viewMenu()].forEach(main.addItem)
        NSApp.mainMenu = main
    }
}

// AppKit shows its own open panel at launch for apps with document types; the empty window replaces it.
UserDefaults.standard.register(defaults: ["NSShowAppCentricOpenPanelInsteadOfUntitledFile": false])
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
