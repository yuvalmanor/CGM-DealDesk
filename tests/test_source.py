from dealdesk.source import UNKNOWN_SOURCE, derive_source


def test_plain_address():
    assert derive_source("deals@acme-wholesale.com") == "acme-wholesale.com"


def test_display_name_address():
    assert derive_source('"Acme Wholesale" <blast@acme-wholesale.com>') == "acme-wholesale.com"


def test_domain_is_lowercased():
    assert derive_source("Blast@ACME-Wholesale.COM") == "acme-wholesale.com"


def test_missing_domain_falls_back_to_unknown():
    assert derive_source("not-an-address") == UNKNOWN_SOURCE
    assert derive_source("") == UNKNOWN_SOURCE
