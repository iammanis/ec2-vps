#!/usr/bin/env python3
"""
Regenerate the pricing dataset used by the site, straight from AWS's live public
pricing feeds (the same JSON documents that aws.amazon.com/*/pricing pages render).

No AWS credentials are needed -- every endpoint below is public and unauthenticated.

Outputs
-------
assets/data/pricing.json   full dataset consumed by assets/js/app.js
_data/rates.json           per-region reference rates rendered by Liquid
_data/meta.json            provenance / freshness metadata

Usage
-----
    python3 tools/build_pricing.py            # write into the repo
    python3 tools/build_pricing.py --check    # fetch + validate, write nothing
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

BASE = "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps"

#: AWS bills a "month" as 730 hours on its own pricing pages and calculator.
HOURS_PER_MONTH = 730

#: Only keep instances at or below this On-Demand hourly rate. Everything a
#: sub-$20/month budget could reach sits far below this; the headroom lets the
#: on-page budget slider go above $20 without the data running out.
MAX_HOURLY = 0.15

#: Regions the site covers, mapped to the display name AWS uses as the feed key.
REGIONS: dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "ca-central-1": "Canada (Central)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-north-1": "EU (Stockholm)",
    "eu-south-1": "EU (Milan)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-southeast-3": "Asia Pacific (Jakarta)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ap-east-1": "Asia Pacific (Hong Kong)",
    "sa-east-1": "South America (Sao Paulo)",
    "me-south-1": "Middle East (Bahrain)",
    "af-south-1": "Africa (Cape Town)",
    "il-central-1": "Israel (Tel Aviv)",
}

#: Short labels for the region picker.
REGION_SHORT: dict[str, str] = {
    "us-east-1": "N. Virginia",
    "us-east-2": "Ohio",
    "us-west-1": "N. California",
    "us-west-2": "Oregon",
    "ca-central-1": "Canada Central",
    "eu-west-1": "Ireland",
    "eu-west-2": "London",
    "eu-west-3": "Paris",
    "eu-central-1": "Frankfurt",
    "eu-north-1": "Stockholm",
    "eu-south-1": "Milan",
    "ap-south-1": "Mumbai",
    "ap-southeast-1": "Singapore",
    "ap-southeast-2": "Sydney",
    "ap-southeast-3": "Jakarta",
    "ap-northeast-1": "Tokyo",
    "ap-northeast-2": "Seoul",
    "ap-northeast-3": "Osaka",
    "ap-east-1": "Hong Kong",
    "sa-east-1": "Sao Paulo",
    "me-south-1": "Bahrain",
    "af-south-1": "Cape Town",
    "il-central-1": "Tel Aviv",
}

GEO = {
    "us-east-1": "North America", "us-east-2": "North America",
    "us-west-1": "North America", "us-west-2": "North America",
    "ca-central-1": "North America",
    "eu-west-1": "Europe", "eu-west-2": "Europe", "eu-west-3": "Europe",
    "eu-central-1": "Europe", "eu-north-1": "Europe", "eu-south-1": "Europe",
    "il-central-1": "Middle East & Africa", "me-south-1": "Middle East & Africa",
    "af-south-1": "Middle East & Africa",
    "ap-south-1": "Asia Pacific", "ap-southeast-1": "Asia Pacific",
    "ap-southeast-2": "Asia Pacific", "ap-southeast-3": "Asia Pacific",
    "ap-northeast-1": "Asia Pacific", "ap-northeast-2": "Asia Pacific",
    "ap-northeast-3": "Asia Pacific", "ap-east-1": "Asia Pacific",
    "sa-east-1": "South America",
}

#: Flat, all-regions-in-one-document feeds.
FLAT_FEEDS = {
    "vpc": "vpc/USD/current/vpc.json",
    "ebs": "ec2/USD/current/ebs.json",
    "natgateway": "ec2/USD/current/natgateway.json",
    "datatransfer": "datatransfer/USD/current/datatransfer.json",
}

#: rate name -> key inside the relevant flat feed.
RATE_KEYS = {
    "vpc": {
        "ipv4_in_use_hr": "Hourly charge for In use Public IPv4 Addresses per Hrs",
        "ipv4_idle_hr": "Hourly charge for Idle Public IPv4 Addresses per Hrs",
    },
    "ebs": {
        "ebs_gp3_gb_mo": "Storage General Purpose gp3 GB Mo",
        "ebs_gp2_gb_mo": "Storage General Purpose gp2 GB Mo",
        "ebs_gp3_iops_mo": "Provisioned EBS IOPS gp3 Volumes per IOPS Mo",
        "ebs_gp3_tput_gibps_mo": "Provisioned Throughput gp3 per GiBps mo",
        "ebs_magnetic_gb_mo": "Storage Magnetic standard GB Mo",
        "ebs_st1_gb_mo": "Storage Throughput Optimized HDD st1 GB Mo",
        "ebs_sc1_gb_mo": "Storage Cold HDD sc1 GB Mo",
        "snapshot_gb_mo": "Storage Snapshot Amazon S3 GB Mo",
    },
    "natgateway": {
        "nat_hr": "Hourly charge for NAT Gateways",
        "nat_gb": "Charge for per GB data processed by NatGateways",
    },
    "datatransfer": {
        "dto_gb": "DataTransfer External Outbound Next 10 TB",
        "dto_gb_next40": "DataTransfer External Outbound Next 40 TB",
        "dto_gb_next100": "DataTransfer External Outbound Next 100 TB",
        "dto_gb_over150": "DataTransfer External Outbound Greater than 150 TB",
        "dti_gb": "DataTransfer External Inbound",
    },
}

#: Charged outside the per-region feeds. Route 53 is a global service billed at a
#: flat rate per hosted zone (first 25 zones).
ROUTE53_HOSTED_ZONE_MO = 0.50
ROUTE53_PER_MILLION_QUERIES = 0.40

#: The free allowance AWS applies to internet egress, account-wide, every month.
FREE_EGRESS_GB = 100


def fmt(value: float | None, min_dp: int = 2, max_dp: int = 4) -> str:
    """Money-format a rate for display in templates.

    Liquid has no sprintf, so the builder emits ready-to-print strings. Keeps at
    least `min_dp` decimals and trims pointless trailing zeros beyond that, so
    0.1 -> '0.10', 0.08 -> '0.08', 0.0912 -> '0.0912', 40.96 -> '40.96'.
    """
    if value is None:
        return "—"
    text = f"{value:.{max_dp}f}"
    if "." in text:
        whole, frac = text.split(".")
        frac = frac.rstrip("0")
        while len(frac) < min_dp:
            frac += "0"
        text = f"{whole}.{frac}" if frac else whole
    return text

BURSTABLE_FAMILIES = {"t1", "t2", "t3", "t3a", "t4g"}

#: Instance types the written pages name explicitly. Priced per region so the prose
#: and the dataset can never disagree.
PICKS = ["t4g.nano", "t4g.micro", "t4g.small", "t3a.small", "t3.small", "t3.micro"]

FAMILY_RE = re.compile(r"^([a-z]+)(\d+)([a-z]*)$")


# --------------------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------------------

def fetch_json(url: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ec2-vps-pricing-builder (+https://github.com/iammanis/ec2-vps)",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def ec2_feed_url(location: str) -> str:
    loc = urllib.parse.quote(location)
    return (f"{BASE}/ec2/USD/current/ec2-ondemand-without-sec-sel/"
            f"{loc}/Linux/index.json")


def named_rates(feed: dict, location: str) -> dict[str, float]:
    """Return the human-readable rate map for one region out of a flat feed."""
    region_block = feed.get("regions", {}).get(location)
    if region_block is None:
        return {}
    return {k: float(v["price"]) for k, v in region_block.items() if "price" in v}


# --------------------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------------------

def parse_family(instance_type: str) -> str:
    return instance_type.split(".", 1)[0]


def is_graviton(instance_type: str) -> bool:
    """Graviton/arm64 families carry a 'g' in the suffix *after* the generation digit.

    m7g -> arm64, c8gn -> arm64, im4gn -> arm64, x2gd -> arm64,
    g5 -> x86 ('g' is the prefix, not the suffix), m6idn -> x86.
    """
    m = FAMILY_RE.match(parse_family(instance_type))
    if not m:
        return False
    return "g" in m.group(3)


def is_burstable(instance_type: str) -> bool:
    return parse_family(instance_type) in BURSTABLE_FAMILIES


def parse_memory_gib(text: str) -> float:
    m = re.search(r"([\d.]+)", text or "")
    return float(m.group(1)) if m else 0.0


def parse_vcpu(text: str) -> int:
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else 0


def local_storage(text: str) -> str:
    t = (text or "").strip()
    return "" if t.lower() in {"ebs only", ""} else t


def short_network(text: str) -> str:
    """'Up to 5 Gigabit' -> 'Up to 5 Gbps'; '10 Gigabit' -> '10 Gbps'."""
    t = (text or "").strip()
    t = t.replace("Megabit", "Mbps").replace("Gigabit", "Gbps")
    t = re.sub(r"\s+", " ", t)
    return t


# --------------------------------------------------------------------------------------
# Cost model (mirrored in assets/js/app.js -- keep the two in sync)
# --------------------------------------------------------------------------------------

def monthly_cost(hourly: float, rates: dict, *, hours: int = HOURS_PER_MONTH,
                 ebs_gb: float = 8, public_ipv4: int = 1,
                 route53_zone: bool = True, egress_gb: float = 20,
                 nat_gateway: bool = False, snapshot_gb: float = 0) -> dict:
    """Return the full monthly line-item breakdown for one instance."""
    compute = hourly * hours
    ipv4 = rates.get("ipv4_in_use_hr", 0.005) * hours * public_ipv4
    ebs = rates.get("ebs_gp3_gb_mo", 0.08) * ebs_gb
    snapshots = rates.get("snapshot_gb_mo", 0.05) * snapshot_gb
    dns = ROUTE53_HOSTED_ZONE_MO if route53_zone else 0.0
    billable_egress = max(0.0, egress_gb - FREE_EGRESS_GB)
    egress = billable_egress * rates.get("dto_gb", 0.09)
    nat = 0.0
    if nat_gateway:
        nat = rates.get("nat_hr", 0.045) * hours + rates.get("nat_gb", 0.045) * egress_gb
    total = compute + ipv4 + ebs + snapshots + dns + egress + nat
    return {
        "compute": round(compute, 4),
        "ipv4": round(ipv4, 4),
        "ebs": round(ebs, 4),
        "snapshots": round(snapshots, 4),
        "dns": round(dns, 4),
        "egress": round(egress, 4),
        "nat": round(nat, 4),
        "total": round(total, 4),
    }


#: The assumptions behind the headline "under $20" claim.
BASELINE = dict(hours=HOURS_PER_MONTH, ebs_gb=8, public_ipv4=1,
                route53_zone=True, egress_gb=20, nat_gateway=False, snapshot_gb=0)

BUDGET = 20.0


# --------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------

def build() -> tuple[dict, dict, dict]:
    print("Fetching flat (all-region) feeds ...", flush=True)
    flat = {}
    for name, path in FLAT_FEEDS.items():
        flat[name] = fetch_json(f"{BASE}/{path}")
        pub = flat[name].get("manifest", {}).get("hawkFilePublicationDate", "?")
        print(f"  {name:14} published {pub}")

    regions_out: list[dict] = []
    rates_out: dict[str, dict] = {}
    problems: list[str] = []

    for rid, location in REGIONS.items():
        # ---- reference rates for this region -------------------------------------
        rates: dict[str, float] = {}
        for feed_name, keymap in RATE_KEYS.items():
            available = named_rates(flat[feed_name], location)
            for out_key, feed_key in keymap.items():
                if feed_key in available:
                    rates[out_key] = round(available[feed_key], 6)
                else:
                    problems.append(f"{rid}: missing {feed_name}/{feed_key}")

        # ---- instances -----------------------------------------------------------
        try:
            feed = fetch_json(ec2_feed_url(location))
        except urllib.error.HTTPError as exc:
            problems.append(f"{rid}: EC2 feed HTTP {exc.code}")
            continue
        records = feed["regions"].get(location) or next(iter(feed["regions"].values()))

        instances = []
        for rec in records.values():
            itype = rec.get("Instance Type", "")
            try:
                hourly = float(rec["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if hourly <= 0 or hourly > MAX_HOURLY:
                continue
            if itype.startswith("mac"):          # Dedicated Host only, never a cheap VPS
                continue
            instances.append({
                "t": itype,
                "h": round(hourly, 6),
                "v": parse_vcpu(rec.get("vCPU")),
                "m": parse_memory_gib(rec.get("Memory")),
                "f": rec.get("Instance Family", ""),
                "n": short_network(rec.get("Network Performance")),
                "d": local_storage(rec.get("Storage")),
                "a": "arm64" if is_graviton(itype) else "x86_64",
                "b": is_burstable(itype),
            })
        instances.sort(key=lambda r: (r["h"], r["t"]))

        fits = [i for i in instances
                if monthly_cost(i["h"], rates, **BASELINE)["total"] < BUDGET]
        cheapest = instances[0] if instances else None

        regions_out.append({
            "id": rid,
            "name": location,
            "short": REGION_SHORT.get(rid, rid),
            "geo": GEO.get(rid, "Other"),
            "rates": rates,
            "instances": instances,
        })

        summary = {
            "id": rid,
            "name": location,
            "short": REGION_SHORT.get(rid, rid),
            "geo": GEO.get(rid, "Other"),
            **rates,
            "ipv4_mo": round(rates.get("ipv4_in_use_hr", 0) * HOURS_PER_MONTH, 2),
            "nat_mo": round(rates.get("nat_hr", 0) * HOURS_PER_MONTH, 2),
            "fixed_overhead_mo": round(
                rates.get("ipv4_in_use_hr", 0) * HOURS_PER_MONTH
                + rates.get("ebs_gp3_gb_mo", 0) * BASELINE["ebs_gb"]
                + ROUTE53_HOSTED_ZONE_MO, 2),
            "under_budget_count": len(fits),
            "cheapest_type": cheapest["t"] if cheapest else None,
            "cheapest_total_mo": (
                round(monthly_cost(cheapest["h"], rates, **BASELINE)["total"], 2)
                if cheapest else None),
        }
        # Named instances the prose pages refer to, priced live so the narrative can
        # never drift away from the dataset.
        by_type = {i["t"]: i for i in instances}
        picks = {}
        for want in PICKS:
            inst = by_type.get(want)
            if not inst:
                continue
            b = monthly_cost(inst["h"], rates, **BASELINE)
            picks[want] = {
                "hourly": fmt(inst["h"], 4, 4),
                "vcpu": inst["v"],
                "mem": (f"{int(inst['m'] * 1024)} MiB" if inst["m"] < 1
                        else f"{inst['m']:g} GiB"),
                "arch": inst["a"],
                "compute_mo": fmt(b["compute"], 2, 2),
                "total_mo": fmt(b["total"], 2, 2),
                "under": b["total"] < BUDGET,
            }
        summary["picks"] = picks

        # Pre-formatted display strings: Liquid cannot sprintf, and "$0.1" looks broken.
        compute_budget = BUDGET - summary["fixed_overhead_mo"]
        summary["f"] = {
            "ipv4_hr": fmt(rates.get("ipv4_in_use_hr"), 3, 4),
            "ipv4_idle_hr": fmt(rates.get("ipv4_idle_hr"), 3, 4),
            "ipv4_mo": fmt(summary["ipv4_mo"], 2, 2),
            "nat_hr": fmt(rates.get("nat_hr"), 3, 4),
            "nat_gb": fmt(rates.get("nat_gb"), 3, 4),
            "nat_mo": fmt(summary["nat_mo"], 2, 2),
            "gp3": fmt(rates.get("ebs_gp3_gb_mo")),
            "gp2": fmt(rates.get("ebs_gp2_gb_mo")),
            "gp3_iops": fmt(rates.get("ebs_gp3_iops_mo"), 3, 4),
            "gp3_tput": fmt(rates.get("ebs_gp3_tput_gibps_mo"), 2, 2),
            "magnetic": fmt(rates.get("ebs_magnetic_gb_mo")),
            "st1": fmt(rates.get("ebs_st1_gb_mo")),
            "sc1": fmt(rates.get("ebs_sc1_gb_mo")),
            "snapshot": fmt(rates.get("snapshot_gb_mo")),
            "dto": fmt(rates.get("dto_gb")),
            "dto40": fmt(rates.get("dto_gb_next40")),
            "dto100": fmt(rates.get("dto_gb_next100")),
            "dto150": fmt(rates.get("dto_gb_over150")),
            "fixed_overhead_mo": fmt(summary["fixed_overhead_mo"], 2, 2),
            "cheapest_total_mo": fmt(summary["cheapest_total_mo"], 2, 2),
            "ebs8": fmt(rates.get("ebs_gp3_gb_mo", 0) * 8, 2, 2),
            "ebs30": fmt(rates.get("ebs_gp3_gb_mo", 0) * 30, 2, 2),
            "gp2_8": fmt(rates.get("ebs_gp2_gb_mo", 0) * 8, 2, 2),
            "gp2_30": fmt(rates.get("ebs_gp2_gb_mo", 0) * 30, 2, 2),
            "mag8": fmt(rates.get("ebs_magnetic_gb_mo", 0) * 8, 2, 2),
            "mag30": fmt(rates.get("ebs_magnetic_gb_mo", 0) * 30, 2, 2),
            "compute_budget_mo": fmt(compute_budget, 2, 2),
            "max_hourly_rate": fmt(compute_budget / HOURS_PER_MONTH, 4, 4),
        }

        rates_out[rid] = summary
        print(f"  {rid:16} {len(instances):4} instances <= ${MAX_HOURLY}/h, "
              f"{len(fits):4} under ${BUDGET:.0f}/mo, "
              f"cheapest {summary['cheapest_type']} @ ${summary['cheapest_total_mo']}")

    generated = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    pubdates = {n: flat[n].get("manifest", {}).get("hawkFilePublicationDate")
                for n in FLAT_FEEDS}

    pricing = {
        "generated_utc": generated.isoformat(),
        "hours_per_month": HOURS_PER_MONTH,
        "budget_usd": BUDGET,
        "max_hourly_included": MAX_HOURLY,
        "free_egress_gb": FREE_EGRESS_GB,
        "route53_hosted_zone_mo": ROUTE53_HOSTED_ZONE_MO,
        "route53_per_million_queries": ROUTE53_PER_MILLION_QUERIES,
        "baseline": BASELINE,
        "feed_publication_dates": pubdates,
        "regions": regions_out,
    }

    meta = {
        "generated_utc": generated.isoformat(),
        "generated_human": generated.strftime("%d %b %Y %H:%M UTC"),
        "hours_per_month": HOURS_PER_MONTH,
        "budget_usd": int(BUDGET),
        "free_egress_gb": FREE_EGRESS_GB,
        "route53_hosted_zone_mo": fmt(ROUTE53_HOSTED_ZONE_MO, 2, 2),
        "route53_per_million_queries": fmt(ROUTE53_PER_MILLION_QUERIES, 2, 2),
        "max_hourly_included": fmt(MAX_HOURLY, 2, 2),
        "region_count": len(regions_out),
        "instance_row_count": sum(len(r["instances"]) for r in regions_out),
        "feed_publication_dates": pubdates,
        "baseline": BASELINE,
        "problems": problems,
    }

    if problems:
        print("\nWARNINGS:")
        for p in problems:
            print("  -", p)

    return pricing, rates_out, meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="fetch and validate but do not write any files")
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pricing, rates, meta = build()

    if not pricing["regions"]:
        print("ERROR: no regions were built", file=sys.stderr)
        return 1

    if args.check:
        print("\n--check: no files written.")
        return 0

    # Liquid cannot sort a hash by a nested property, so also emit a pre-sorted
    # list for templates to iterate directly.
    regions_sorted = sorted(
        rates.values(),
        key=lambda s: (s["cheapest_total_mo"] is None, s["cheapest_total_mo"]),
    )

    targets = {
        os.path.join(repo, "assets", "data", "pricing.json"): pricing,
        os.path.join(repo, "_data", "rates.json"): rates,
        os.path.join(repo, "_data", "regions.json"): regions_sorted,
        os.path.join(repo, "_data", "meta.json"): meta,
    }
    for path, payload in targets.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"), sort_keys=False)
            fh.write("\n")
        print(f"wrote {os.path.relpath(path, repo)} "
              f"({os.path.getsize(path) / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
