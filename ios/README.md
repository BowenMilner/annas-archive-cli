# Bookfinder for iPhone and iPad

Native SwiftUI search, edition details, a verified local library and sharing to
Files or reader apps. This first iOS port lives alongside the Python terminal
app and uses the same catalogue layouts and exact-edition checksum contract.
It runs on iOS 17 or newer. The bundle ID is `dev.bowenmilner.bookfinder`.

## Personal installation

Download the `Bookfinder-unsigned-iOS` artifact from the iOS Bookfinder workflow.
Unzip the artifact to get `Bookfinder-unsigned.ipa`, then import it with your
existing LiveContainer setup, or sign it with your preferred sideloading tool.
No Apple account credentials or provisioning profiles are stored in the repo.
This is not an App Store or TestFlight build.

To build on a Mac with full Xcode selected:

```sh
brew install xcodegen
bash ios/build.sh
```

The generated Xcode project, downloaded package checkout and build products are
ignored. ZIPFoundation is pinned to 0.9.19 for streaming EPUB CRC validation.

## How to use

Search by title, optionally filter the returned page by author, and choose an
edition. Defaults are English and EPUB; format and language persist between
launches. Automatic search tries the three configured mirrors and remembers a
successful one. A rate limit ends the attempt. An explicitly selected mirror
stays pinned.

The edition screen lists free HTTP sources. Select a source, complete any browser
check and use the source's download control. A queue remains visible in that
browser; the app does not fabricate a countdown or silently change editions.
WebKit handles page redirects, cookies and file transfers within the same session.
Close a search verification browser and tap Search again to retry.

The downloaded file stays temporary until its MD5 matches the selected edition.
EPUBs also require the expected mimetype and valid CRCs for every ZIP entry.
Empty files, mismatched editions and obvious error pages are rejected. Files use
generated names, never untrusted server filenames. Verified books persist in the
app's protected Application Support directory. Library → Share exports a file
to Files or a reader. Cancelling or a failed verification removes temporary data.

## Scope and remaining acceptance

This is a first port, not full terminal feature parity. Download source selection
and queues are browser-driven; automatic free-source retries, the separately
labelled Gutenberg alternative, statistics, pagination and in-app reading are not
implemented yet. Safari/Firefox cookie imports are unavailable on iOS. Browser
verification can still fail inside WebKit; this app cannot guarantee clearance.
Downloads are foreground-only: keep Bookfinder open until verification completes.
App deletion removes its private library; export books you want to keep.

CI runs the original reduced search/record fixtures against real WebKit, URL and
author matching checks, and file-integrity tests before producing an unsigned IPA.
Physical-device/LiveContainer launch, browser verification, an exact-edition live
download, cancellation and Files/reader export still require device acceptance.
The Linux authoring environment cannot certify those checks.
