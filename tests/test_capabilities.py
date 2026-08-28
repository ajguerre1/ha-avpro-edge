"""Capability discovery.

The rule under test: a missing endpoint is a fact about this firmware, recorded once and never
re-probed, and it must never look like a failure.
"""

from __future__ import annotations

from avpro.capabilities import Capabilities
from avpro.protocol import CommandEndpoint, StatusEndpoint


def test_everything_is_assumed_available_until_proven_otherwise() -> None:
    caps = Capabilities()
    assert all(caps.endpoint_available(e) for e in StatusEndpoint)
    assert all(caps.command_available(e) for e in CommandEndpoint)


def test_recording_an_absent_endpoint_rules_it_out() -> None:
    caps = Capabilities().with_absent(StatusEndpoint.TMDS)
    assert not caps.endpoint_available(StatusEndpoint.TMDS)
    assert caps.endpoint_available(StatusEndpoint.VIDEO)


def test_recording_an_unsupported_command_rules_it_out() -> None:
    caps = Capabilities().with_unsupported(CommandEndpoint.TMDS)
    assert not caps.command_available(CommandEndpoint.TMDS)
    assert caps.command_available(CommandEndpoint.VIDEO)


def test_findings_are_sticky_across_accumulation() -> None:
    caps = (
        Capabilities()
        .with_absent(StatusEndpoint.TMDS)
        .with_unsupported(CommandEndpoint.EDID)
        .with_absent(StatusEndpoint.EDID)
    )
    assert not caps.endpoint_available(StatusEndpoint.TMDS)
    assert not caps.endpoint_available(StatusEndpoint.EDID)
    assert not caps.command_available(CommandEndpoint.EDID)


def test_recording_the_same_finding_twice_returns_the_same_object() -> None:
    """Cheap identity check that re-probing cannot churn the value."""
    caps = Capabilities().with_absent(StatusEndpoint.TMDS)
    assert caps.with_absent(StatusEndpoint.TMDS) is caps


def test_capabilities_are_value_comparable() -> None:
    assert Capabilities().with_absent(StatusEndpoint.TMDS) == Capabilities().with_absent(
        StatusEndpoint.TMDS
    )


def test_recording_does_not_mutate_the_original() -> None:
    original = Capabilities()
    original.with_absent(StatusEndpoint.TMDS)
    assert original.endpoint_available(StatusEndpoint.TMDS)


def test_diagnostics_report_paths_only() -> None:
    """Diagnostics get pasted into public issues; only endpoint paths may appear."""
    caps = Capabilities().with_absent(StatusEndpoint.TMDS).with_unsupported(CommandEndpoint.TMDS)
    report = caps.as_diagnostics()
    assert report == {
        "absent_endpoints": ["TMDSDivSta.CGI"],
        "unsupported_commands": ["TmdsSendCmd.CGI"],
    }


def test_diagnostics_are_stable_and_sorted() -> None:
    a = Capabilities().with_absent(StatusEndpoint.TMDS).with_absent(StatusEndpoint.EDID)
    b = Capabilities().with_absent(StatusEndpoint.EDID).with_absent(StatusEndpoint.TMDS)
    assert a.as_diagnostics() == b.as_diagnostics()
