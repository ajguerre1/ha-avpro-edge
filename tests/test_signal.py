"""Signal presence: three answers, and the one the hardware taught us.

``signal_present`` exists because every consumer of a signal field used to ask ``bool(raw)``, and
``bool`` has two answers where this needs three. It was wrong in both directions, and the second
one took real hardware to find.

**Wrong once:** ``bool(None)`` is ``False``, so a port never read reported *Disconnected*.

**Wrong again:** ``bool("NO SIGNAL")`` is ``True``, so a port the matrix explicitly reported as
dark read *Connected*. That is the one case the entity exists for, and it was inverted.

The reason the suite never caught the second is in ``tools/fake_avpro.py``: it modelled a dark port
as an empty string, which is not what an AC-MX44-AUHD does. Measured 2026-08-29 by unplugging a
source -- ``NO SIGNAL``, in words, and the format string back on replug.
"""

from __future__ import annotations

import pytest
from avpro.models import NO_SIGNAL_TOKENS, signal_present

# ---------------------------------------------------------------------------------------------
# The three answers
# ---------------------------------------------------------------------------------------------


def test_a_port_never_read_is_unknown() -> None:
    """Absence of a reading, not a reading of absence."""
    assert signal_present(None) is None


def test_the_measured_no_signal_token_is_false() -> None:
    """The string the live matrix actually sends for a port with the cable out."""
    assert signal_present("NO SIGNAL") is False


def test_a_real_format_string_is_true() -> None:
    assert signal_present("3840X2160P@60HZ YUV420") is True
    assert signal_present("1920X1080P@60HZ") is True


# ---------------------------------------------------------------------------------------------
# The two bugs, pinned so neither can come back
# ---------------------------------------------------------------------------------------------


def test_it_disagrees_with_bool_in_exactly_the_two_places_that_were_wrong() -> None:
    """Stated as a comparison, because ``bool`` is what every call site used to do.

    Both disagreements are the whole point of the function. If a change ever made these agree
    again, it would have reintroduced one of the two defects.
    """
    assert bool(None) is False and signal_present(None) is None
    assert bool("NO SIGNAL") is True and signal_present("NO SIGNAL") is False


def test_the_token_check_ignores_case_and_surrounding_space() -> None:
    """The field is split out of a ``&``-delimited body; nothing guarantees it arrives trimmed."""
    for spelling in ("no signal", "No Signal", "  NO SIGNAL  ", "\tNO SIGNAL\r"):
        assert signal_present(spelling) is False, spelling


# ---------------------------------------------------------------------------------------------
# What an unrecognised answer must do
# ---------------------------------------------------------------------------------------------


def test_an_unfamiliar_string_reads_as_present_rather_than_absent() -> None:
    """The asymmetry is deliberate, and it is about which lie an automation acts on.

    This is one firmware on one model, and the AUHD family does not share a vocabulary -- another
    unit may spell darkness differently. Reporting a dark port as live is a smaller error than
    reporting a live port as dark, because only the second makes "if no signal then..." fire
    against a working display.
    """
    for unfamiliar in ("NO SYNC", "NONE", "---", "UNKNOWN FORMAT"):
        assert signal_present(unfamiliar) is True, unfamiliar


def test_an_empty_field_is_unknown_not_dark() -> None:
    """Not observed on this firmware, which is exactly why it is not treated as darkness.

    The device says ``NO SIGNAL`` rather than sending an empty field, so an empty one is a body we
    do not understand. Calling that "no signal" would be inventing a second vocabulary for the
    device -- the same guess that produced the first version of this test suite.
    """
    assert signal_present("") is None
    assert signal_present("   ") is None


def test_the_token_set_is_not_silently_empty() -> None:
    """Guards every check above: an empty set would make them all pass by accident."""
    assert NO_SIGNAL_TOKENS
    assert all(token == token.upper() for token in NO_SIGNAL_TOKENS)


# ---------------------------------------------------------------------------------------------
# The fake has to send what the hardware sends
# ---------------------------------------------------------------------------------------------


def test_the_fake_models_darkness_the_way_the_matrix_reports_it() -> None:
    """The fidelity check that would have caught this a week earlier.

    The fake used ``""`` for its dark port. Every consumer tested truthiness, empty strings are
    falsy, and so the suite agreed with a device that does not exist -- the same failure as the
    ``TMDSDivSta`` tab it once served on a firmware without one.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from fake_avpro import MatrixModel

    dark = [s for s in MatrixModel().signals if signal_present(s) is not True]
    assert dark, "the fake has no dark port, so nothing exercises the absent case"
    assert all(s.strip().upper() in NO_SIGNAL_TOKENS for s in dark), (
        f"the fake invents its own spelling of darkness: {dark}"
    )


@pytest.mark.parametrize("index", [0, 1, 3])
def test_the_fakes_other_ports_are_live(index: int) -> None:
    """So a test asserting "some ports are live" is not passing vacuously."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from fake_avpro import MatrixModel

    assert signal_present(MatrixModel().signals[index]) is True
