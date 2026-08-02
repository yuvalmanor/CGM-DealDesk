from dealdesk.source import UNKNOWN_SOURCE, derive_seller_agent, derive_source


def test_plain_address():
    assert derive_source("deals@acme-wholesale.com") == "acme-wholesale.com"


def test_display_name_address():
    assert derive_source('"Acme Wholesale" <blast@acme-wholesale.com>') == "acme-wholesale.com"


def test_domain_is_lowercased():
    assert derive_source("Blast@ACME-Wholesale.COM") == "acme-wholesale.com"


def test_missing_domain_falls_back_to_unknown():
    assert derive_source("not-an-address") == UNKNOWN_SOURCE
    assert derive_source("") == UNKNOWN_SOURCE


# ---- seller agent (the Calculator's "who do I call") ----------------------


def test_seller_agent_is_the_display_name():
    assert derive_seller_agent("Momentum Capital <dispo@dfwinvestments.com>") == "Momentum Capital"


def test_seller_agent_unquotes_a_quoted_display_name():
    assert derive_seller_agent('"Acme Wholesale" <blast@acme.com>') == "Acme Wholesale"


def test_seller_agent_falls_back_to_the_address_when_there_is_no_name():
    # Blank would tell the operator nothing; the address is at least callable.
    assert derive_seller_agent("deals@investorlift.com") == "deals@investorlift.com"


def test_seller_agent_keeps_its_case():
    # A proper name shown in the Calculator UI, not a match key.
    assert derive_seller_agent("Reed Hunter <reed.hunter@newwestern.com>") == "Reed Hunter"


def test_seller_agent_of_an_empty_header_is_empty():
    assert derive_seller_agent("") == ""
    assert derive_seller_agent(None) == ""
