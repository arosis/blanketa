import Foundation
import Security
import CryptoKit

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

func commandLineOption(_ name: String) -> String? {
    guard let index = CommandLine.arguments.firstIndex(of: name), index + 1 < CommandLine.arguments.count else { return nil }
    return CommandLine.arguments[index + 1]
}

// A keychain given by --keychain is unlocked with ZFO_KEYCHAIN_PASSWORD when set,
// so a separate test keychain does not raise the system password dialog.
func keychainSearchList() -> [SecKeychain]? {
    guard let path = commandLineOption("--keychain") else { return nil }
    var keychain: SecKeychain?
    guard SecKeychainOpen(path, &keychain) == errSecSuccess, let keychain else { fail("cannot open keychain \(path)") }
    if let password = ProcessInfo.processInfo.environment["ZFO_KEYCHAIN_PASSWORD"] {
        let status = SecKeychainUnlock(keychain, UInt32(password.utf8.count), password, true)
        if status != errSecSuccess { fail("cannot unlock keychain \(path): \(status)") }
    }
    return [keychain]
}

func signingIdentities() -> [(SecIdentity, Bool)] {
    var found: [(SecIdentity, Bool)] = []
    var queries: [([String: Any], Bool)] = []
    var baseQuery: [String: Any] = [
        kSecClass as String: kSecClassIdentity,
        kSecMatchLimit as String: kSecMatchLimitAll,
        kSecReturnRef as String: true,
    ]
    if let searchList = keychainSearchList() {
        baseQuery[kSecMatchSearchList as String] = searchList
        queries.append((baseQuery, false))
    } else {
        queries.append((baseQuery, false))
        var tokenQuery = baseQuery
        tokenQuery[kSecAttrAccessGroup as String] = kSecAttrAccessGroupToken
        queries.append((tokenQuery, true))
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

func listIdentities() {
    var entries: [[String: Any]] = []
    for (identity, onToken) in signingIdentities() {
        var cert: SecCertificate?
        SecIdentityCopyCertificate(identity, &cert)
        let der = SecCertificateCopyData(cert!) as Data
        entries.append([
            "sha1": sha1Hex(der),
            "subject": SecCertificateCopySubjectSummary(cert!) as String? ?? "",
            "token": onToken,
            "certificate": der.base64EncodedString(),
        ])
    }
    let json = try! JSONSerialization.data(withJSONObject: entries, options: [.prettyPrinted, .sortedKeys])
    FileHandle.standardOutput.write(json)
}

func signStandardInput() {
    guard let wanted = commandLineOption("--cert")?.lowercased() else { fail("missing --cert SHA1HEX") }
    let algorithm: SecKeyAlgorithm = commandLineOption("--alg") == "sha1"
        ? .rsaSignatureMessagePKCS1v15SHA1 : .rsaSignatureMessagePKCS1v15SHA256
    guard let (identity, _) = signingIdentities().first(where: { sha1Hex(certificateDER($0.0)) == wanted }) else {
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
}

func printTrustVerdicts() {
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
}

let command = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""
switch command {
case "list": listIdentities()
case "sign": signStandardInput()
case "trust": printTrustVerdicts()
default: fail("usage: macsign list|sign|trust …")
}
