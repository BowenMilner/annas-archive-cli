import SwiftUI
import WebKit

@MainActor
final class SearchModel: ObservableObject {
    @Published var books: [Book] = []
    @Published var query = ""
    @Published var author = ""
    @Published var busy = false
    @Published var searched = false
    @Published var error: String?
    @Published var challengedURL: URL?
    @Published var detail: Book?
    @Published var detailLoading = false
    @Published var detailError: String?
    private var task: Task<Void, Never>?
    let browser: CatalogueBrowser
    init(browser: CatalogueBrowser) { self.browser = browser }

    func search(language: String, format: String, mirror: String) {
        guard !busy, !detailLoading, !browser.isDownloading, !browser.browserVisible else { return }
        let title = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        busy = true; error = nil; challengedURL = nil; searched = false
        task = Task {
            defer { busy = false }
            let preferred = UserDefaults.standard.string(forKey: "lastMirror") ?? Catalogue.mirrors[0]
            let origins = mirror.isEmpty ? [preferred] + Catalogue.mirrors.filter { $0 != preferred } : [mirror]
            var latestError: Error?
            for origin in origins {
                do {
                    let url = Catalogue.searchURL(origin: origin, query: title, language: language, format: format)
                    try await browser.load(url)
                    let results = try await browser.extract([Book].self, mode: "search")
                    books = author.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? results
                        : results.filter { Catalogue.authorMatches($0.author, requested: author) }
                    UserDefaults.standard.set(origin, forKey: "lastMirror")
                    challengedURL = nil
                    searched = true
                    return
                } catch is CancellationError { return }
                catch BookfinderError.rateLimited { latestError = BookfinderError.rateLimited; break }
                catch BookfinderError.browserCheck(let url) { challengedURL = url; latestError = BookfinderError.browserCheck(url) }
                catch { latestError = error }
            }
            books = []
            error = latestError?.localizedDescription ?? "No working catalogue mirror was found."
        }
    }
    func inspect(_ selected: Book) {
        guard !busy, !detailLoading, !browser.isDownloading, !browser.browserVisible else { return }
        detail = selected; detailLoading = true; detailError = nil; challengedURL = nil
        task = Task {
            defer { detailLoading = false }
            do {
                try await browser.load(selected.url)
                detail = try await browser.extract(Book.self, mode: "info", md5: selected.md5)
            } catch is CancellationError { return }
            catch BookfinderError.browserCheck(let url) {
                challengedURL = url
                detailError = BookfinderError.browserCheck(url).localizedDescription
            } catch { detailError = error.localizedDescription }
        }
    }
}

@main
@MainActor
struct BookfinderApp: App {
    @StateObject private var library: LibraryStore
    @StateObject private var browser: CatalogueBrowser
    @StateObject private var search: SearchModel

    init() {
        let library = LibraryStore()
        let browser = CatalogueBrowser()
        browser.library = library
        _library = StateObject(wrappedValue: library)
        _browser = StateObject(wrappedValue: browser)
        _search = StateObject(wrappedValue: SearchModel(browser: browser))
    }
    var body: some Scene {
        WindowGroup {
            RootView(search: search, browser: browser, library: library)
        }
    }
}

@MainActor
struct RootView: View {
    @ObservedObject var search: SearchModel
    @ObservedObject var browser: CatalogueBrowser
    @ObservedObject var library: LibraryStore
    @AppStorage("language") private var language = "en"
    @AppStorage("format") private var format = "epub"
    @AppStorage("mirror") private var mirror = ""
    @State private var settings = false

    var body: some View {
        TabView {
            NavigationStack {
                List {
                    Section {
                        TextField("Book title", text: $search.query).submitLabel(.search)
                            .onSubmit { runSearch() }
                            .accessibilityIdentifier("searchTitle")
                        TextField("Author (optional)", text: $search.author).submitLabel(.search)
                            .onSubmit { runSearch() }
                        HStack {
                            Picker("Format", selection: $format) {
                                Text("EPUB").tag("epub"); Text("PDF").tag("pdf"); Text("Any").tag("")
                            }
                            Button(action: runSearch) {
                                Label("Search", systemImage: "magnifyingglass")
                            }.buttonStyle(.borderedProminent)
                                .disabled(search.busy || search.detailLoading || browser.isDownloading || search.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                                .accessibilityIdentifier("searchButton")
                        }
                    }
                    if search.busy { ProgressView("Searching catalogue…") }
                    if let error = search.error {
                        Section {
                            Text(error).foregroundStyle(.secondary)
                            if let url = search.challengedURL {
                                Button("Open browser check") { browser.showVerification(url) }
                                Text("Complete the check, close the browser, then tap Search again.").font(.caption)
                            }
                        }
                    }
                    if search.searched && search.books.isEmpty { Text("No matching editions on this results page.").foregroundStyle(.secondary) }
                    Section(search.books.isEmpty ? "" : "Editions") {
                        ForEach(search.books) { book in
                            Button { search.inspect(book) } label: { BookRow(book: book) }
                                .disabled(search.busy || search.detailLoading || browser.isDownloading)
                        }
                    }
                }
                .navigationTitle("Bookfinder")
                .toolbar { Button { settings = true } label: { Image(systemName: "slider.horizontal.3") }.accessibilityLabel("Settings") }
                .sheet(isPresented: $settings) {
                    NavigationStack {
                        Form {
                            Section("Search defaults") {
                                TextField("Language code, or blank for all", text: $language)
                                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                                Picker("Mirror", selection: $mirror) {
                                    Text("Automatic").tag("")
                                    ForEach(Catalogue.mirrors, id: \.self) { Text($0).tag($0) }
                                }
                            }
                            Section {
                                Text("Browser checks use Bookfinder’s own browser session. Safari and Firefox sessions cannot be imported on iOS.")
                                Text("Books stay in the app’s library. Share a verified file to save it to Files or open it in a reader. Keep the app open during downloads.")
                            }.font(.footnote)
                        }.navigationTitle("Settings")
                            .toolbar { Button("Done") { settings = false } }
                    }
                }
                .sheet(item: $search.detail) { book in
                    DetailView(book: book, search: search, browser: browser)
                }
            }.tabItem { Label("Search", systemImage: "magnifyingglass") }

            NavigationStack {
                List {
                    if let error = library.loadError { Text(error).foregroundStyle(.red) }
                    if library.books.isEmpty {
                        ContentUnavailableView("Your books", systemImage: "books.vertical",
                            description: Text("Verified downloads will appear here."))
                    }
                    ForEach(library.books) { saved in
                        VStack(alignment: .leading, spacing: 10) {
                            BookRow(book: saved.book)
                            ShareLink(item: library.file(for: saved)) { Label("Share book", systemImage: "square.and.arrow.up") }
                        }.padding(.vertical, 4)
                    }
                }.navigationTitle("Library")
            }.tabItem { Label("Library", systemImage: "books.vertical") }
        }
        .sheet(isPresented: Binding(get: { browser.browserVisible && search.detail == nil },
            set: { if !$0 { browser.closeBrowser() } }), onDismiss: { browser.closeBrowser() }) {
            BrowserView(browser: browser)
        }
    }
    private func runSearch() { search.search(language: language, format: format, mirror: mirror) }
}

struct BookRow: View {
    let book: Book
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(book.title).font(.headline).foregroundStyle(.primary)
            if !book.author.isEmpty { Text(book.author).font(.subheadline).foregroundStyle(.secondary) }
            Text([book.format.uppercased(), book.language, book.size].filter { !$0.isEmpty }.joined(separator: " · "))
                .font(.caption).foregroundStyle(.secondary)
        }.padding(.vertical, 4)
    }
}

@MainActor
struct DetailView: View {
    let book: Book
    @ObservedObject var search: SearchModel
    @ObservedObject var browser: CatalogueBrowser
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List {
                Section { BookRow(book: book) }
                if search.detailLoading { ProgressView("Loading edition…") }
                if let error = search.detailError {
                    Section {
                        Text(error)
                        if let url = search.challengedURL { Button("Open browser check") { browser.showVerification(url) } }
                        Button("Retry edition") { search.inspect(book) }.disabled(browser.browserVisible)
                    }
                }
                if !book.publisher.isEmpty { Section("Publisher") { Text(book.publisher) } }
                if !book.description.isEmpty { Section("About this edition") { Text(book.description) } }
                Section("Download sources") {
                    Text("Choose a source and use its download control. Any browser check or queue stays visible. Bookfinder verifies the selected edition’s checksum before saving.")
                        .font(.footnote).foregroundStyle(.secondary)
                    ForEach(book.links.filter { $0.kind != "fast" }) { source in
                        Button { browser.openSource(source, book: book) } label: {
                            Label(source.label, systemImage: "arrow.down.circle")
                        }.disabled(search.detailLoading || browser.isDownloading)
                    }
                    if !search.detailLoading && book.links.filter({ $0.kind != "fast" }).isEmpty {
                        Text("No free HTTP sources listed for this edition.").foregroundStyle(.secondary)
                    }
                }
            }.navigationTitle("Edition").navigationBarTitleDisplayMode(.inline)
                .toolbar { Button("Done") { dismiss() }.disabled(browser.isDownloading) }
                .sheet(isPresented: $browser.browserVisible, onDismiss: { browser.closeBrowser() }) {
                    BrowserView(browser: browser)
                }
        }.interactiveDismissDisabled(search.detailLoading || browser.isDownloading || browser.browserVisible)
    }
}

struct WebView: UIViewRepresentable {
    let webView: WKWebView
    func makeUIView(context: Context) -> WKWebView { webView }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

@MainActor
struct BrowserView: View {
    @ObservedObject var browser: CatalogueBrowser
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if let host = browser.webView.url?.host { Text(host).font(.caption).padding(6) }
                if let status = browser.transferStatus { Text(status).font(.callout).padding() }
                if let error = browser.errorMessage { Text(error).font(.callout).foregroundStyle(.red).padding() }
                if browser.isDownloading {
                    HStack { ProgressView(); Button("Cancel download") { browser.cancelDownload() } }.padding()
                }
                WebView(webView: browser.webView)
            }.navigationTitle(browser.browserTitle).navigationBarTitleDisplayMode(.inline)
                .toolbar { Button("Close") { browser.closeBrowser() }.disabled(browser.isDownloading) }
        }.interactiveDismissDisabled(browser.isDownloading)
    }
}
