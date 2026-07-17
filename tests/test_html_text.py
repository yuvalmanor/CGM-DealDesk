"""HTML body -> text conversion (pure).

Table cases over the shapes real Source HTML actually takes: table-based
marketing layouts, entity noise, mso conditional comments, and the script/style
bulk that must never reach the AI rung.
"""

import pytest

from dealdesk.html_text import html_to_text


def test_strips_tags_and_keeps_visible_text():
    assert html_to_text("<p>Asking Price: $250,000</p>") == "Asking Price: $250,000"


def test_table_cells_stay_on_one_line_beside_their_label():
    # The heuristics' label-then-value patterns allow only a short gap between
    # a label and its value, so adjacent cells must not be split by a newline.
    html = "<table><tr><td>Cash Price</td><td>$97,500</td></tr></table>"
    assert "Cash Price $97,500" in html_to_text(html)


def test_rows_become_separate_lines():
    html = "<table><tr><td>Year Built</td><td>1985</td></tr><tr><td>Beds</td><td>2</td></tr></table>"
    lines = [l for l in html_to_text(html).split("\n") if l]
    assert lines == ["Year Built 1985", "Beds 2"]


def test_br_and_block_tags_break_lines():
    assert html_to_text("<div>123 Main St<br/>Dallas, TX</div>") == "123 Main St\nDallas, TX"


def test_script_and_style_content_never_leaks():
    html = """
    <html><head><style>.es-button { color: red; }</style><title>Ignore me</title></head>
    <body><script>var price = 999;</script><p>Cash Price: $97,500</p></body></html>
    """
    text = html_to_text(html)
    assert text == "Cash Price: $97,500"
    assert "es-button" not in text and "var price" not in text and "Ignore me" not in text


def test_href_bulk_is_dropped():
    # Attribute values are never text nodes — a marketing email's tracking URLs
    # must not reach the AI rung as tokens.
    html = '<a href="https://track.example.com/x?utm=aaaaaaaaaaaaaaaaaaaa">View in Marketplace</a>'
    assert html_to_text(html) == "View in Marketplace"


def test_entities_are_unescaped_and_nbsp_normalized():
    assert html_to_text("<p>Price:&nbsp;$97,500 &amp; up &#8212; cheap</p>") == (
        "Price: $97,500 & up — cheap"
    )


def test_mso_conditional_comments_are_dropped():
    html = "<!--[if gte mso 9]><style>sup { font-size: 100%; }</style><![endif]--><p>Beds 2</p>"
    assert html_to_text(html) == "Beds 2"


def test_whitespace_is_collapsed():
    html = "<p>Cash     Price:\t\t$97,500</p>\n\n\n<p></p>\n<p></p>\n<p>Year Built 1985</p>"
    assert html_to_text(html) == "Cash Price: $97,500\n\nYear Built 1985"


@pytest.mark.parametrize("raw", ["", "   ", "\n\n"])
def test_blank_input_yields_empty_string(raw):
    assert html_to_text(raw) == ""


def test_malformed_markup_is_best_effort_not_fatal():
    # A broken Email must not sink its run — it would only park in Error forever.
    assert "Cash Price: $97,500" in html_to_text("<p>Cash Price: $97,500</b></unclosed <<>")


def test_conversion_shrinks_a_marketing_email():
    # Cost, not tidiness: the AI rung is billed per token.
    html = "<html><head><style>" + ("a{color:red}" * 500) + "</style></head><body><p>Beds 2</p></body></html>"
    assert html_to_text(html) == "Beds 2"
