// Signs data with a private key held by macOS: login keychain, a given keychain file,
// or a smart card / token exposed through CryptoTokenKit. The key never leaves macOS;
// a token asks for its PIN through the system dialog.
//
// usage:
//   macsign list [--keychain PATH]
//   macsign sign --cert SHA1HEX [--alg sha256|sha1] [--keychain PATH] < data > signature.b64
//   macsign trust < base64 DER certificates, one per line   (prints one JSON verdict per line)
import Foundation
import Security
import CryptoKit

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

func option(_ name: String) -> String? {
    guard let i = CommandLine.arguments.firstIndex(of: name), i + 1 < CommandLine.arguments.count else { return nil }
    return CommandLine.arguments[i + 1]
}

// A keychain given by --keychain is unlocked with ZFO_KEYCHAIN_PASSWORD when set,
// so a separate test keychain does not raise the system password dialog.
func searchList() -> [SecKeychain]? {
    guard let path = option("--keychain") else { return nil }
    var keychain: SecKeychain?
    guard SecKeychainOpen(path, &keychain) == errSecSuccess, let keychain else { fail("cannot open keychain \(path)") }
    if let password = ProcessInfo.processInfo.environment["ZFO_KEYCHAIN_PASSWORD"] {
        let status = SecKeychainUnlock(keychain, UInt32(password.utf8.count), password, true)
        if status != errSecSuccess { fail("cannot unlock keychain \(path): \(status)") }
    }
    return [keychain]
}

func identities() -> [(SecIdentity, Bool)] {
    var found: [(SecIdentity, Bool)] = []
    var queries: [([String: Any], Bool)] = []
    var base: [String: Any] = [
        kSecClass as String: kSecClassIdentity,
        kSecMatchLimit as String: kSecMatchLimitAll,
        kSecReturnRef as String: true,
    ]
    if let list = searchList() {
        base[kSecMatchSearchList as String] = list
        queries.append((base, false))
    } else {
        queries.append((base, false))
        var token = base
        token[kSecAttrAccessGroup as String] = kSecAttrAccessGroupToken
        queries.append((token, true))
    }
    var seen = Set<Data>()
    for (query, onToken) in queries {
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let items = result as? [SecIdentity] else { continue }
        for identity in items {
            let der = certificateDER(identity)
            if seen.insert(der).inserted { found.append((identity, onToken)) }
        }
    }
    return found
}

func certificateDER(_ identity: SecIdentity) -> Data {
    var cert: SecCertificate?
    SecIdentityCopyCertificate(identity, &cert)
    return SecCertificateCopyData(cert!) as Data
}

func sha1Hex(_ data: Data) -> String {
    Insecure.SHA1.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

let command = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""
switch command {
case "list":
    var out: [[String: Any]] = []
    for (identity, onToken) in identities() {
        var cert: SecCertificate?
        SecIdentityCopyCertificate(identity, &cert)
        let der = SecCertificateCopyData(cert!) as Data
        out.append([
            "sha1": sha1Hex(der),
            "subject": SecCertificateCopySubjectSummary(cert!) as String? ?? "",
            "token": onToken,
            "certificate": der.base64EncodedString(),
        ])
    }
    let json = try! JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted, .sortedKeys])
    FileHandle.standardOutput.write(json)
case "sign":
    guard let wanted = option("--cert")?.lowercased() else { fail("missing --cert SHA1HEX") }
    let algorithm: SecKeyAlgorithm = option("--alg") == "sha1"
        ? .rsaSignatureMessagePKCS1v15SHA1 : .rsaSignatureMessagePKCS1v15SHA256
    guard let (identity, _) = identities().first(where: { sha1Hex(certificateDER($0.0)) == wanted }) else {
        fail("certificate \(wanted) not found")
    }
    var key: SecKey?
    guard SecIdentityCopyPrivateKey(identity, &key) == errSecSuccess, let key else { fail("no private key") }
    let data = FileHandle.standardInput.readDataToEndOfFile()
    var error: Unmanaged<CFError>?
    guard let signature = SecKeyCreateSignature(key, algorithm, data as CFData, &error) as Data? else {
        fail("signing failed: \(error!.takeRetainedValue())")
    }
    FileHandle.standardOutput.write(signature.base64EncodedString().data(using: .utf8)!)
case "trust":
    let input = String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8) ?? ""
    for line in input.split(separator: "\n") {
        var verdict: [String: Any] = ["trusted": false]
        if let der = Data(base64Encoded: String(line)), let cert = SecCertificateCreateWithData(nil, der as CFData) {
            var trust: SecTrust?
            SecTrustCreateWithCertificates(cert, SecPolicyCreateBasicX509(), &trust)
            var error: CFError?
            verdict["trusted"] = SecTrustEvaluateWithError(trust!, &error)
            if let error { verdict["reason"] = CFErrorCopyDescription(error) as String }
        }
        let json = try! JSONSerialization.data(withJSONObject: verdict)
        FileHandle.standardOutput.write(json + "\n".data(using: .utf8)!)
    }
default:
    fail("usage: macsign list|sign|trust …")
}
