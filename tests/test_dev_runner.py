from __future__ import annotations

from net_razor_dev.runner import SERVICES


def test_dev_runner_uses_expected_local_ports() -> None:
    assert [(service.name, service.port) for service in SERVICES] == [
        ("orchestrator", 8010),
        ("x-api", 8011),
        ("hn-api", 8012),
        ("yt-api", 8013),
    ]
