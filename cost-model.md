---
layout: default
permalink: /cost-model/
title: The complete EC2 VPS cost model
layout_title: The complete cost model
subtitle: Every line item that lands on the bill when you run one small EC2 server — where each rate comes from, and which ones people forget.
prose: true
---

{%- assign r = site.data.rates['us-east-1'] -%}
{%- assign m = site.data.meta -%}

<div class="toc" markdown="0">
  <strong>On this page</strong>
  <ol>
    <li><a href="#answer">The short answer</a></li>
    <li><a href="#compute">EC2 compute</a></li>
    <li><a href="#ipv4">Public IPv4 addresses</a></li>
    <li><a href="#ebs">EBS storage</a></li>
    <li><a href="#transfer">Data transfer</a></li>
    <li><a href="#nat-gateway">NAT Gateway</a></li>
    <li><a href="#dns">DNS</a></li>
    <li><a href="#free">What costs nothing</a></li>
    <li><a href="#surprises">The usual surprises</a></li>
    <li><a href="#budget">The $20 arithmetic</a></li>
    <li><a href="#cheaper">Going cheaper still</a></li>
    <li><a href="#methodology">Methodology</a></li>
  </ol>
</div>

## The short answer {#answer}

A permanently-on, SSH-reachable Linux VPS on EC2 has exactly **four** unavoidable cost
components, plus two optional ones people bolt on by accident.

| # | Component | Avoidable? | {{ r.short }} rate | Monthly at baseline |
|---|---|---|---|---|
| 1 | EC2 instance compute | No | varies by type | from ${{ r.f.cheapest_total_mo }} total |
| 2 | One public IPv4 address | Only by going IPv6-only | ${{ r.f.ipv4_hr }}/hr | **${{ r.f.ipv4_mo }}** |
| 3 | EBS root volume (gp3) | No | ${{ r.f.gp3 }}/GB-mo | **${{ r.f.ebs8 }}** at 8 GB |
| 4 | DNS hosting for the name | Yes, if your registrar does DNS | ${{ m.route53_hosted_zone_mo }}/zone-mo | **${{ m.route53_hosted_zone_mo }}** |
| 5 | Data transfer out | Mostly — 100 GB/mo is free | ${{ r.f.dto }}/GB | $0 at light use |
| 6 | NAT Gateway | **Yes — don't provision one** | ${{ r.f.nat_hr }}/hr | ${{ r.f.nat_mo }} ⚠️ |

Items 2–4 add up to a **fixed ${{ r.f.fixed_overhead_mo }}/month floor in {{ r.short }} before
you have run a single CPU cycle.** That floor is the whole reason this site exists: it means your
real compute budget is not $20, it is **${{ r.f.compute_budget_mo }}**.

<div class="callout" markdown="1">
**AWS bills a month as 730 hours** on its own pricing pages and in the AWS Pricing Calculator
(365 × 24 ÷ 12 = 730). Every monthly figure on this site uses 730 hours, so the numbers line up
with what the calculator tells you. A 31-day month is actually 744 hours, so treat monthly
figures as ±2%.
</div>

## 1 · EC2 compute {#compute}

On-Demand is billed **per second, with a 60-second minimum**, at a fixed published rate for the
instance type, region, and operating system. There is no minimum term and no upfront cost.

Three things decide how cheap you can go:

**Instance family.** For a small VPS you want the **T family** (`t2`, `t3`, `t3a`, `t4g`) — the
burstable line. They are dramatically cheaper than fixed-performance families because you are
buying a *fraction* of a core plus a credit balance rather than dedicated CPU.

**Architecture.** `t4g` instances run on AWS Graviton (arm64) and are consistently the cheapest
option in every region — roughly 20% below the equivalent `t3`. Every mainstream distro and most
server software has native arm64 builds now. If you have no x86-only binaries, `t4g` is the
default choice. `t3a` (AMD) sits in the middle.

**Region.** The spread is large. The same `t4g.nano` costs
${{ site.data.rates['ap-south-1'].f.cheapest_total_mo }} all-in in Mumbai and
${{ site.data.rates['sa-east-1'].f.cheapest_total_mo }} in São Paulo — about 48% more. Pick the
cheapest region whose latency you can live with.

### Burstable CPU credits — the one real catch

A `t4g.nano` does not give you two full vCPUs. It earns **CPU credits** at a fixed rate and
spends them when it runs above its baseline (5% per vCPU for `nano`, 10% for `micro`, 20% for
`small`). Idle or lightly loaded, you bank credits. Under sustained load you drain them, and
then one of two things happens:

- **Standard mode** — the instance is throttled to its baseline. Slow, but free.
- **Unlimited mode** — the instance keeps full speed and you are charged a **surcharge for
  surplus credits** on top of the instance rate. `t3`/`t4g` default to *unlimited*.

<div class="callout warn" markdown="1">
**This is the one line item that can silently exceed the estimates on this site.** A runaway
process on a `t4g.nano` in unlimited mode bills surplus vCPU-hours indefinitely. If a hard
ceiling matters more than performance, set the instance's credit specification to `standard`:

```bash
aws ec2 modify-instance-credit-specification \
  --instance-id i-0123456789abcdef0 \
  --instance-credit-specification '{"CpuCredits":"standard"}'
```
</div>

## 2 · Public IPv4 addresses {#ipv4}

**Since 1 February 2024, AWS charges for every public IPv4 address, in use or not.** This is the
change that broke every "run EC2 for $3/month" blog post written before 2024, and it is the
single largest fixed cost in a small VPS build.

| Address state | {{ r.short }} rate | Per 730-hr month |
|---|---|---|
| Attached to a running instance | ${{ r.f.ipv4_hr }}/hr | **${{ r.f.ipv4_mo }}** |
| Elastic IP allocated but idle | ${{ r.f.ipv4_idle_hr }}/hr | ${{ r.f.ipv4_mo }} |

Note the second row. **An Elastic IP you are not using costs exactly the same as one you are.**
Releasing unused EIPs is free money. The rate is uniform at $0.005/hour across every commercial
region in this dataset.

### Auto-assigned public IP vs Elastic IP

Both cost the same $0.005/hour. The difference is stability:

- **Auto-assigned public IPv4** — handed out when the instance launches, **released and replaced
  on every stop/start.** Your DNS record goes stale the first time you reboot the host.
- **Elastic IP (EIP)** — allocated to your account and stays yours until you release it. This is
  what you want for `myvps.domain.com`, because a static A record only works with a static
  address.

Use an Elastic IP, and remember to *release* it — not just disassociate it — when you tear the
server down.

### The IPv6 escape hatch

**IPv6 addresses are free on EC2.** A dual-stack or IPv6-only instance with an
egress-only internet gateway costs nothing for addressing, saving the full
${{ r.f.ipv4_mo }}/month.

The catch is real, though: you can only `ssh` to an IPv6-only host from a network that has IPv6
connectivity. Plenty of mobile networks, corporate LANs, hotel Wi-Fi and coffee shops are still
IPv4-only, so "SSH from anywhere" stops being true. For a machine you need to reach reliably from
arbitrary networks, pay the ${{ r.f.ipv4_mo }}. Run **dual-stack** if you want IPv6 as well.

## 3 · EBS storage {#ebs}

The root volume is charged per **provisioned** GB-month — not per GB used — and **it keeps
billing while the instance is stopped.** Stopping an instance zeroes the compute line and leaves
the storage line untouched.

| Volume type | {{ r.short }} rate | 8 GB | 30 GB |
|---|---|---|---|
| **gp3** (general purpose SSD, default choice) | ${{ r.f.gp3 }}/GB-mo | ${{ r.f.ebs8 }} | ${{ r.f.ebs30 }} |
| gp2 (previous generation SSD) | ${{ r.f.gp2 }}/GB-mo | ${{ r.f.gp2_8 }} | ${{ r.f.gp2_30 }} |
| st1 (throughput HDD, 125 GB minimum) | ${{ r.f.st1 }}/GB-mo | — | — |
| sc1 (cold HDD, 125 GB minimum) | ${{ r.f.sc1 }}/GB-mo | — | — |
| standard (magnetic, legacy) | ${{ r.f.magnetic }}/GB-mo | ${{ r.f.mag8 }} | ${{ r.f.mag30 }} |
| Snapshots (to S3) | ${{ r.f.snapshot }}/GB-mo | — | — |

**Always pick gp3.** It is ~20% cheaper per GB than gp2 *and* includes a free performance
baseline of **3,000 IOPS and 125 MB/s** at any volume size. On gp2, performance scaled with size
(3 IOPS/GB), so an 8 GB gp2 volume was painfully slow. With gp3 an 8 GB root disk performs fine.

You only pay extra on gp3 if you provision *beyond* the free baseline:
${{ r.f.gp3_iops }}/IOPS-month above 3,000 and ${{ r.f.gp3_tput }}/GiBps-month
above 125 MB/s. For a VPS you never need to.

Snapshots are charged on **compressed changed blocks**, so a 30 GB volume that is 40% full
typically snapshots to a few GB. Useful and cheap, but not free — budget a little if you enable
automated backups.

## 4 · Data transfer {#transfer}

| Direction | Rate |
|---|---|
| **Inbound from the internet** | **Free**, always, unlimited |
| Outbound to internet — first {{ m.free_egress_gb }} GB/month, account-wide | **Free** |
| Outbound to internet — next 10 TB | ${{ r.f.dto }}/GB |
| Outbound — next 40 TB | ${{ r.f.dto40 }}/GB |
| Outbound — next 100 TB | ${{ r.f.dto100 }}/GB |
| Outbound — beyond 150 TB | ${{ r.f.dto150 }}/GB |
| Between AZs in the same region | $0.01/GB each direction |

For an SSH box, a Git remote, a personal API or a low-traffic site, the free
{{ m.free_egress_gb }} GB covers you completely — **egress is genuinely $0 on the baseline
estimate**, and that free allowance is account-wide per month, not per instance.

It stops being free the moment you serve media, host backups people download, or push large
container images outward. At ${{ r.f.dto }}/GB, **170 GB of egress costs more than a
`t4g.nano`.** Egress is the line item most likely to break a small budget unexpectedly, so
set a billing alarm.

## 5 · NAT Gateway {#nat-gateway}

<div class="callout danger" markdown="1">
**A NAT Gateway costs ${{ r.f.nat_mo }}/month in {{ r.short }} before processing a single byte,
and there is no reason for a single SSH-accessible VPS to have one.**
</div>

| Charge | {{ r.short }} rate | Monthly |
|---|---|---|
| Per NAT Gateway-hour (every hour it exists) | ${{ r.f.nat_hr }}/hr | **${{ r.f.nat_mo }}** |
| Per GB processed (in **and** out) | ${{ r.f.nat_gb }}/GB | on top |

Partial hours bill as full hours, and standard data-transfer charges apply *in addition* to the
per-GB processing fee — so egress through a NAT Gateway is billed twice, once by NAT and once by
data transfer.

### Why so many tutorials tell you to use one

A NAT Gateway exists to give instances in a **private** subnet (no public IP, unreachable from
the internet) outbound access for updates and API calls. That is genuinely the right pattern for
application servers in a production tier, and every "AWS best practice VPC" diagram shows one.

It is the wrong pattern for a VPS you intend to SSH into from the open internet. A machine that
must accept inbound connections belongs in a **public subnet** with a route to an **Internet
Gateway**. An IGW is a horizontally-scaled, fully-managed VPC component that costs **$0/hour and
$0/GB** — you pay only the normal data transfer rates you would pay anyway.

### Cheaper ways to get outbound-only access

| Approach | Hourly cost | Notes |
|---|---|---|
| **Public subnet + Internet Gateway** | **$0** | The right answer here. Instance needs a public IP; use security groups to restrict inbound. |
| Egress-only internet gateway (IPv6) | **$0** | IPv6 equivalent of NAT. Genuinely free, outbound-only. |
| VPC gateway endpoints for S3 & DynamoDB | **$0** | No hourly, no per-GB. Add these even in a NAT design. |
| NAT instance (a `t4g.nano` doing NAT) | ~$3/mo | Cheap but you own the patching, failover and throughput. |
| **NAT Gateway** | **${{ r.f.nat_mo }}/mo** | Managed and highly available. Not for a $20 budget. |

## 6 · DNS {#dns}

To reach `myvps.domain.com` you need an A record. Two options, and only one costs money:

**Route 53 (${{ m.route53_hosted_zone_mo }}/month per hosted zone).** A hosted zone for
`domain.com` is ${{ m.route53_hosted_zone_mo }}/month for the first 25 zones, plus
${{ m.route53_per_million_queries }} per million standard queries. Real-world query volume for a
personal domain is a rounding error — you are paying the ${{ m.route53_hosted_zone_mo }}. Alias
records to AWS resources are free to query.

**Your existing registrar or DNS provider ($0).** Cloudflare, Namecheap, Porkbun, Route 53 
alternatives — most include DNS hosting free with the domain. If `domain.com` already resolves 
somewhere, just add an A record for the `myvps` subdomain there and pay nothing.

The baseline on this site **includes** the ${{ m.route53_hosted_zone_mo }} to stay conservative.
Untick the Route 53 switch on the explorer if your registrar handles DNS.

Domain registration itself (~$10–15/year, roughly $1/month amortised) is outside these figures —
you told us you already own the domain.

## What costs nothing {#free}

Worth knowing, because fear of hidden charges pushes people into worse architectures:

- **VPC, subnets, route tables, security groups, network ACLs** — no charge
- **Internet Gateway** — no hourly, no per-GB
- **Egress-only internet gateway** (IPv6 outbound) — no charge
- **EC2 key pairs** — no charge
- **IPv6 addresses** — no charge
- **Inbound data transfer** — no charge
- **Gateway VPC endpoints** for S3 and DynamoDB — no charge
- **EC2 Instance Connect Endpoint** — no hourly charge; lets you reach a private instance
  without any public IP (but needs the AWS CLI/console, not a plain `ssh` to a hostname)
- **Basic CloudWatch monitoring** (5-minute metrics) — no charge
- **AWS Budgets** — first two budgets free, so a spend alarm costs nothing
- **First {{ m.free_egress_gb }} GB/month egress** — no charge

## The usual surprises {#surprises}

Every one of these has generated a "why is my AWS bill $40?" forum post:

1. **An idle Elastic IP still bills ${{ r.f.ipv4_mo }}/month.** Release it, don't just detach it.
2. **A stopped instance still bills for its EBS volume.** Stopping saves compute only.
3. **Terminating an instance can leave the volume behind** if `DeleteOnTermination` was set to
   false. Orphaned volumes bill forever. So do orphaned snapshots.
4. **Detailed CloudWatch monitoring** (1-minute metrics) is about $2.10/instance/month. The
   launch wizard makes it a single checkbox. Leave it off.
5. **Cross-AZ traffic costs $0.01/GB each way.** Keep a single-instance build in one AZ.
6. **Unlimited-mode burst credits** have no ceiling. See the compute section.
7. **A second public IPv4 on the same instance** is another ${{ r.f.ipv4_mo }}/month.
8. **Elastic Load Balancers** start around $16–18/month before LCU charges. A single VPS does
   not need one; terminate TLS on the instance with Caddy or nginx + Let's Encrypt.
9. **Public IPv4 on a NAT Gateway** is billed too — the NAT's own address is an extra
   ${{ r.f.ipv4_mo }}/month on top of the ${{ r.f.nat_mo }}.

## The $20 arithmetic {#budget}

Working backwards from the budget in {{ r.short }}:

```text
  Monthly budget                                    $20.00
- Public IPv4 address   ${{ r.f.ipv4_hr }}/hr x 730 hr           -$ {{ r.f.ipv4_mo }}
- EBS gp3 root volume   ${{ r.f.gp3 }}/GB-mo x 8 GB          -$ {{ r.f.ebs8 }}
- Route 53 hosted zone  flat                       -$ {{ m.route53_hosted_zone_mo }}
- Data transfer out     20 GB, inside free tier    -$ 0.00
                                                   ========
  Left for compute                                  ${{ r.f.compute_budget_mo }}
                                                  ÷ 730 hr
                                                   ========
  Maximum On-Demand rate                        ${{ r.f.max_hourly_rate }} /hr
```

So **any instance at or below ${{ r.f.max_hourly_rate }}/hour fits a $20 all-in monthly budget in
{{ r.short }}** — which lands you at `t3.small` and everything below it.

At the baseline, {{ r.under_budget_count }} instance types qualify in {{ r.short }}. The
[explorer]({{ '/' | relative_url }}) shows the full list for all {{ m.region_count }} regions and
lets you move every assumption.

### Recommended picks

| Use case | Instance | vCPU / RAM | All-in in {{ r.short }} |
|---|---|---|---|
| Bastion / jump host, dev shell, cron runner | `t4g.nano` | {{ r.picks['t4g.nano'].vcpu }} / {{ r.picks['t4g.nano'].mem }} | ~${{ r.picks['t4g.nano'].total_mo }} |
| Personal site, small API, Docker, Tailscale node | `t4g.micro` | {{ r.picks['t4g.micro'].vcpu }} / {{ r.picks['t4g.micro'].mem }} | ~${{ r.picks['t4g.micro'].total_mo }} |
| Comfortable general VPS — the sweet spot | `t4g.small` | {{ r.picks['t4g.small'].vcpu }} / {{ r.picks['t4g.small'].mem }} | ~${{ r.picks['t4g.small'].total_mo }} |
| Same but x86-only software | `t3a.small` | {{ r.picks['t3a.small'].vcpu }} / {{ r.picks['t3a.small'].mem }} | ~${{ r.picks['t3a.small'].total_mo }} |

`t4g.small` at ~${{ r.picks['t4g.small'].total_mo }} all-in is the best value in the set: 2 GB of RAM leaves room for a real
workload while staying comfortably inside $20 with headroom for a bit of egress overage.

<div class="callout" markdown="1">
**On the AWS Free Tier.** New AWS accounts now get **credit-based** free usage (up to $200 in
credits) rather than the old blanket 12-month allowances; accounts created under the earlier
programme kept 750 hours/month of `t2.micro`/`t3.micro`, 750 hours of public IPv4, and 30 GB of
EBS for 12 months. Because the terms depend on when your account was opened, **every figure on
this site is full On-Demand list price with no free tier applied.** Free-tier credit can only
make your bill smaller than what you see here — and it does expire, which is exactly when people
get surprised.
</div>

## Going cheaper still {#cheaper}

You asked for On-Demand, so the explorer prices On-Demand only. For completeness, the levers
that go lower:

- **Compute Savings Plan / Reserved Instance** — commit to 1 or 3 years for up to ~40% off a T
  instance (more on larger families). A 1-year no-upfront Compute Savings Plan is the single
  biggest lever if the box is genuinely permanent.
- **Spot Instances** — up to ~90% off, but AWS can reclaim the instance with a 2-minute warning.
  Fine for batch, wrong for a VPS you SSH into.
- **Stop it when you sleep.** Per-second billing is real. 12 hours a day instead of 24 halves
  the compute line (EBS and IPv4 keep billing). An EventBridge schedule doing this is free.
- **Drop the Route 53 zone** if your registrar hosts DNS — saves
  ${{ m.route53_hosted_zone_mo }}/month.
- **Go IPv6-only** — saves ${{ r.f.ipv4_mo }}/month, at the cost of reachability from IPv4-only
  networks. Read the [IPv6 section](#ipv4) before choosing this.
- **Pick a cheaper region** — Mumbai (`ap-south-1`) is the cheapest in this dataset at
  ${{ site.data.rates['ap-south-1'].f.cheapest_total_mo }} all-in.
- **Consider Lightsail instead.** AWS Lightsail bundles compute, SSD, a static IPv4 and a
  generous transfer allowance into one flat monthly price starting around $5/month. For a
  straightforward VPS it is often cheaper and simpler than assembling EC2 parts. EC2 wins when
  you want the full VPC, IAM, AMI and instance-type flexibility — which is what you asked about.

## Methodology {#methodology}

**Where the numbers come from.** Every rate is fetched at build time from the public,
unauthenticated JSON pricing feeds that back AWS's own pricing pages —
`b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/...`. Nothing is hand-typed and nothing is
scraped out of HTML. See [`tools/build_pricing.py`]({{ site.github_repo_url }}/blob/main/tools/build_pricing.py).

| Feed | What it provides |
|---|---|
| `ec2/.../ec2-ondemand-without-sec-sel/{region}/Linux` | On-Demand hourly rates + vCPU, memory, network, family |
| `vpc/USD/current/vpc.json` | Public IPv4 in-use and idle hourly rates |
| `ec2/USD/current/ebs.json` | gp3/gp2/st1/sc1/magnetic and snapshot rates |
| `ec2/USD/current/natgateway.json` | NAT Gateway hourly and per-GB rates |
| `datatransfer/USD/current/datatransfer.json` | Internet egress tiers |

**Scope and assumptions.**

- Linux, shared tenancy, no pre-installed commercial software, no license required.
- On-Demand list price in USD. No Savings Plan, Reserved Instance, Spot, credit, EDP discount,
  free-tier allowance or tax is applied.
- 730 hours per month, matching AWS's own convention.
- Baseline configuration: {{ m.baseline.hours }} hr/month, {{ m.baseline.ebs_gb }} GB gp3 root
  volume, {{ m.baseline.public_ipv4 }} public IPv4 address, Route 53 hosted zone included,
  {{ m.baseline.egress_gb }} GB egress, no NAT Gateway, no snapshots.
- The dataset includes every instance type priced at or below ${{ m.max_hourly_included }}/hour, which is generous
  headroom above anything a $20 budget can reach.
- Mac instances are excluded — they are Dedicated Host only and can never be cheap.

**Freshness.** Generated {{ m.generated_human }}. Regenerate with
`python3 tools/build_pricing.py`; a scheduled GitHub Actions workflow refreshes it weekly and
commits any changes. AWS changes prices without notice, and regional rates drift independently —
confirm against the [AWS Pricing Calculator](https://calculator.aws/) before committing spend.

**Not affiliated with Amazon Web Services.** Independent reference, provided as-is.
