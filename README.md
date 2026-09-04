# ec2-vps

**Which Amazon EC2 instances can you actually run as an SSH-accessible VPS for under $20 a month
— once *every* cost is counted?**

A Jekyll site for GitHub Pages that answers that question with live data pulled straight from
AWS's own public pricing feeds. It covers **23 regions** and prices the full bill, not just
compute:

- EC2 On-Demand compute (Linux, shared tenancy, list price)
- **Public IPv4 address charges** — $0.005/hr since Feb 2024, the cost that broke every
  pre-2024 "cheap EC2" guide
- EBS root volume (gp3/gp2/st1/sc1/magnetic) and snapshots
- **NAT Gateway** — and why a $20 build must not have one
- Internet data transfer, including the 100 GB/month free allowance
- Route 53 hosted zone for the DNS name

## The answer, briefly

A permanently-on EC2 VPS has a **fixed floor of about $4.79/month** in `us-east-1` before any
compute at all: $3.65 for one public IPv4 + $0.64 for an 8 GB gp3 root volume + $0.50 for a
Route 53 hosted zone. That leaves roughly **$15.21/month for compute**, i.e. any instance at or
below **~$0.0208/hour**.

| Instance | vCPU / RAM | Arch | All-in `us-east-1` |
|---|---|---|---|
| `t4g.nano` | 2 / 512 MiB | arm64 | **$7.86** |
| `t4g.micro` | 2 / 1 GiB | arm64 | **$10.92** |
| `t4g.small` | 2 / 2 GiB | arm64 | **$17.05** ← best value |
| `t3a.small` | 2 / 2 GiB | x86_64 | **$18.51** |
| `t3.small` | 2 / 2 GiB | x86_64 | **$19.97** |

Mumbai (`ap-south-1`) is the cheapest region in the dataset at **$6.92** all-in for a
`t4g.nano`.

And the trap: **a NAT Gateway costs $32.85/month before processing a single byte** — more than
the entire budget. A single SSH-accessible VPS belongs in a *public* subnet behind an Internet
Gateway, which is free.

## Pages

| Page | What's on it |
|---|---|
| `/` | Interactive explorer — region picker, budget slider, live cost breakdown, sortable table of every instance type, region comparison |
| `/cost-model/` | Every rate, every line item, the $20 arithmetic, the traps, and full methodology |
| `/ssh-setup/` | Complete build for `ssh myvps.domain.com`: VPC, subnet, IGW, security group, key pair, Elastic IP, DNS (Route 53 *or* external registrar), sshd hardening, billing alarm, teardown checklist |

## Repository layout

```
.
├── _config.yml                      Jekyll configuration
├── index.html                       Interactive pricing explorer
├── cost-model.md                    Full cost breakdown + methodology
├── ssh-setup.md                     End-to-end SSH-via-domain guide
├── _layouts/default.html
├── _includes/{head,header,footer}.html
├── _data/
│   ├── rates.json                   GENERATED — per-region rates, keyed by region id
│   ├── regions.json                 GENERATED — same data as a list, sorted cheapest first
│   └── meta.json                    GENERATED — provenance and freshness
├── assets/
│   ├── css/main.css
│   ├── js/app.js                    Explorer logic + cost model
│   ├── favicon.svg
│   └── data/pricing.json            GENERATED — full dataset (~240 KiB)
├── tools/build_pricing.py           Regenerates all three generated files
└── .github/workflows/
    ├── pages.yml                    Build + deploy to GitHub Pages
    └── refresh-pricing.yml          Weekly re-fetch of AWS rates
```

## Regenerating the pricing data

No AWS credentials required — every endpoint is public and unauthenticated.

```bash
python3 tools/build_pricing.py            # rewrite the four generated files
python3 tools/build_pricing.py --check    # fetch and validate, write nothing
```

It reads the same JSON documents that back the `aws.amazon.com/*/pricing` pages:

| Feed | Provides |
|---|---|
| `ec2/.../ec2-ondemand-without-sec-sel/{region}/Linux` | On-Demand hourly rates, vCPU, memory, network, family |
| `vpc/USD/current/vpc.json` | Public IPv4 in-use / idle hourly rates |
| `ec2/USD/current/ebs.json` | gp3, gp2, st1, sc1, magnetic, snapshots |
| `ec2/USD/current/natgateway.json` | NAT Gateway hourly + per-GB |
| `datatransfer/USD/current/datatransfer.json` | Internet egress tiers |

The cost model lives in `monthly_cost()` in `tools/build_pricing.py` and is mirrored in
`breakdown()` in `assets/js/app.js`. **Change one, change the other.**

A scheduled workflow re-runs the builder every Monday and commits only if a rate actually moved
(timestamp-only churn is ignored).

## Running locally

```bash
bundle install
bundle exec jekyll serve --livereload
# → http://127.0.0.1:4000
```

## Deploying to GitHub Pages

1. Push to `main`.
2. In **Settings → Pages**, set **Source** to **GitHub Actions**.
3. The `pages.yml` workflow builds and deploys on every push to `main`.

The workflow passes `--baseurl` from `actions/configure-pages`, so the same commit works at
`https://<user>.github.io/ec2-vps/` and at a custom domain with no config change. All internal
links use Jekyll's `relative_url` filter.

### Pointing a subdomain at the site

1. Create `CNAME` in the repository root containing exactly your hostname, e.g.:

   ```
   ec2vps.example.com
   ```

2. At your DNS provider add:

   | Type | Name | Value |
   |---|---|---|
   | `CNAME` | `ec2vps` | `<your-github-username>.github.io` |

3. In **Settings → Pages**, enter the custom domain and tick **Enforce HTTPS** once the
   certificate is issued (usually a few minutes).
4. Optionally set `url: https://ec2vps.example.com` in `_config.yml` so `jekyll-seo-tag` and
   `jekyll-sitemap` emit absolute URLs.

> Use a `CNAME` record for a subdomain. Apex domains need `A`/`AAAA` records pointing at
> GitHub's Pages IPs instead.

## Accuracy and caveats

- **US-dollar On-Demand list prices.** No Savings Plan, Reserved Instance, Spot, free-tier
  allowance, credit, EDP discount or tax is applied. Real bills can only be lower — until you
  add something the model doesn't know about.
- **730 hours per month**, matching AWS's own pricing pages and calculator. A 31-day month is
  744 hours, so treat monthly figures as ±2%.
- **Burstable CPU credits in `unlimited` mode have no ceiling** and are the one line item that
  can exceed these estimates. `/cost-model/` explains how to cap it; `/ssh-setup/` launches with
  `CpuCredits=standard`.
- Every figure is generated, never hand-typed — but **verify against the
  [AWS Pricing Calculator](https://calculator.aws/) before committing spend.**

Not affiliated with or endorsed by Amazon Web Services.

## License

[MIT](LICENSE)
