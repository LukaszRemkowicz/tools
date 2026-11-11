// PathBar: shows the current path under macOS Open/Save dialogs. Click the bar to copy the path.
import AppKit
import ApplicationServices
import ServiceManagement

// MARK: - Path choice (pure, self-tested)

/// Selected item wins (like Finder's path bar); otherwise the folder holding the shallowest listed item.
/// shortcut: in column view without a selection the shallowest item is in the first column, so the bar
/// shows that column's folder; read the column/popup structure if this bites.
func pickPath(urls: [URL], selected: [URL]) -> String? {
    if let s = selected.max(by: { $0.pathComponents.count < $1.pathComponents.count }) { return s.path }
    // Drop ancestors of other URLs: the ⌥ path bar lists "/", "/Users", … which would otherwise win as shallowest.
    var ancestors = Set<String>()
    for u in urls {
        var p = u.deletingLastPathComponent()
        while p.path != "/" && p.path != "/.." && ancestors.insert(p.path).inserted { p = p.deletingLastPathComponent() }
        ancestors.insert("/")
    }
    let items = urls.filter { !ancestors.contains($0.path) }
    guard let top = items.min(by: { $0.pathComponents.count < $1.pathComponents.count }) else { return nil }
    return top.deletingLastPathComponent().path
}

func selfTest() {
    let a = URL(fileURLWithPath: "/Users/x/proj/a.tif"), b = URL(fileURLWithPath: "/Users/x/proj/sub/b.tif")
    precondition(pickPath(urls: [b, a], selected: []) == "/Users/x/proj")
    precondition(pickPath(urls: [a, b], selected: [b]) == "/Users/x/proj/sub/b.tif")
    precondition(pickPath(urls: [], selected: []) == nil)
    // ⌥ held: the native path bar adds every ancestor of the current folder.
    let bar = ["/", "/Users", "/Users/x", "/Users/x/proj"].map { URL(fileURLWithPath: $0) }
    precondition(pickPath(urls: bar + [a, b], selected: []) == "/Users/x/proj")
    print("selftest ok")
}

// MARK: - Accessibility

func attr(_ e: AXUIElement, _ a: String) -> AnyObject? {
    var v: AnyObject?
    return AXUIElementCopyAttributeValue(e, a as CFString, &v) == .success ? v : nil
}

func isFileDialog(_ e: AXUIElement) -> Bool {
    let id = attr(e, "AXIdentifier") as? String ?? ""
    return id == "open-panel" || id == "save-panel"
}

/// Open/Save dialog of the frontmost app: a standalone window or a sheet on one.
func findDialog(pid: pid_t) -> AXUIElement? {
    let app = AXUIElementCreateApplication(pid)
    for w in attr(app, "AXWindows") as? [AXUIElement] ?? [] {
        if isFileDialog(w) { return w }
        for c in attr(w, "AXChildren") as? [AXUIElement] ?? [] where isFileDialog(c) { return c }
    }
    return nil
}

/// Collect file URLs in the dialog; rows marked selected also go to `selected`.
func collect(_ e: AXUIElement, _ depth: Int, _ urls: inout [URL], _ selected: inout [URL], selectedRow: Bool = false) {
    if depth > 25 || urls.count > 400 { return }  // shortcut: caps the walk for huge folders
    let sel = selectedRow || (attr(e, "AXSelected") as? Bool ?? false)
    if let u = attr(e, "AXURL") as? URL, u.isFileURL {
        urls.append(u)
        if sel { selected.append(u) }
    }
    for k in attr(e, "AXChildren") as? [AXUIElement] ?? [] {
        collect(k, depth + 1, &urls, &selected, selectedRow: sel)
    }
}

func frame(of e: AXUIElement) -> CGRect? {
    guard let p = attr(e, "AXPosition"), let s = attr(e, "AXSize") else { return nil }
    var pt = CGPoint.zero, sz = CGSize.zero
    AXValueGetValue(p as! AXValue, .cgPoint, &pt)
    AXValueGetValue(s as! AXValue, .cgSize, &sz)
    return CGRect(origin: pt, size: sz)
}

// MARK: - Bar window

final class Bar: NSPanel {
    let label = NSTextField(labelWithString: "")
    var path = ""

    init() {
        super.init(contentRect: .zero, styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: true)
        level = .modalPanel + 1
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        let fx = NSVisualEffectView()
        fx.material = .hudWindow
        fx.state = .active
        fx.wantsLayer = true
        fx.layer?.cornerRadius = 8
        label.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        label.lineBreakMode = .byTruncatingHead
        label.translatesAutoresizingMaskIntoConstraints = false
        fx.addSubview(label)
        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: fx.leadingAnchor, constant: 10),
            label.trailingAnchor.constraint(equalTo: fx.trailingAnchor, constant: -10),
            label.centerYAnchor.constraint(equalTo: fx.centerYAnchor),
        ])
        contentView = fx
        fx.addGestureRecognizer(NSClickGestureRecognizer(target: self, action: #selector(copyPath)))
        fx.setAccessibilityLabel("Path bar, click to copy")
    }

    @objc func copyPath() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(path, forType: .string)
        label.stringValue = "Copied: " + path
    }

    /// `axFrame` is in AX coordinates (top-left origin of the main screen).
    func show(path: String, under axFrame: CGRect) {
        if path != self.path { self.path = path; label.stringValue = path }
        place(under: axFrame)
        orderFrontRegardless()
    }

    /// Glue the bar flush to the dialog's bottom edge (or top edge when there is no room below).
    func place(under axFrame: CGRect) {
        let mainH = NSScreen.screens.first?.frame.height ?? 0
        let h: CGFloat = 26
        var y = mainH - axFrame.maxY - h
        let screen = NSScreen.screens.first { $0.frame.contains(CGPoint(x: axFrame.midX, y: mainH - axFrame.midY)) }
        if let vis = screen?.visibleFrame, y < vis.minY { y = mainH - axFrame.minY }
        let target = CGRect(x: axFrame.minX, y: y, width: axFrame.width, height: h)
        if target != frame { setFrame(target, display: true) }
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    let bar = Bar()
    var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ n: Notification) {
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        if !AXIsProcessTrustedWithOptions(opts) {
            NSLog("PathBar: no Accessibility permission yet")
        }
        // Open at login once; after that the user's choice in Login Items wins.
        if !UserDefaults.standard.bool(forKey: "loginItemRegistered") {
            do {
                try SMAppService.mainApp.register()
                UserDefaults.standard.set(true, forKey: "loginItemRegistered")
            } catch { NSLog("PathBar: login item registration failed: \(error)") }
        }
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "⌥/"
        let menu = NSMenu()
        menu.addItem(withTitle: "Quit PathBar", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.menu = menu
        statusItem = item
        // shortcut: dialog/path lookup polls every 0.4 s; switch to AXObserver notifications if CPU use shows up.
        Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] _ in self?.tick() }
    }

    var dialog: AXUIElement?
    var ownerPid: pid_t?
    var follow: Timer?

    func tick() {
        let trusted = AXIsProcessTrusted()
        statusItem?.button?.title = trusted ? "⌥/" : "⌥/ ⚠︎"  // ⚠︎ = Accessibility permission missing
        guard trusted, let front = NSWorkspace.shared.frontmostApplication else { hide(); return }
        // ⇧⌘4 and other menu-bar tools take focus without being a real app switch: keep the last dialog's owner.
        let pid = front.activationPolicy == .regular ? front.processIdentifier : (ownerPid ?? front.processIdentifier)
        guard let dlg = findDialog(pid: pid), let f = frame(of: dlg) else { hide(); return }
        ownerPid = pid
        var urls: [URL] = [], selected: [URL] = []
        collect(dlg, 0, &urls, &selected)
        guard let path = pickPath(urls: urls, selected: selected) else { hide(); return }
        dialog = dlg
        bar.show(path: path, under: f)
        // Cheap frame-only reads at 60 Hz keep the bar glued while the dialog is dragged or resized.
        if follow == nil {
            follow = Timer.scheduledTimer(withTimeInterval: 1.0 / 60, repeats: true) { [weak self] _ in
                guard let self, let d = self.dialog, let f = frame(of: d) else { return }
                self.bar.place(under: f)
            }
        }
    }

    func hide() {
        follow?.invalidate()
        follow = nil
        dialog = nil
        bar.orderOut(nil)
    }
}

if CommandLine.arguments.contains("--selftest") { selfTest(); exit(0) }
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
