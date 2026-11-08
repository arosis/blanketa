import asyncio
import base64
import copy
import hashlib
from datetime import datetime, timezone
from urllib.parse import unquote

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from asn1crypto import cms
from lxml import etree
from pyhanko.sign.validation.generic_cms import validate_tst_signed_data
from pyhanko.sign.validation.status import TimestampSignatureStatus
from pyhanko_certvalidator import ValidationContext

import pdf_sign
from zfo_verify import C14N, DS, EXC

C14N_COMMENTS = C14N + "#WithComments"
EXC_COMMENTS = EXC + "WithComments"
C14N11 = "http://www.w3.org/2006/12/xml-c14n11"
C14N11_COMMENTS = C14N11 + "#WithComments"
CANONICALIZATIONS = {C14N, C14N_COMMENTS, EXC, EXC_COMMENTS, C14N11, C14N11_COMMENTS}
ENVELOPED = DS + "enveloped-signature"
BASE64 = DS + "base64"
XADES_NAMESPACES = ("http://uri.etsi.org/01903/v1.3.2#", "http://uri.etsi.org/01903/v1.4.1#")
SIGNED_PROPERTIES_TYPE = "http://uri.etsi.org/01903#SignedProperties"
XML_NS = "http://www.w3.org/XML/1998/namespace"

STRONG_DIGESTS = {
    "http://www.w3.org/2001/04/xmlenc#sha256": hashlib.sha256,
    "http://www.w3.org/2001/04/xmldsig-more#sha384": hashlib.sha384,
    "http://www.w3.org/2001/04/xmlenc#sha512": hashlib.sha512,
}
LEGACY_DIGESTS = {**STRONG_DIGESTS, DS + "sha1": hashlib.sha1}
SIGNATURE_METHODS = {
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": (rsa.RSAPublicKey, hashes.SHA256),
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384": (rsa.RSAPublicKey, hashes.SHA384),
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512": (rsa.RSAPublicKey, hashes.SHA512),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256": (ec.EllipticCurvePublicKey, hashes.SHA256),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha384": (ec.EllipticCurvePublicKey, hashes.SHA384),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha512": (ec.EllipticCurvePublicKey, hashes.SHA512),
}

INCOMPLETE = "podpis nemá úplnou strukturu"
UNKNOWN_SIGNATURE_ALGORITHM = "neznámý algoritmus podpisu"
UNKNOWN_DIGEST = "neznámý nebo nepovolený algoritmus otisku"
UNKNOWN_TRANSFORM = "nepovolená transformace"
UNSUPPORTED_C14N = "nepodporovaná kanonizace"
WRAPPED = "podpis obsahuje duplicitní nebo cizí prvky"
BAD_REFERENCE = "odkaz na soubor mimo kontejner nebo na chybějící soubor"
NO_SIGNED_PROPERTIES = "chybí podepsané vlastnosti XAdES"
CERTIFICATE_MISMATCH = "certifikát podepisujícího nesouhlasí s podepsaným otiskem"
BAD_VALUE = "hodnota podpisu nesouhlasí s certifikátem"
CONTENT_CHANGED = "podepsaný obsah byl změněn"


class XadesError(ValueError):
    pass


class _Refused(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _ds(name):
    return f"{{{DS}}}{name}"


def parse(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        root = etree.fromstring(data, parser)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise XadesError("not a readable XML document") from error
    if root.getroottree().docinfo.doctype:
        raise XadesError("XML with a DOCTYPE is refused")
    return root


def find_signatures(root):
    if root.tag == _ds("Signature"):
        return [root]
    return root.findall(_ds("Signature"))


def _canonicalize(node, algorithm, *, with_comments, prefixes=None):
    # lxml has no C14N 1.1; it equals C14N 1.0 unless xml:id or xml:base appear in or above the canonicalized element.
    if algorithm in (C14N11, C14N11_COMMENTS):
        element = node.getroot() if isinstance(node, etree._ElementTree) else node
        if element.xpath("ancestor-or-self::*/@xml:id | ancestor-or-self::*/@xml:base | .//@xml:id | .//@xml:base",
                         namespaces={"xml": XML_NS}):
            raise _Refused(UNSUPPORTED_C14N)
    exclusive = algorithm in (EXC, EXC_COMMENTS)
    try:
        return etree.tostring(node, method="c14n", exclusive=exclusive, with_comments=with_comments,
                              inclusive_ns_prefixes=prefixes if exclusive else None)
    except etree.C14NError as error:
        raise _Refused(UNSUPPORTED_C14N) from error


def _inclusive_prefixes(method):
    namespaces = method.find(f"{{{EXC}}}InclusiveNamespaces")
    return namespaces.get("PrefixList", "").split() if namespaces is not None else None


def _decode_base64(text):
    return base64.b64decode("".join((text or "").split()), validate=True)


def _display_name(name):
    common_names = name.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return common_names[0].value if common_names else name.rfc4514_string()


def _signed_at(signed_properties):
    namespace = etree.QName(signed_properties).namespace
    text = signed_properties.findtext(f"{{{namespace}}}SignedSignatureProperties/{{{namespace}}}SigningTime")
    try:
        moment = datetime.fromisoformat((text or "").strip())
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


class _Verification:
    def __init__(self, signature, resolve, allow_empty_uri, standalone):
        self.signature = signature
        self.resolve = resolve
        self.allow_empty_uri = allow_empty_uri
        self.standalone = standalone
        self.reason = ""
        self.content_changed = False
        self.references = []
        self.ids = {}
        for element in signature.getroottree().getroot().iter(tag=etree.Element):
            if element.get("Id") is not None:
                self.ids.setdefault(element.get("Id"), []).append(element)

    def fail(self, reason):
        if not self.reason:
            self.reason = reason

    def child(self, parent, name):
        found = parent.findall(_ds(name))
        if len(found) > 1:
            raise _Refused(WRAPPED)
        return found[0] if found else None

    def is_inside_signature(self, element):
        return any(ancestor is self.signature for ancestor in element.iterancestors())

    def internal_target(self, uri):
        found = self.ids.get(uri[1:], [])
        if len(found) != 1 or not self.is_inside_signature(found[0]):
            raise _Refused(WRAPPED)
        target = found[0]
        parent = target.getparent()
        grandparent = parent.getparent()
        if target.tag in (_ds("Object"), _ds("KeyInfo")) and parent is self.signature:
            return target
        if target.tag == _ds("Manifest") and parent.tag == _ds("Object") and grandparent is self.signature:
            return target
        if etree.QName(target).localname == "SignedProperties" and etree.QName(target).namespace in XADES_NAMESPACES:
            return target
        raise _Refused(WRAPPED)

    def transforms(self, reference):
        container = self.child(reference, "Transforms")
        if container is None:
            return []
        transforms = list(container)
        if any(transform.tag != _ds("Transform") for transform in transforms):
            raise _Refused(UNKNOWN_TRANSFORM)
        return transforms

    def canonical_transforms(self, transforms, *, enveloped_allowed):
        algorithms = [transform.get("Algorithm") for transform in transforms]
        if algorithms[:1] == [ENVELOPED]:
            if not enveloped_allowed:
                raise _Refused(UNKNOWN_TRANSFORM)
            transforms, algorithms = transforms[1:], algorithms[1:]
        if len(algorithms) > 1 or any(algorithm not in CANONICALIZATIONS for algorithm in algorithms):
            raise _Refused(UNKNOWN_TRANSFORM)
        return transforms[0] if transforms else None

    def whole_document(self, transforms):
        algorithms = [transform.get("Algorithm") for transform in transforms]
        if not self.allow_empty_uri or algorithms[:1] != [ENVELOPED]:
            raise _Refused(BAD_REFERENCE)
        method = self.canonical_transforms(transforms, enveloped_allowed=True)
        tree = self.signature.getroottree()
        path = []
        element = self.signature
        while element.getparent() is not None:
            path.append(element.getparent().index(element))
            element = element.getparent()
        document = copy.deepcopy(tree)
        target = document.getroot()
        for index in reversed(path):
            target = target[index]
        if target is document.getroot():
            raise _Refused(BAD_REFERENCE)
        target.getparent().remove(target)
        return self.apply_canonicalization(document, method, default=C14N)

    def apply_canonicalization(self, node, method, *, default):
        algorithm = method.get("Algorithm") if method is not None else default
        prefixes = _inclusive_prefixes(method) if method is not None else None
        return _canonicalize(node, algorithm, with_comments=False, prefixes=prefixes)

    def resolved_file(self, uri):
        return None if self.standalone else self.resolve(uri)

    def file_data(self, resolved, transforms):
        if self.standalone:
            raise _Refused(BAD_REFERENCE)
        method = self.canonical_transforms(transforms, enveloped_allowed=False)
        if resolved is None:
            raise _Refused(BAD_REFERENCE)
        _, data = resolved
        if method is not None:
            try:
                document = parse(data)
            except XadesError as error:
                raise _Refused(UNKNOWN_TRANSFORM) from error
            algorithm = method.get("Algorithm")
            data = _canonicalize(document.getroottree(), algorithm, with_comments=algorithm.endswith("WithComments"),
                                 prefixes=_inclusive_prefixes(method))
        return data

    def internal_data(self, target, transforms):
        algorithms = [transform.get("Algorithm") for transform in transforms]
        if BASE64 in algorithms:
            if not self.standalone or algorithms != [BASE64] or target.tag != _ds("Object"):
                raise _Refused(UNKNOWN_TRANSFORM)
            try:
                return _decode_base64("".join(target.itertext()))
            except ValueError as error:
                raise _Refused(UNKNOWN_TRANSFORM) from error
        method = self.canonical_transforms(transforms, enveloped_allowed=False)
        return self.apply_canonicalization(target, method, default=C14N)

    def check_reference(self, reference, *, in_manifest):
        uri = reference.get("URI")
        is_file = uri is None or not uri.startswith("#")
        # Resolved before any other check, so a refused reference still names the listed file it points at.
        resolved = self.resolved_file(uri) if is_file and uri != "" else None
        entry = {"name": resolved[0] if resolved else unquote(uri) if uri else "", "outcome": "refused"}
        target = None
        try:
            transforms = self.transforms(reference)
            if uri == "":
                data = self.whole_document(transforms)
            elif is_file:
                data = self.file_data(resolved, transforms)
            else:
                target = self.internal_target(uri)
                if target.tag == _ds("Manifest") and in_manifest:
                    raise _Refused(WRAPPED)
                data = self.internal_data(target, transforms)
                if self.standalone and target.tag == _ds("Object"):
                    decoded = [transform.get("Algorithm") for transform in transforms] == [BASE64]
                    entry.update(content=data, mime=(target.get("MimeType") or "") if decoded else "application/xml")
            is_properties = target is not None and etree.QName(target).localname == "SignedProperties"
            method = self.child(reference, "DigestMethod")
            digests = LEGACY_DIGESTS if (is_file and uri != "") or is_properties else STRONG_DIGESTS
            function = digests.get(method.get("Algorithm") if method is not None else None)
            if function is None:
                raise _Refused(UNKNOWN_DIGEST)
            try:
                expected = _decode_base64(reference.findtext(_ds("DigestValue")))
            except ValueError as error:
                raise _Refused(INCOMPLETE) from error
            if function(data).digest() == expected:
                entry["outcome"] = "ok"
            else:
                entry["outcome"] = "changed"
                self.content_changed = True
                self.fail(CONTENT_CHANGED)
        except _Refused as refused:
            self.fail(refused.reason)
        if is_file or (target is not None and target.tag == _ds("Object")):
            self.references.append(entry)
        return target, entry["outcome"]

    def unchecked_manifest_entry(self, reference, outcome):
        uri = reference.get("URI")
        if uri is not None and (uri == "" or uri.startswith("#")):
            return
        resolved = self.resolved_file(uri)
        self.references.append({"name": resolved[0] if resolved else unquote(uri) if uri else "", "outcome": outcome})

    def check_references(self, signed_info):
        verified = set()
        for reference in signed_info.findall(_ds("Reference")):
            target, outcome = self.check_reference(reference, in_manifest=False)
            if target is not None and target.tag == _ds("Manifest") and target not in verified:
                verified.add(target)
                for manifest_reference in target.findall(_ds("Reference")):
                    if outcome == "ok":
                        self.check_reference(manifest_reference, in_manifest=True)
                    else:
                        self.unchecked_manifest_entry(manifest_reference, outcome)

    def signed_properties(self, signed_info):
        signature_id = self.signature.get("Id")
        qualifying = [element for element in self.signature.iter(*(f"{{{ns}}}QualifyingProperties" for ns in XADES_NAMESPACES))]
        if not qualifying or not signature_id:
            raise _Refused(NO_SIGNED_PROPERTIES)
        if len(qualifying) > 1:
            raise _Refused(WRAPPED)
        [qualifying] = qualifying
        holder = qualifying.getparent()
        if holder.tag != _ds("Object") or holder.getparent() is not self.signature or qualifying.get("Target") != f"#{signature_id}":
            raise _Refused(WRAPPED)
        namespace = etree.QName(qualifying).namespace
        properties = qualifying.findall(f"{{{namespace}}}SignedProperties")
        if len(properties) != 1:
            raise _Refused(NO_SIGNED_PROPERTIES if not properties else WRAPPED)
        references = [reference for reference in signed_info.findall(_ds("Reference")) if reference.get("Type") == SIGNED_PROPERTIES_TYPE]
        if not references:
            raise _Refused(NO_SIGNED_PROPERTIES)
        uri = references[0].get("URI") or ""
        if len(references) > 1 or not uri.startswith("#") or self.internal_target(uri) is not properties[0]:
            raise _Refused(WRAPPED)
        return properties[0]

    def signing_certificate(self, signed_properties):
        namespace = etree.QName(signed_properties).namespace
        digest = None
        for name in ("SigningCertificateV2", "SigningCertificate"):
            digest = signed_properties.find(f"{{{namespace}}}SignedSignatureProperties/{{{namespace}}}{name}/{{{namespace}}}Cert/{{{namespace}}}CertDigest")
            if digest is not None:
                break
        if digest is None:
            raise _Refused(CERTIFICATE_MISMATCH)
        method = digest.find(_ds("DigestMethod"))
        function = LEGACY_DIGESTS.get(method.get("Algorithm") if method is not None else None)
        if function is None:
            raise _Refused(UNKNOWN_DIGEST)
        try:
            expected = _decode_base64(digest.findtext(_ds("DigestValue")))
        except ValueError as error:
            raise _Refused(CERTIFICATE_MISMATCH) from error
        key_info = self.child(self.signature, "KeyInfo")
        for element in key_info.iter(_ds("X509Certificate")) if key_info is not None else ():
            try:
                der = _decode_base64(element.text)
            except ValueError:
                continue
            if function(der).digest() == expected:
                try:
                    cert = x509.load_der_x509_certificate(der)
                    _display_name(cert.subject), _display_name(cert.issuer)
                except ValueError as error:
                    raise _Refused(CERTIFICATE_MISMATCH) from error
                return cert
        raise _Refused(CERTIFICATE_MISMATCH)

    def check_value(self, signed_info, cert):
        method = signed_info.find(_ds("CanonicalizationMethod"))
        key_type, hash_type = SIGNATURE_METHODS[signed_info.find(_ds("SignatureMethod")).get("Algorithm")]
        public_key = cert.public_key()
        if not isinstance(public_key, key_type):
            raise _Refused(BAD_VALUE)
        data = _canonicalize(signed_info, method.get("Algorithm"), with_comments=method.get("Algorithm").endswith("WithComments"),
                             prefixes=_inclusive_prefixes(method))
        try:
            value = _decode_base64(self.signature.findtext(_ds("SignatureValue")))
            if isinstance(public_key, rsa.RSAPublicKey):
                public_key.verify(value, data, padding.PKCS1v15(), hash_type())
            else:
                size = (public_key.curve.key_size + 7) // 8
                if len(value) != 2 * size:
                    raise _Refused(BAD_VALUE)
                der = utils.encode_dss_signature(int.from_bytes(value[:size], "big"), int.from_bytes(value[size:], "big"))
                public_key.verify(der, data, ec.ECDSA(hash_type()))
        except (InvalidSignature, ValueError) as error:
            raise _Refused(BAD_VALUE) from error

    def check_algorithms(self, signed_info):
        method = self.child(signed_info, "CanonicalizationMethod")
        if method is None or method.get("Algorithm") not in CANONICALIZATIONS:
            raise _Refused(UNSUPPORTED_C14N)
        signature_method = self.child(signed_info, "SignatureMethod")
        if signature_method is None or signature_method.get("Algorithm") not in SIGNATURE_METHODS:
            raise _Refused(UNKNOWN_SIGNATURE_ALGORITHM)
        _canonicalize(signed_info, method.get("Algorithm"), with_comments=False)

    def run(self):
        result = {**unverified_result(""), "references": self.references}
        try:
            if any(len(elements) > 1 for elements in self.ids.values()):
                self.fail(WRAPPED)
            signed_info = self.child(self.signature, "SignedInfo")
            if signed_info is None or self.child(self.signature, "SignatureValue") is None:
                raise _Refused(INCOMPLETE)
        except _Refused as refused:
            self.fail(refused.reason)
            return self.finish(result)
        for step in (self.check_algorithms, self.check_references):
            try:
                step(signed_info)
            except _Refused as refused:
                self.fail(refused.reason)
        cert = None
        try:
            signed_properties = self.signed_properties(signed_info)
            result["signed_at"] = _signed_at(signed_properties)
            cert = self.signing_certificate(signed_properties)
        except _Refused as refused:
            self.fail(refused.reason)
        if cert is not None:
            result.update(subject=_display_name(cert.subject), issuer=_display_name(cert.issuer),
                          not_after=cert.not_valid_after_utc.isoformat(), der=cert.public_bytes(serialization.Encoding.DER))
            if not self.reason:
                try:
                    self.check_value(signed_info, cert)
                except _Refused as refused:
                    self.fail(refused.reason)
        result["stamps"] = self.collect_stamps()
        return self.finish(result)

    def signature_stamp(self, element):
        try:  # a damaged token only means the time is not confirmed, the signature is judged apart from it
            method = element.find(_ds("CanonicalizationMethod"))
            algorithm = method.get("Algorithm") if method is not None else C14N
            if algorithm not in CANONICALIZATIONS:
                raise _Refused(UNSUPPORTED_C14N)
            imprinted = _canonicalize(self.child(self.signature, "SignatureValue"), algorithm,
                                      with_comments=algorithm.endswith("WithComments"), prefixes=_inclusive_prefixes(method)
                                      if method is not None else None)
            token = element.find(f"{{{etree.QName(element).namespace}}}EncapsulatedTimeStamp")
            signed_data = cms.ContentInfo.load(_decode_base64(token.text))["content"]
            kwargs = asyncio.run(validate_tst_signed_data(
                signed_data, ValidationContext(allow_fetching=False), lambda name: hashlib.new(name, imprinted).digest()))
            return pdf_sign._stamp_details(TimestampSignatureStatus(**kwargs), "signature")
        except Exception:
            return pdf_sign._stamp_details(None, "signature")

    def collect_stamps(self):
        try:
            return [self.signature_stamp(element) for namespace in XADES_NAMESPACES
                    for element in self.signature.iterfind(
                        f"{_ds('Object')}/{{{namespace}}}QualifyingProperties/{{{namespace}}}UnsignedProperties/"
                        f"{{{namespace}}}UnsignedSignatureProperties/{{{namespace}}}SignatureTimeStamp")]
        except Exception:
            return [pdf_sign._stamp_details(None, "signature")]

    def finish(self, result):
        result.update(intact=not self.reason, broken_reason=self.reason, content_changed=self.content_changed)
        return result


def unverified_result(broken_reason):
    return {"subject": "", "issuer": "", "publisher": False, "intact": False, "broken_reason": broken_reason, "content_changed": False,
            "modified": False, "changes_unchecked": False, "not_after": None, "signed_at": None, "der": None, "stamps": []}


def verify(signature, resolve, *, allow_empty_uri=False, standalone=False):
    return _Verification(signature, resolve, allow_empty_uri, standalone).run()
