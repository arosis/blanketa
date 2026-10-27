// "ZFO na Macu": opens .zfo forms in a window. Each window runs the bundled zfo-server
// (Python, see app.py) for its file and shows it in a WKWebView.
import AppKit
import PDFKit
import WebKit
import UniformTypeIdentifiers

final class FormWindow: NSObject, NSWindowDelegate, WKScriptMessageHandler, WKUIDelegate {
    let window: NSWindow
    let webView: WKWebView
    let server = Process()
    let file: URL?
    var onClose: (() -> Void)?
    var onOpen: ((URL?) -> Void)?

    // Without a file the window shows the application with only "Otevřít" and the recent files.
    init(file: URL?) {
        self.file = file
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: config)
        super.init()
        config.userContentController.add(self, name: "app")
        webView.uiDelegate = self
        // Swift owns the window; AppKit's own release on close would free it a second time.
        window.isReleasedWhenClosed = false
        window.title = file?.lastPathComponent ?? "ZFO na Macu"
        window.representedURL = file
        window.contentView = webView
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("FormWindow")
        if file != nil {
            webView.loadHTMLString("<p style='font:14px -apple-system;padding:24px'>Otevírám formulář…</p>", baseURL: nil)
        }
        window.makeKeyAndOrderFront(nil)
        start(file: file)
    }

    private func start(file: URL?) {
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
                self?.webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))
            }
        }
        do {
            try server.run()
        } catch {
            webView.loadHTMLString("<p style='font:14px -apple-system;padding:24px'>Nelze spustit: \(error)</p>", baseURL: nil)
        }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any] else { return }
        switch body["action"] as? String {
        case "open": onOpen?(nil)
        case "openFile":
            if let path = body["path"] as? String { onOpen?(URL(fileURLWithPath: path)) }
        case "recent":
            let paths = RecentFiles.urls.map(\.path)
            let json = String(data: try! JSONEncoder().encode(paths), encoding: .utf8)!
            webView.evaluateJavaScript("window.zfoRecent && zfoRecent(\(json))")
        case "print": print()
        case "printPdf":
            if let path = body["path"] as? String { printPdf(URL(fileURLWithPath: path)) }
        case "reveal":
            if let path = body["path"] as? String {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
            }
        case "saveFile":
            if let name = body["name"] as? String, let content = body["content"] as? String {
                saveFile(name: name, folder: body["folder"] as? String, content: content)
            }
        default: break
        }
    }

    func perform(_ action: String) {
        webView.evaluateJavaScript("window.zfoAction && zfoAction('\(action)')")
    }

    // Form Filler's "Uložit data do souboru" asks where to put the XML.
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

    // The system print panel also offers "Save as PDF", which stands in for Form Filler's PDF export.
    private func print() {
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        info.topMargin = 0; info.bottomMargin = 0; info.leftMargin = 0; info.rightMargin = 0
        info.horizontalPagination = .fit
        info.isVerticallyCentered = false
        info.jobDisposition = .spool
        let operation = webView.printOperation(with: info)
        operation.view?.frame = webView.bounds
        operation.jobTitle = window.title
        operation.runModal(for: window, delegate: self, didRun: #selector(printDone(_:success:contextInfo:)), contextInfo: nil)
    }

    // PDF documents print from the filled file itself, at full quality, rather than from the page on screen.
    private func printPdf(_ url: URL) {
        guard let document = PDFDocument(url: url) else { return }
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        info.jobDisposition = .spool
        guard let operation = document.printOperation(for: info, scalingMode: .pageScaleToFit, autoRotate: true) else { return }
        operation.jobTitle = window.title
        operation.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
    }

    @objc private func printDone(_ operation: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        webView.evaluateJavaScript("window.zfoPrinted && zfoPrinted()")
    }

    // File inputs in the page (e.g. the form's "Import dat do formuláře") use the system open panel.
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.xml]
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
        DispatchQueue.main.async { if !self.openedFile { self.openEmpty() } }
    }

    // AppKit would otherwise show its own open panel at launch; the empty window replaces it.
    func applicationShouldOpenUntitledFile(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { openEmpty() }
        return true
    }

    func openEmpty() { add(FormWindow(file: nil)) }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        windows.forEach { if $0.server.isRunning { $0.server.terminate() } }
    }

    func open(_ url: URL) {
        let empty = windows.filter { $0.file == nil }
        add(FormWindow(file: url))
        RecentFiles.add(url)
        NSDocumentController.shared.noteNewRecentDocumentURL(url)
        // The request may come from the empty window's own page, so it closes once that call has returned.
        DispatchQueue.main.async { empty.forEach { $0.window.close() } }
    }

    private func add(_ form: FormWindow) {
        form.onClose = { [weak self, weak form] in self?.windows.removeAll { $0 === form } }
        form.onOpen = { [weak self] url in
            if let url { self?.open(url) } else { self?.openDocument(nil) }
        }
        windows.append(form)
    }

    @objc func openDocument(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "zfo") ?? .data, .pdf]
        panel.allowsMultipleSelection = true
        if panel.runModal() == .OK {
            panel.urls.forEach(open)
        }
    }

    // Menu commands that live in the page (save, print, check…) go to the active form window.
    @objc func formAction(_ sender: NSMenuItem) {
        guard let action = sender.representedObject as? String,
              let form = windows.first(where: { $0.window.isKeyWindow }) ?? windows.last else { return }
        form.perform(action)
    }

    private func formItem(_ title: String, _ action: String, _ key: String = "", _ modifiers: NSEvent.ModifierFlags = .command) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: #selector(formAction(_:)), keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        item.representedObject = action
        return item
    }

    private func buildMenu() {
        let f9 = String(Character(UnicodeScalar(NSF9FunctionKey)!))
        let main = NSMenu()
        let appItem = NSMenuItem()
        appItem.submenu = NSMenu()
        appItem.submenu!.addItem(withTitle: "Ukončit ZFO na Macu", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let fileItem = NSMenuItem()
        fileItem.submenu = NSMenu(title: "Soubor")
        fileItem.submenu!.addItem(withTitle: "Otevřít…", action: #selector(openDocument(_:)), keyEquivalent: "o")
        fileItem.submenu!.addItem(.separator())
        fileItem.submenu!.addItem(formItem("Uložit bez podpisu", "save-plain", "s"))
        fileItem.submenu!.addItem(formItem("Podepsat a uložit…", "save", "s", [.command, .shift]))
        fileItem.submenu!.addItem(.separator())
        fileItem.submenu!.addItem(formItem("Tisk…", "print", "p"))
        fileItem.submenu!.addItem(.separator())
        fileItem.submenu!.addItem(withTitle: "Zavřít", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        let formMenuItem = NSMenuItem()
        formMenuItem.submenu = NSMenu(title: "Formulář")
        formMenuItem.submenu!.addItem(formItem("Přepočítat hodnoty", "recalc", f9, []))
        formMenuItem.submenu!.addItem(formItem("Zkontrolovat formulář", "check", f9, .shift))
        formMenuItem.submenu!.addItem(.separator())
        formMenuItem.submenu!.addItem(formItem("Obnovit výchozí hodnotu pole", "reset-field",
                                               String(Character(UnicodeScalar(NSBackspaceCharacter)!)), [.command, .shift]))
        formMenuItem.submenu!.addItem(formItem("Obnovit výchozí hodnoty v celém formuláři…", "reset"))
        formMenuItem.submenu!.addItem(.separator())
        formMenuItem.submenu!.addItem(formItem("Import dat do formuláře…", "import-data"))
        formMenuItem.submenu!.addItem(formItem("Uložit data do souboru…", "export-data"))
        let viewItem = NSMenuItem()
        viewItem.submenu = NSMenu(title: "Zobrazení")
        viewItem.submenu!.addItem(formItem("Zvětšit", "zoom-in", "+"))
        viewItem.submenu!.addItem(formItem("Zmenšit", "zoom-out", "-"))
        viewItem.submenu!.addItem(formItem("Přizpůsobit šířce okna", "zoom-fit"))
        viewItem.submenu!.addItem(.separator())
        viewItem.submenu!.addItem(formItem("Nápověda k poli", "help", "1", [.command, .option]))
        viewItem.submenu!.addItem(formItem("Informace o formuláři a certifikátech", "panel", "2", [.command, .option]))
        let editItem = NSMenuItem()
        editItem.submenu = NSMenu(title: "Úpravy")
        editItem.submenu!.addItem(withTitle: "Zpět", action: Selector(("undo:")), keyEquivalent: "z")
        editItem.submenu!.addItem(withTitle: "Vyjmout", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editItem.submenu!.addItem(withTitle: "Kopírovat", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editItem.submenu!.addItem(withTitle: "Vložit", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editItem.submenu!.addItem(withTitle: "Vybrat vše", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu!.addItem(.separator())
        editItem.submenu!.addItem(formItem("Najít…", "find", "f"))
        editItem.submenu!.addItem(formItem("Najít další", "find-next", "g"))
        editItem.submenu!.addItem(formItem("Najít předchozí", "find-previous", "g", [.command, .shift]))
        [appItem, fileItem, editItem, formMenuItem, viewItem].forEach(main.addItem)
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
