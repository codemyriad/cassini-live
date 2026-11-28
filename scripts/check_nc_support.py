#!/usr/bin/env python3
"""Validate advertised Nextcloud support against the pinned Cassini harness inventory.

ci/cassini-harness.lock names the exact gocassini commit whose harness boots the
compatibility stacks. That commit's ci/nextcloud-compatibility.json is the single
source of truth for which Nextcloud majors are supported and tested:

  check     the manifest's <nextcloud> bounds must equal the inventory bounds,
            so the advertised and the tested support window cannot drift apart
  matrix    the GitHub Actions baseline matrix for a tier ("pr": reference plus
            the exact minimum; "all": every locked baseline)

Standard library only, so CI runs it before installing anything.
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "ci/cassini-harness.lock"
MANIFESTS = ("appinfo/info.xml",)


def read_lock(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    assert re.fullmatch(r"[\w.-]+/[\w.-]+", values.get("repo", "")), "lock: invalid repo"
    assert re.fullmatch(r"[0-9a-f]{40}", values.get("sha", "")), "lock: sha must be a full commit"
    return values


def fetch_inventory(lock):
    url = (f"https://raw.githubusercontent.com/{lock['repo']}/{lock['sha']}"
           "/ci/nextcloud-compatibility.json")
    with urlopen(url, timeout=60) as response:
        return json.load(response)


def major(value):
    assert isinstance(value, str) and re.fullmatch(r"\d+(\.\d+\.\d+)?", value), \
        f"unexpected version: {value!r}"
    return int(value.split(".")[0])


def validate(policy):
    """The pinned inventory invariants this repo relies on (gocassini checks more)."""
    assert policy["schema"] == 1, "unsupported inventory schema"
    floor = major(policy["minimum"])
    ceiling = policy["maximum_major"]
    assert type(ceiling) is int and ceiling >= floor, "invalid support bounds"
    baselines = policy["baselines"]
    assert baselines, "no locked baselines"
    majors = {major(stack["nextcloud_version"]) for stack in baselines}
    assert majors == set(range(floor, ceiling + 1)), \
        "a supported major has no locked baseline (or the range has a hole)"
    identifiers = [stack["id"] for stack in baselines]
    assert len(identifiers) == len(set(identifiers)), "duplicate baseline ID"
    assert policy["reference"] in identifiers, "reference is not a locked baseline"
    assert policy["minimum"] in [stack["nextcloud_version"] for stack in baselines], \
        "the exact advertised minimum has no locked baseline"
    return floor, ceiling


def check(policy):
    floor, ceiling = validate(policy)
    for name in MANIFESTS:
        nextcloud = ET.parse(ROOT / name).getroot().find("dependencies/nextcloud")
        assert nextcloud is not None, f"{name}: missing <dependencies><nextcloud>"
        for attribute, bound in (("min-version", floor), ("max-version", ceiling)):
            value = nextcloud.get(attribute)
            assert value is not None and major(value) == bound, (
                f"{name}: {attribute} {value!r} does not match the pinned inventory "
                f"(minimum {policy['minimum']}, maximum major {ceiling}); update the "
                "manifest or move ci/cassini-harness.lock to a commit that tests it")
    print(f"Advertised Nextcloud {floor}..{ceiling} matches the pinned harness "
          f"inventory ({len(policy['baselines'])} locked baselines)")


def matrix(policy, tier):
    validate(policy)
    baselines = policy["baselines"]
    if tier == "pr":
        keep = {policy["reference"]} | {
            stack["id"] for stack in baselines
            if stack["nextcloud_version"] == policy["minimum"]}
        baselines = [stack for stack in baselines if stack["id"] in keep]
    print(json.dumps({"include": [{"baseline": stack["id"]} for stack in baselines]},
                     separators=(",", ":")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", default=str(LOCK))
    parser.add_argument("--inventory", help="local inventory JSON instead of fetching the pin")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("check")
    tiers = actions.add_parser("matrix")
    tiers.add_argument("--tier", choices=["pr", "all"], required=True)
    args = parser.parse_args(argv)
    if args.inventory:
        policy = json.loads(Path(args.inventory).read_text())
    else:
        policy = fetch_inventory(read_lock(args.lock))
    if args.action == "check":
        check(policy)
    else:
        matrix(policy, args.tier)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        sys.exit(f"nc-support: {exc}")
