"""SSRF gate (_net.validate_url) unit tests.

Run from 4_List_renderer/ with PYTHONPATH=src:
    python -m pytest tests/test_net.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from list_renderer.adapters._net import BlockedURLError, validate_url


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://127.0.0.1:8001/admin",                # other localhost service
    "http://localhost/",                          # loopback by name
    "https://10.0.0.5/internal",                  # private RFC1918
    "http://192.168.1.1/",                        # private
    "http://[::1]/",                              # IPv6 loopback
    "file:///C:/Users/secret.txt",                # non-http scheme
    "ftp://example.com/x",                        # non-http scheme
    "gopher://127.0.0.1:11211/",                  # non-http scheme
    "",                                           # empty
    "http://",                                    # no host
])
def test_blocked(url):
    with pytest.raises((BlockedURLError, ValueError)):
        validate_url(url)


def test_allows_public_host():
    # A resolvable public host must pass unchanged. (Uses example.com, an IANA
    # reserved-for-docs domain that resolves to a public, non-private address.)
    out = validate_url("https://example.com/feed.xml")
    assert out == "https://example.com/feed.xml"
