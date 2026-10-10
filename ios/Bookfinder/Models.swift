import Foundation

struct Book: Codable, Identifiable, Hashable {
    var id: String { md5 }
    let md5: String
    let title: String
    let url: URL
    var author: String = ""
    var publisher: String = ""
    var language: String = ""
    var format: String = ""
    var size: String = ""
    var description: String = ""
    var links: [SourceLink] = []
}

struct SourceLink: Codable, Hashable, Identifiable {
    var id: String { url.absoluteString }
    let label: String
    let url: URL
    let kind: String
}

struct SavedBook: Codable, Identifiable {
    let id: UUID
    let book: Book
    let filename: String
    let savedAt: Date
}

enum BookfinderError: LocalizedError {
    case message(String)
    case browserCheck(URL)
    case rateLimited
    var errorDescription: String? {
        switch self {
        case .message(let text): return text
        case .browserCheck: return "This site needs a browser check. Open the browser, complete the check, then retry."
        case .rateLimited: return "This site has rate limited requests. Wait before trying again."
        }
    }
}

struct CatalogueEnvelope<Value: Decodable>: Decodable {
    let value: Value?
    let error: String?
}

enum Catalogue {
    static let mirrors = ["https://annas-archive.gd", "https://annas-archive.gl", "https://annas-archive.pk"]
    static func searchURL(origin: String, query: String, language: String, format: String) -> URL {
        var parts = URLComponents(string: origin + "/search")!
        parts.queryItems = [URLQueryItem(name: "q", value: query), URLQueryItem(name: "display", value: "")]
        if !language.isEmpty { parts.queryItems?.append(URLQueryItem(name: "lang", value: language)) }
        if !format.isEmpty { parts.queryItems?.append(URLQueryItem(name: "ext", value: format)) }
        // Catalogue query parsing follows form semantics, where a literal + means a space.
        parts.percentEncodedQuery = parts.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        return parts.url!
    }
    static func authorMatches(_ actual: String, requested: String) -> Bool {
        let words: (String) -> Set<String> = { value in
            Set(value.folding(options: [.caseInsensitive], locale: Locale(identifier: "en_US_POSIX"))
                .components(separatedBy: .alphanumerics.inverted).filter { !$0.isEmpty })
        }
        let wanted = words(requested)
        return !wanted.isEmpty && wanted.isSubset(of: words(actual))
    }
}
