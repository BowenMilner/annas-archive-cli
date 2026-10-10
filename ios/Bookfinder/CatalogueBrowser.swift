import Combine
import Foundation
import WebKit

/// Catalogue requests and human verification use one persistent WebKit session.
/// Safari cookies are not accessible to an iOS app; no desktop cookie import is attempted.
@MainActor
final class CatalogueBrowser: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    let webView: WKWebView
    @Published var browserVisible = false
    @Published var browserTitle = "Browser check"
    @Published var transferStatus: String?
    @Published var errorMessage: String?
    @Published var isDownloading = false
    var library: LibraryStore?
    private var continuation: CheckedContinuation<Void, Error>?
    private var navigation: WKNavigation?
    private var requestID: UUID?
    private var responseError: Error?
    private var timeout: Task<Void, Never>?
    private var selectedBook: Book?
    private var download: WKDownload?
    private var stagingURL: URL?
    private var verification: Task<Void, Never>?

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
    }

    func extract<T: Decodable>(_ type: T.Type, mode: String, md5: String = "") async throws -> T {
        guard let path = Bundle.main.url(forResource: "catalogue", withExtension: "js") else {
            throw BookfinderError.message("The catalogue adapter is missing from this build.")
        }
        let source = try String(contentsOf: path, encoding: .utf8)
        _ = try await webView.evaluateJavaScript(source)
        // Encode arguments as JSON instead of interpolating catalogue values into executable JS.
        let arguments = String(decoding: try JSONEncoder().encode([mode, md5]), as: UTF8.self)
        let result = try await webView.evaluateJavaScript("window.bookfinder.extract(...\(arguments))")
        guard let json = result as? String, let data = json.data(using: .utf8) else {
            throw BookfinderError.message("The catalogue did not return edition data.")
        }
        let envelope = try JSONDecoder().decode(CatalogueEnvelope<T>.self, from: data)
        if envelope.error == "browser_check", let url = webView.url { throw BookfinderError.browserCheck(url) }
        guard let value = envelope.value else {
            throw BookfinderError.message("The site layout was not recognised. Try another mirror or open the browser.")
        }
        return value
    }

    func load(_ url: URL) async throws {
        guard continuation == nil, !isDownloading, selectedBook == nil else {
            throw BookfinderError.message("Close the source browser or finish the current request first.")
        }
        let id = UUID()
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (pending: CheckedContinuation<Void, Error>) in
                continuation = pending
                requestID = id
                responseError = nil
                navigation = webView.load(URLRequest(url: url, timeoutInterval: 40))
                timeout = Task { [weak self] in
                    do { try await Task.sleep(nanoseconds: 45_000_000_000) } catch { return }
                    guard let self, self.requestID == id else { return }
                    self.webView.stopLoading()
                    self.finish(BookfinderError.message("The site did not respond in time. Try again later."))
                }
            }
        }, onCancel: {
            Task { @MainActor [weak self] in
                guard let self, self.requestID == id else { return }
                self.webView.stopLoading()
                self.finish(CancellationError())
            }
        })
    }

    private func finish(_ error: Error? = nil) {
        let pending = continuation
        continuation = nil
        requestID = nil
        navigation = nil
        timeout?.cancel()
        timeout = nil
        if let error { pending?.resume(throwing: error) } else { pending?.resume() }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if navigation === self.navigation { finish(responseError) }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if navigation === self.navigation { finish(responseError ?? error) }
        else if selectedBook != nil { errorMessage = error.localizedDescription }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        self.webView(webView, didFail: navigation, withError: error)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if continuation != nil { finish(BookfinderError.message("The browser stopped. Retry the request.")) }
        else { errorMessage = "The browser stopped. Close and reopen the source." }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let scheme = action.request.url?.scheme?.lowercased(), ["https", "http", "about"].contains(scheme) else {
            decisionHandler(.cancel)
            return
        }
        // Ignore pop-up windows; source links with target=_blank stay in this same session.
        if action.targetFrame == nil {
            if !isDownloading { webView.load(action.request) }
            decisionHandler(.cancel)
            return
        }
        decisionHandler(action.shouldPerformDownload && selectedBook != nil && !isDownloading ? .download : .allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if response.isForMainFrame, let http = response.response as? HTTPURLResponse {
            let code = http.statusCode
            if code == 429 {
                let error = BookfinderError.rateLimited
                responseError = error
                errorMessage = error.localizedDescription
                decisionHandler(.cancel)
                if continuation != nil { finish(error) }
                return
            }
            if code == 403 {
                responseError = BookfinderError.browserCheck(http.url ?? webView.url!)
            } else if code >= 400 {
                responseError = BookfinderError.message("The source returned HTTP \(code). Try again later.")
                if continuation == nil { errorMessage = responseError?.localizedDescription }
            }
            if code >= 400 && !response.canShowMIMEType {
                decisionHandler(.cancel)
                if continuation != nil { finish(responseError) }
                return
            }
        }
        let attachment = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?
            .lowercased().hasPrefix("attachment") == true
        let mime = response.response.mimeType?.lowercased() ?? ""
        let bookContent = ["application/epub+zip", "application/pdf", "application/octet-stream"].contains(mime)
        if response.isForMainFrame && (attachment || bookContent || !response.canShowMIMEType) {
            guard selectedBook != nil, !isDownloading else {
                decisionHandler(.cancel)
                if continuation != nil { finish(BookfinderError.message("The source returned a file instead of catalogue data.")) }
                return
            }
            decisionHandler(.download)
        } else { decisionHandler(.allow) }
    }

    func showVerification(_ url: URL) {
        guard !isDownloading, continuation == nil else { return }
        selectedBook = nil
        browserTitle = "Browser check"
        webView.load(URLRequest(url: url))
        browserVisible = true
    }
    func openSource(_ source: SourceLink, book: Book) {
        guard !isDownloading, continuation == nil else { return }
        selectedBook = book
        errorMessage = nil
        transferStatus = nil
        browserTitle = source.label
        webView.load(URLRequest(url: source.url))
        browserVisible = true
    }
    func closeBrowser() {
        guard !isDownloading else { return }
        webView.stopLoading()
        selectedBook = nil
        browserVisible = false
    }
    func cancelDownload() {
        if let verification {
            verification.cancel()
            transferStatus = "Cancelling…"
            return
        }
        download?.cancel { _ in }
        download = nil
        if let stagingURL { try? FileManager.default.removeItem(at: stagingURL) }
        stagingURL = nil
        transferStatus = "Cancelled. No file was saved."
        isDownloading = false
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        begin(download)
    }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        begin(download)
    }
    private func begin(_ incoming: WKDownload) {
        guard selectedBook != nil, !isDownloading else { incoming.cancel { _ in }; return }
        download = incoming
        incoming.delegate = self
        isDownloading = true
        transferStatus = "Downloading… Keep Bookfinder open."
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        guard download === self.download, selectedBook != nil else { completionHandler(nil); return }
        // Ignore untrusted Content-Disposition filenames, and never replace existing files.
        let destination = FileManager.default.temporaryDirectory.appendingPathComponent("bookfinder-\(UUID().uuidString).partial")
        stagingURL = destination
        completionHandler(destination)
    }
    func downloadDidFinish(_ download: WKDownload) {
        guard download === self.download, let file = stagingURL, let book = selectedBook, let library else { return }
        self.download = nil
        transferStatus = "Checking the edition’s checksum…"
        verification = Task {
            do {
                try await Task.detached(priority: .userInitiated) { try FileVerifier.validate(file, book: book) }.value
                try Task.checkCancellation()
                try library.addVerified(file, book: book)
                transferStatus = "Saved to Library. Use Share to open it in your reader or save to Files."
            } catch is CancellationError {
                try? FileManager.default.removeItem(at: file)
            } catch {
                try? FileManager.default.removeItem(at: file)
                errorMessage = error.localizedDescription
                transferStatus = "Nothing was saved."
            }
            stagingURL = nil
            isDownloading = false
            verification = nil
        }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        guard download === self.download else { return }
        if let stagingURL { try? FileManager.default.removeItem(at: stagingURL) }
        stagingURL = nil
        self.download = nil
        isDownloading = false
        errorMessage = error.localizedDescription
        transferStatus = "Download failed. No file was saved."
    }
}
