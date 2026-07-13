from dealdesk.discovery import tally_sources
from dealdesk.models import MessageMeta


def _msg(from_addr, i=0):
    return MessageMeta(id=f"m{i}", from_addr=from_addr, subject="", date="")


def test_empty_input_yields_empty_tally():
    assert tally_sources([]) == []


def test_ranked_highest_volume_first():
    msgs = [
        _msg("a@big.com", 1),
        _msg("b@big.com", 2),
        _msg("c@big.com", 3),
        _msg("a@small.com", 4),
    ]
    tally = tally_sources(msgs)
    assert [sc.source for sc in tally] == ["big.com", "small.com"]
    assert tally[0].count == 3
    assert tally[1].count == 1


def test_percentages_and_cumulative():
    msgs = [_msg("a@big.com", 1), _msg("b@big.com", 2), _msg("a@small.com", 3)]
    tally = tally_sources(msgs)
    assert tally[0].source == "big.com"
    assert tally[0].pct == 66.7
    assert tally[0].cumulative_pct == 66.7
    assert tally[1].pct == 33.3
    assert tally[1].cumulative_pct == 100.0


def test_ties_break_alphabetically():
    msgs = [_msg("x@zebra.com", 1), _msg("y@apple.com", 2)]
    tally = tally_sources(msgs)
    assert [sc.source for sc in tally] == ["apple.com", "zebra.com"]
