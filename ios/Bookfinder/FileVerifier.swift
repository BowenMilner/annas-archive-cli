import CryptoKit
import Combine
import Foundation
import ZIPFoundation

enum FileVerifier {
    static func validate(_ file: URL, book: Book) throws {
        guard book.md5.range(of: "^[a-fA-F0-9]{32}$", options: .regularExpression) != nil else {
            throw BookfinderError.message("The selected edition has no valid checksum.")
        }
        let stream = try FileHandle(forReadingFrom: file)
        defer { try? stream.close() }
        var digest = Insecure.MD5()
        var bytes = 0
        var prefix = Data()
        while let chunk = try stream.read(upToCount: 1024 * 1024), !chunk.isEmpty {
            if prefix.isEmpty { prefix = chunk.prefix(512) }
            bytes += chunk.count
            digest.update(data: chunk)
        }
        guard bytes > 0 else { throw BookfinderError.message("The source returned an empty file.") }
        let opening = String(decoding: prefix, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !opening.hasPrefix("<!doctype html"), !opening.hasPrefix("<html"),
              !opening.hasPrefix("<?xml"), !opening.hasPrefix("{\"") else {
            throw BookfinderError.message("The source returned a web page instead of a book.")
        }
        let checksum = digest.finalize().map { String(format: "%02x", $0) }.joined()
        guard checksum == book.md5.lowercased() else {
            throw BookfinderError.message("The file does not match this edition’s checksum. Nothing was saved.")
        }
        if book.format.lowercased() == "epub" {
            let archive = try Archive(url: file, accessMode: .read)
            guard let mime = archive["mimetype"] else { throw BookfinderError.message("The EPUB has no mimetype entry.") }
            var value = Data()
            _ = try archive.extract(mime) { value.append($0) }
            guard value == Data("application/epub+zip".utf8) else { throw BookfinderError.message("The EPUB mimetype is invalid.") }
            // Validate every member's CRC without decompressing files to disk.
            for entry in archive {
                let crc = try archive.extract(entry) { _ in }
                guard crc == entry.checksum else { throw BookfinderError.message("The EPUB failed its integrity check.") }
            }
        }
    }
}

@MainActor
final class LibraryStore: ObservableObject {
    @Published private(set) var books: [SavedBook] = []
    @Published var loadError: String?
    let directory: URL
    private var index: URL { directory.appendingPathComponent("library.json") }

    init(directory: URL? = nil) {
        self.directory = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Bookfinder", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
            if FileManager.default.fileExists(atPath: index.path) {
                books = try JSONDecoder().decode([SavedBook].self, from: Data(contentsOf: index))
            }
        } catch { loadError = "Could not read the library: \(error.localizedDescription)" }
    }

    func file(for saved: SavedBook) -> URL { directory.appendingPathComponent(saved.filename) }

    func addVerified(_ temporary: URL, book: Book) throws {
        // Caller verifies off the main actor before publication.
        guard loadError == nil else { throw BookfinderError.message("The existing library could not be read. Restart after checking storage.") }
        let id = UUID()
        let allowed = ["epub", "pdf", "mobi", "azw", "azw3", "djvu", "txt", "rtf", "doc", "docx", "cbr", "cbz", "fb2", "zip"]
        let ext = allowed.contains(book.format.lowercased()) ? book.format.lowercased() : "book"
        let saved = SavedBook(id: id, book: book, filename: "\(id.uuidString).\(ext)", savedAt: Date())
        let destination = file(for: saved)
        try FileManager.default.moveItem(at: temporary, to: destination)
        do {
            let next = [saved] + books
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: destination.path)
            try JSONEncoder().encode(next).write(to: index, options: [.atomic, .completeFileProtection])
            books = next
        } catch {
            try? FileManager.default.removeItem(at: destination)
            throw error
        }
    }
}
