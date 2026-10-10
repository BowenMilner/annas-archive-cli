import CryptoKit
import WebKit
import XCTest
@testable import Bookfinder

@MainActor
final class BookfinderTests: XCTestCase, WKNavigationDelegate {
    private var loaded: CheckedContinuation<Void, Error>?

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loaded?.resume(); loaded = nil
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        loaded?.resume(throwing: error); loaded = nil
    }

    private func parse<T: Decodable>(_ type: T.Type, fixture: String, mode: String, md5: String = "") async throws -> T {
        let fixtures = Bundle(for: Self.self).url(forResource: fixture, withExtension: "html", subdirectory: "Fixtures")!
        let html = try String(contentsOf: fixtures, encoding: .utf8)
        return try await parse(type, html: html, mode: mode, md5: md5)
    }

    private func parse<T: Decodable>(_ type: T.Type, html: String, mode: String, md5: String = "") async throws -> T {
        let browser = CatalogueBrowser()
        browser.webView.navigationDelegate = self
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            loaded = continuation
            // Fixtures exercise DOM extraction only: block cover images and every remote request.
            let policy = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-eval' 'unsafe-inline'; style-src 'unsafe-inline'\">"
            browser.webView.loadHTMLString(policy + html, baseURL: URL(string: "https://annas-archive.gd"))
        }
        return try await browser.extract(type, mode: mode, md5: md5)
    }

    func testLegacySearchIncludesCommentCardsAndDeduplicates() async throws {
        let books = try await parse([Book].self, fixture: "search", mode: "search")
        XCTAssertEqual(books.count, 2)
        XCTAssertEqual(books[0].title, "Pride & Prejudice")
        XCTAssertEqual(books[0].author, "Jane Austen")
        XCTAssertEqual(books[0].format, "epub")
        XCTAssertEqual(books[1].language, "zh")
        XCTAssertEqual(books[1].title, "傲慢与偏见")
    }

    func testCurrentSearchDoesNotUseCoverAsTitle() async throws {
        let books = try await parse([Book].self, fixture: "search-current", mode: "search")
        XCTAssertEqual(books.count, 1)
        XCTAssertEqual(books[0].title, "Pride and Prejudice")
        XCTAssertEqual(books[0].author, "Austen, Jane")
        XCTAssertEqual(books[0].publisher, "Project Gutenberg, 1998")
        XCTAssertEqual(books[0].format, "epub")
        XCTAssertEqual(books[0].size, "0.3MB")
    }

    func testLegacyRecordFiltersUnsafeAndDuplicateLinks() async throws {
        let book = try await parse(Book.self, fixture: "info", mode: "info", md5: String(repeating: "1", count: 32))
        XCTAssertEqual(book.title, "Pride & Prejudice")
        XCTAssertEqual(book.author, "Jane Austen")
        XCTAssertEqual(book.links.count, 3)
        XCTAssertEqual(book.links[1].kind, "slow")
        XCTAssertEqual(book.links[1].url.host, "annas-archive.gd")
        XCTAssertFalse(book.links.contains { $0.url.scheme == "javascript" })
    }

    func testCurrentRecordUsesMetadataAndSources() async throws {
        let book = try await parse(Book.self, fixture: "info-current", mode: "info", md5: "51d2b22ca12a8b470b51f543298b34c9")
        XCTAssertEqual(book.title, "Pride and Prejudice")
        XCTAssertEqual(book.author, "Austen, Jane")
        XCTAssertEqual(book.format, "epub")
        XCTAssertEqual(book.links.count, 3)
    }

    func testChallengeAndUnknownLayoutAreNotEmptyResults() async {
        for html in ["<title>Just a moment</title><body>Check</body>", "<title>Search</title><body>Changed layout</body>"] {
            do {
                let _: [Book] = try await parse([Book].self, html: html, mode: "search")
                XCTFail("Expected rejection")
            } catch { /* Expected. */ }
        }
        do {
            let books = try await parse([Book].self, html: "<body>No files found</body>", mode: "search")
            XCTAssertTrue(books.isEmpty)
        } catch { XCTFail(error.localizedDescription) }
    }

    func testQueryEscapingAndSurnameFirstMatching() {
        let url = Catalogue.searchURL(origin: Catalogue.mirrors[0], query: "A&B + café", language: "en", format: "epub")
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!
        XCTAssertEqual(items.first { $0.name == "q" }?.value, "A&B + café")
        XCTAssertTrue(url.absoluteString.contains("%2B"))
        XCTAssertTrue(Catalogue.authorMatches("Austen, Jane", requested: "Jane Austen"))
        XCTAssertFalse(Catalogue.authorMatches("Jane Austen", requested: "Jan"))
    }

    func testChecksumRejectsDifferentEditionAndErrorPages() throws {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: file) }
        let data = Data("A plain text book".utf8)
        try data.write(to: file)
        let md5 = Insecure.MD5.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let correct = Book(md5: md5, title: "Test", url: URL(string: "https://annas-archive.gd/md5/\(md5)")!, format: "txt")
        XCTAssertNoThrow(try FileVerifier.validate(file, book: correct))
        let wrong = Book(md5: String(repeating: "0", count: 32), title: "Wrong edition", url: correct.url, format: "txt")
        XCTAssertThrowsError(try FileVerifier.validate(file, book: wrong))
        try Data("<!DOCTYPE html><title>Denied</title>".utf8).write(to: file)
        XCTAssertThrowsError(try FileVerifier.validate(file, book: correct))
        try Data().write(to: file)
        XCTAssertThrowsError(try FileVerifier.validate(file, book: correct))
    }

    func testEPUBRequiresValidArchiveEvenWithMatchingChecksum() throws {
        let data = Data("not a zip archive".utf8)
        let md5 = Insecure.MD5.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let book = Book(md5: md5, title: "Damaged EPUB", url: URL(string: "https://annas-archive.gd")!, format: "epub")
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: file) }
        try data.write(to: file)
        XCTAssertThrowsError(try FileVerifier.validate(file, book: book))
    }

    func testEPUBCRCValidationChecksEveryEntry() throws {
        for (name, valid) in [("valid-epub", true), ("bad-crc-epub", false)] {
            let fixture = Bundle(for: Self.self).url(forResource: name, withExtension: "base64", subdirectory: "Fixtures")!
            let encoded = try String(contentsOf: fixture, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
            let data = Data(base64Encoded: encoded)!
            let md5 = Insecure.MD5.hash(data: data).map { String(format: "%02x", $0) }.joined()
            let book = Book(md5: md5, title: "EPUB", url: URL(string: "https://annas-archive.gd")!, format: "epub")
            let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            defer { try? FileManager.default.removeItem(at: file) }
            try data.write(to: file)
            if valid { XCTAssertNoThrow(try FileVerifier.validate(file, book: book)) }
            else { XCTAssertThrowsError(try FileVerifier.validate(file, book: book)) }
        }
    }

    func testLibraryPersistsVerifiedFileWithoutUsingServerFilename() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = LibraryStore(directory: root)
        let file = root.appendingPathComponent("temporary")
        try Data("book".utf8).write(to: file)
        let book = Book(md5: String(repeating: "1", count: 32), title: "../bad/name", url: URL(string: "https://annas-archive.gd")!, format: "pdf")
        try store.addVerified(file, book: book)
        XCTAssertEqual(store.books.count, 1)
        XCTAssertFalse(store.books[0].filename.contains("/"))
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.file(for: store.books[0]).path))
        XCTAssertEqual(LibraryStore(directory: root).books[0].book.title, "../bad/name")
    }
}
