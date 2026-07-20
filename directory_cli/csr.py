"""Local key + CSR generation for `cert sign`.

The member API forces the certificate subject (CN = the org's identifier_url) regardless
of the CSR, so the CSR here only needs to be well-formed; the subject is a placeholder.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def generate_key_and_csr(common_name: str = "directory-cli") -> tuple[bytes, str]:
    """Generate a P-256 private key and a CSR. Returns (private_key_pem, csr_pem)."""
    key = ec.generate_private_key(ec.SECP256R1())
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()
    return key_pem, csr_pem
