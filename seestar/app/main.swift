// seestar_download: small AppKit front end for the bundled seestar_download.py.
import AppKit

// MARK: - Pure logic (self-tested)

/// Keep-stacks only makes sense together with delete (the script refuses it otherwise), so it is dropped then.
func downloadFlags(delete: Bool, keepStacks: Bool) -> [String] {
    guard delete else { return [] }
    return keepStacks ? ["--delete", "--keep-stacks"] : ["--delete"]
}

let scriptURL = Bundle.main.resourceURL?.appendingPathComponent("seestar_download.py")
let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"

func selfTest() {
    precondition(scriptURL.map { FileManager.default.fileExists(atPath: $0.path) } ?? false, "bundled script missing")
    let script = (try? String(contentsOf: scriptURL!, encoding: .utf8)) ?? ""
    precondition(script.contains("__version__ = '\(appVersion)'"), "app and bundled script versions differ")
    precondition(downloadFlags(delete: true, keepStacks: true) == ["--delete", "--keep-stacks"])
    precondition(downloadFlags(delete: true, keepStacks: false) == ["--delete"])
    precondition(downloadFlags(delete: false, keepStacks: false) == [])
    precondition(downloadFlags(delete: false, keepStacks: true) == [])
    print("selftest ok, version \(appVersion)")
}

/// First python3 that is 3.9 or newer (the CLI needs it).
func findPython() -> String? {
    let home = NSHomeDirectory()
    for p in [home + "/.pyenv/shims/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
    where FileManager.default.isExecutableFile(atPath: p) {
        let t = Process()
        t.executableURL = URL(fileURLWithPath: p)
        t.arguments = ["-c", "import sys; sys.exit(sys.version_info < (3, 9))"]
        t.standardOutput = FileHandle.nullDevice
        t.standardError = FileHandle.nullDevice
        if (try? t.run()) != nil { t.waitUntilExit(); if t.terminationStatus == 0 { return p } }
    }
    return nil
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 560),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
    let deleteBox = NSButton(checkboxWithTitle: "Delete from the scope after download", target: nil, action: nil)
    let keepBox = NSButton(checkboxWithTitle: "Keep stacks on the scope (for GoTo from the album)", target: nil, action: nil)
    let status = NSTextField(wrappingLabelWithString: "Press Start to download the photos.")
    let progress = NSProgressIndicator()
    let fileLabel = NSTextField(labelWithString: "")
    let logView = NSTextView()
    let startButton = NSButton(title: "Start", target: nil, action: nil)
    var process: Process?
    var userStopped = false
    var pending = Data()
    var replaceNext = false   // a "\r" was seen: the next text overwrites the current log line
    var lineStart = 0         // offset of the current (last) log line in the text storage
    // explicit attributes: plain inserted text gets black Helvetica, unreadable in dark mode
    let logStyle: [NSAttributedString.Key: Any] = [.font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
                                                   .foregroundColor: NSColor.textColor]

    func applicationDidFinishLaunching(_ n: Notification) {
        buildMenu()
        let title = NSTextField(labelWithString: "Downloading photos from the Seestar")
        title.font = .boldSystemFont(ofSize: 16)
        let dest = NSTextField(labelWithString: "Saving to Pictures › Seestar")
        deleteBox.target = self; deleteBox.action = #selector(deleteToggled)
        keepBox.isEnabled = false
        progress.isIndeterminate = true
        progress.style = .bar
        logView.isEditable = false
        logView.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        logView.autoresizingMask = [.width]
        let scroll = NSScrollView()
        scroll.documentView = logView
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 200).isActive = true
        startButton.target = self; startButton.action = #selector(startStop)
        startButton.keyEquivalent = "\r"
        let open = NSButton(title: "Open photos folder", target: self, action: #selector(openFolder))
        let buttons = NSStackView(views: [startButton, open])
        buttons.spacing = 12
        let stack = NSStackView(views: [title, dest, deleteBox, keepBox, status, progress, fileLabel, scroll, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 8
        stack.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        stack.setCustomSpacing(16, after: dest)
        for v in [status, progress, scroll] { v.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -32).isActive = true }
        window.contentView = stack
        window.title = "Seestar download \(appVersion)"
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ s: NSApplication) -> NSApplication.TerminateReply {
        if let p = process, p.isRunning {
            p.interrupt()
            let end = Date().addingTimeInterval(3)
            while p.isRunning && Date() < end { usleep(50_000) }
        }
        return .terminateNow
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        appMenu.addItem(withTitle: "Quit seestar_download", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit"); editItem.submenu = edit
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        NSApp.mainMenu = main
    }

    @objc func deleteToggled() {
        if deleteBox.state == .off { keepBox.state = .off }
        keepBox.isEnabled = deleteBox.state == .on
    }

    @objc func openFolder() {
        let dir = URL(fileURLWithPath: NSHomeDirectory() + "/Pictures/Seestar")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(dir)
    }

    @objc func startStop() {
        if let p = process, p.isRunning { confirmStop(p) } else { start() }
    }

    func confirmStop(_ p: Process) {
        let a = NSAlert()
        a.messageText = "Stop the download?"
        a.informativeText = "Files already downloaded stay. Next time it continues where it stopped."
        a.addButton(withTitle: "Stop"); a.addButton(withTitle: "Keep downloading")
        guard a.runModal() == .alertFirstButtonReturn, p.isRunning else { return }
        userStopped = true
        p.interrupt()
    }

    func start() {
        guard let script = scriptURL, FileManager.default.fileExists(atPath: script.path) else {
            status.stringValue = "The download script is missing from the app. Reinstall the app."; return
        }
        let flags = downloadFlags(delete: deleteBox.state == .on, keepStacks: keepBox.state == .on)
        if flags.contains("--delete") {
            let a = NSAlert()
            a.messageText = "Delete photos from the scope?"
            a.informativeText = (flags.contains("--keep-stacks")
                ? "Each single frame (sub) is deleted from the Seestar once its copy on this Mac is complete. Stacks stay on the scope."
                : "Each photo is deleted from the Seestar once its copy on this Mac is complete.") + " This cannot be undone."
            a.addButton(withTitle: "Download and delete"); a.addButton(withTitle: "Cancel")
            if a.runModal() != .alertFirstButtonReturn { return }
        }
        setRunning(true)
        status.stringValue = "Connecting to the Seestar app…"
        DispatchQueue.global().async {
            let py = findPython()
            DispatchQueue.main.async {
                guard let py = py else {
                    self.status.stringValue = "Python 3.9 or newer is not installed. Run xcode-select --install in Terminal, or install Python from python.org."
                    self.setRunning(false); return
                }
                self.launch(py, script.path, flags)
            }
        }
    }

    func launch(_ python: String, _ script: String, _ flags: [String]) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-u", script] + flags
        p.environment = ProcessInfo.processInfo.environment.merging(["PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"]) { $1 }
        let pipe = Pipe()
        p.standardOutput = pipe; p.standardError = pipe
        userStopped = false; pending = Data(); replaceNext = false
        logView.string = ""; lineStart = 0
        progress.isIndeterminate = true; progress.startAnimation(nil); fileLabel.stringValue = ""
        pipe.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil; return }
            DispatchQueue.main.async { self.feed(d) }
        }
        p.terminationHandler = { p in
            DispatchQueue.main.async {
                pipe.fileHandleForReading.readabilityHandler = nil
                self.feed(pipe.fileHandleForReading.readDataToEndOfFile())
                self.flushPending()
                self.finished(p.terminationStatus)
            }
        }
        do { try p.run(); process = p } catch {
            status.stringValue = "Could not start Python: \(error.localizedDescription)"
            setRunning(false)
        }
    }

    func setRunning(_ on: Bool) {
        startButton.title = on ? "Stop" : "Start"
        deleteBox.isEnabled = !on
        keepBox.isEnabled = !on && deleteBox.state == .on
    }

    func finished(_ code: Int32) {
        process = nil
        progress.stopAnimation(nil)
        setRunning(false)
        switch code {
        case 0 where !userStopped: status.stringValue = "All photos are downloaded."; progress.isIndeterminate = false; progress.doubleValue = progress.maxValue
        case 130: status.stringValue = "Stopped. Press Start again to continue where it stopped."
        default:
            status.stringValue = userStopped ? "Stopped. Press Start again to continue where it stopped."
                : "The download stopped with an error. See the details below, then press Start again."
        }
    }

    // MARK: log

    /// Split on "\n" and "\r" (bytes, so a split UTF-8 character stays in `pending` until its delimiter arrives).
    func feed(_ d: Data) {
        pending.append(d)
        while let i = pending.firstIndex(where: { $0 == 10 || $0 == 13 }) {
            let seg = String(decoding: pending[pending.startIndex..<i], as: UTF8.self)
            let nl = pending[i] == 10
            pending.removeSubrange(pending.startIndex...i)
            emit(seg, newline: nl)
        }
    }

    func flushPending() {
        if !pending.isEmpty { emit(String(decoding: pending, as: UTF8.self), newline: true); pending = Data() }
    }

    func emit(_ text: String, newline: Bool) {
        let st = logView.textStorage!
        if !text.isEmpty {
            // "\r" rewrites replace the current line; plain text continues it.
            let range = replaceNext ? NSRange(location: lineStart, length: st.length - lineStart) : NSRange(location: st.length, length: 0)
            st.replaceCharacters(in: range, with: NSAttributedString(string: text, attributes: logStyle))
            replaceNext = false
            parse(text.trimmingCharacters(in: .whitespaces))
        }
        if newline { st.append(NSAttributedString(string: "\n", attributes: logStyle)); lineStart = st.length; replaceNext = false }
        else { replaceNext = true }
        logView.scrollToEndOfDocument(nil)
    }

    func parse(_ line: String) {
        if line.hasPrefix("Seestar app is not connected") {
            status.stringValue = "Waiting for the Seestar app. Open it and connect to the telescope, the download then starts by itself."
        } else if line.hasPrefix("Seestar app is connected again") {
            status.stringValue = "Connected again, continuing"
        } else if let m = line.range(of: #"^\[(\d+)/(\d+)\] (.*)$"#, options: .regularExpression) {
            let parts = line[m].dropFirst().split(separator: "]", maxSplits: 1)
            let nums = parts[0].split(separator: "/").compactMap { Double($0) }
            guard nums.count == 2 else { return }
            progress.isIndeterminate = false
            progress.maxValue = nums[1]; progress.doubleValue = nums[0] - 1
            fileLabel.stringValue = "Downloading file \(Int(nums[0])) of \(Int(nums[1])):" + parts[1]
            status.stringValue = "Downloading…"
        }
    }
}

if CommandLine.arguments.contains("--selftest") { selfTest(); exit(0) }
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
