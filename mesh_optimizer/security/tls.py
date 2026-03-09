"""TLS/SSL context helpers for secure agent-controller communication."""
from __future__ import annotations

import logging
import ssl
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def create_ssl_context(
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
    ca_path: Optional[str] = None,
    verify: bool = True,
    server_side: bool = False,
) -> Optional[ssl.SSLContext]:
    """Create an SSL context for TLS communication.

    Args:
        cert_path: Path to PEM certificate file.
        key_path: Path to PEM private key file.
        ca_path: Path to CA bundle for verification.
        verify: Whether to verify peer certificates.
        server_side: True for server contexts, False for client contexts.

    Returns:
        An ssl.SSLContext, or None if no TLS files are provided.
    """
    if not cert_path and not key_path and not ca_path:
        return None

    if server_side:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    else:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    # Minimum TLS 1.2
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    # Strong cipher suites only
    ctx.set_ciphers(
        "ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS:!RC4"
    )

    if cert_path and key_path:
        cert = Path(cert_path)
        key = Path(key_path)
        if not cert.exists():
            raise FileNotFoundError(f"TLS certificate not found: {cert_path}")
        if not key.exists():
            raise FileNotFoundError(f"TLS key not found: {key_path}")
        ctx.load_cert_chain(str(cert), str(key))
        logger.info("TLS certificate loaded: %s", cert_path)

    if ca_path:
        ca = Path(ca_path)
        if not ca.exists():
            raise FileNotFoundError(f"TLS CA bundle not found: {ca_path}")
        ctx.load_verify_locations(str(ca))
        logger.info("TLS CA bundle loaded: %s", ca_path)

    if verify:
        ctx.verify_mode = ssl.CERT_REQUIRED
        if not ca_path:
            ctx.load_default_certs()
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning("TLS certificate verification DISABLED")

    return ctx


def create_client_ssl_context(security_config) -> Optional[ssl.SSLContext]:
    """Create a client SSL context from a SecurityConfig dataclass.

    Returns None if TLS is not configured.
    """
    if not security_config.tls_cert_path and not security_config.tls_ca_path:
        return None

    return create_ssl_context(
        cert_path=security_config.tls_cert_path or None,
        key_path=security_config.tls_key_path or None,
        ca_path=security_config.tls_ca_path or None,
        verify=security_config.verify_tls,
        server_side=False,
    )
