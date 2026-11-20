"""Unit tests for scripts/digitalocean/check_firewall.py.

Pure rule-checking functions only: no network calls, no DO_TOKEN needed.
"""

from scripts.digitalocean.check_firewall import (
    check_droplet,
    find_ssh_open_to_world,
    firewalls_for_droplet,
    format_firewall_summary,
    port_range_covers,
    ssh_open_to_world,
)


def _firewall(name="test-firewall", droplet_ids=(602517171,), inbound=None, outbound=None):
    return {
        "id": "fw-1",
        "name": name,
        "status": "succeeded",
        "droplet_ids": list(droplet_ids),
        "inbound_rules": inbound if inbound is not None else [],
        "outbound_rules": outbound if outbound is not None else [],
    }


def _ssh_rule(addresses):
    return {
        "protocol": "tcp",
        "port_range": "22",
        "sources": {"addresses": list(addresses)},
    }


def test_port_range_covers_single_port():
    assert port_range_covers("22", 22)
    assert not port_range_covers("22", 80)


def test_port_range_covers_range():
    assert port_range_covers("20-25", 22)
    assert not port_range_covers("20-25", 26)
    assert port_range_covers("8000-9000", 7844) is False


def test_port_range_covers_all_and_garbage():
    assert port_range_covers("all", 22)
    assert not port_range_covers("not-a-port", 22)
    assert not port_range_covers(None, 22)


def test_ssh_open_to_world_ipv4():
    assert ssh_open_to_world(_ssh_rule(["0.0.0.0/0"]))


def test_ssh_open_to_world_ipv6():
    assert ssh_open_to_world(_ssh_rule(["::/0"]))


def test_ssh_open_to_world_range_covering_22():
    rule = _ssh_rule(["0.0.0.0/0"])
    rule["port_range"] = "20-25"
    assert ssh_open_to_world(rule)


def test_ssh_not_open_when_restricted():
    assert not ssh_open_to_world(_ssh_rule(["203.0.113.10/32"]))


def test_ssh_not_open_for_other_ports_or_protocols():
    http_rule = _ssh_rule(["0.0.0.0/0"])
    http_rule["port_range"] = "80"
    assert not ssh_open_to_world(http_rule)

    udp_rule = _ssh_rule(["0.0.0.0/0"])
    udp_rule["protocol"] = "udp"
    assert not ssh_open_to_world(udp_rule)


def test_find_ssh_open_to_world_lists_only_offending_rules():
    firewall = _firewall(
        inbound=[
            _ssh_rule(["0.0.0.0/0"]),
            {"protocol": "tcp", "port_range": "443", "sources": {"addresses": ["0.0.0.0/0"]}},
        ]
    )
    offending = find_ssh_open_to_world(firewall)
    assert len(offending) == 1
    assert offending[0]["port_range"] == "22"


def test_droplet_with_no_attached_firewall_fails():
    failures = check_droplet([], 602517171)
    assert len(failures) == 1
    assert "602517171" in failures[0]
    assert "no firewall attached" in failures[0]


def test_droplet_with_firewall_elsewhere_fails():
    other = _firewall(droplet_ids=(603469248,))
    failures = check_droplet([other], 602517171)
    assert len(failures) == 1
    assert "no firewall attached" in failures[0]


def test_droplet_with_clean_firewall_passes():
    firewall = _firewall(
        inbound=[
            _ssh_rule(["203.0.113.10/32"]),
            {"protocol": "tcp", "port_range": "443", "sources": {"addresses": ["0.0.0.0/0"]}},
        ]
    )
    assert check_droplet([firewall], 602517171) == []


def test_firewalls_for_droplet_filters_by_id():
    live = _firewall(name="live-fw", droplet_ids=(602517171,))
    v2 = _firewall(name="v2-fw", droplet_ids=(603469248,))
    assert firewalls_for_droplet([live, v2], 602517171) == [live]
    assert firewalls_for_droplet([live, v2], 603469248) == [v2]
    assert firewalls_for_droplet([live, v2], 999) == []


def test_format_firewall_summary_includes_name_droplets_and_rules():
    firewall = _firewall(
        inbound=[_ssh_rule(["203.0.113.10/32"])],
        outbound=[
            {
                "protocol": "tcp",
                "port_range": "443",
                "destinations": {"addresses": ["0.0.0.0/0", "::/0"]},
            }
        ],
    )
    summary = format_firewall_summary(firewall, ssh_allowed_cidr="203.0.113.10/32")
    assert "test-firewall" in summary
    assert "602517171" in summary
    assert "tcp 22 from 203.0.113.10/32" in summary
    assert "tcp 443 to 0.0.0.0/0, ::/0" in summary
    assert "203.0.113.10/32" in summary
