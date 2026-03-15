"""URL safety validation — SSRF protection for all HTTP requests (#172).

Shared by acquirer.py (PDF downloads) and web.py (URL text fetching).
Blocks requests to private IPs, localhost, link-local, multicast, and
reserved addresses.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def is_safe_url(url: str) -> bool:
    """Allow only safe external HTTP(S) URLs.

    Rejects:
    - Non-HTTP(S) schemes (ftp://, file://, etc.)
    - localhost, *.local hostnames
    - Private, loopback, link-local, multicast, reserved, unspecified IPs
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    host = (parsed.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".local"):
        return False

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True

    blocked = (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
    return not blocked
