---
layout: default
permalink: /ssh-setup/
title: SSH to your EC2 box from anywhere via myvps.domain.com
layout_title: SSH from anywhere via <code>myvps.domain.com</code>
subtitle: A complete, copy-pasteable build for a public-subnet EC2 VPS with a stable Elastic IP, a DNS name, hardened sshd and a billing alarm — no NAT Gateway, no load balancer, nothing you don't need.
prose: true
---

{%- assign r = site.data.rates['us-east-1'] -%}
{%- assign m = site.data.meta -%}

<div class="toc" markdown="0">
  <strong>Steps</strong>
  <ol>
    <li><a href="#architecture">The architecture (and what it costs)</a></li>
    <li><a href="#prereqs">Prerequisites</a></li>
    <li><a href="#keypair">Create an SSH key pair</a></li>
    <li><a href="#network">Networking: VPC, subnet, Internet Gateway</a></li>
    <li><a href="#sg">Security group</a></li>
    <li><a href="#launch">Launch the instance</a></li>
    <li><a href="#eip">Allocate and attach an Elastic IP</a></li>
    <li><a href="#dns">Point myvps.domain.com at it</a></li>
    <li><a href="#connect">Connect</a></li>
    <li><a href="#harden">Harden sshd</a></li>
    <li><a href="#billing">Billing alarm</a></li>
    <li><a href="#teardown">Teardown checklist</a></li>
  </ol>
</div>

## 1 · The architecture (and what it costs) {#architecture}

```text
  Internet
     |
     |  inbound TCP/22, allowed only from your IP by the security group
     v
  Internet Gateway ............................  $0.00       <-- NOT a NAT Gateway
     |
     +-- VPC  10.0.0.0/16 .....................  $0.00
          |
          +-- Public subnet  10.0.1.0/24 ......  $0.00
               |
               +-- EC2  t4g.small (arm64) .....  ${{ r.picks['t4g.small'].compute_mo }} / mo
               +-- EBS  8 GB gp3 root volume ...  ${{ r.f.ebs8 }} / mo
               +-- Elastic IP  static IPv4 .....  ${{ r.f.ipv4_mo }} / mo
               +-- Security group  22/tcp in ...  $0.00

  DNS:  myvps.domain.com  --A-->  203.0.113.42
     |
     +-- Route 53 hosted zone .................  ${{ m.route53_hosted_zone_mo }} / mo
                                                ============
                                        TOTAL    ${{ r.picks['t4g.small'].total_mo }} / mo
```

Deliberately absent: NAT Gateway (${{ r.f.nat_mo }}/mo), load balancer (~$17/mo), second AZ,
detailed monitoring. None of them help a single SSH host.

<div class="callout" markdown="1">
Swap `t4g.small` for `t4g.nano` and the same build is **~${{ r.picks['t4g.nano'].total_mo }}/month**. Use the
[explorer]({{ '/' | relative_url }}) to price any instance in any region.
</div>

## 2 · Prerequisites {#prereqs}

- An AWS account with console or CLI access.
- The AWS CLI v2 configured (`aws configure`). Every step below is also doable in the console;
  the CLI is shown because it is unambiguous.
- A domain you control — `domain.com` in these examples. You need the ability to add a DNS record.
- A region. These commands use `us-east-1`; change `--region` throughout if you pick another.

```bash
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=$AWS_REGION
aws sts get-caller-identity          # confirm who you are
```

## 3 · Create an SSH key pair {#keypair}

Generate the key **locally** and import only the public half, so AWS never holds your private key.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/myvps -C "myvps.domain.com"
chmod 600 ~/.ssh/myvps

aws ec2 import-key-pair \
  --key-name myvps-key \
  --public-key-material "fileb://$HOME/.ssh/myvps.pub"
```

<div class="callout warn" markdown="1">
If you instead use `aws ec2 create-key-pair`, AWS generates the key and hands you the private
material **once**. Lose it and you cannot log in — recovery means detaching the root volume and
mounting it on another instance. Generating locally avoids that whole class of problem.
</div>

## 4 · Networking: VPC, subnet, Internet Gateway {#network}

Every AWS account has a **default VPC** in each region that already has a public subnet, an
Internet Gateway and a working route table. If you are happy with it, skip to
[step 5](#sg) — find its ID with:

```bash
aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text
```

To build a clean, purpose-made VPC instead — all of these resources are free:

```bash
# VPC
VPC_ID=$(aws ec2 create-vpc --cidr-block 10.0.0.0/16 \
  --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=myvps-vpc}]' \
  --query 'Vpc.VpcId' --output text)

aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames
aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support

# Public subnet in one AZ (one AZ only — cross-AZ traffic costs $0.01/GB)
SUBNET_ID=$(aws ec2 create-subnet --vpc-id "$VPC_ID" \
  --cidr-block 10.0.1.0/24 --availability-zone "${AWS_REGION}a" \
  --tag-specifications 'ResourceType=subnet,Tags=[{Key=Name,Value=myvps-public}]' \
  --query 'Subnet.SubnetId' --output text)

# Internet Gateway — $0/hour, $0/GB. This is what makes the subnet "public".
IGW_ID=$(aws ec2 create-internet-gateway \
  --tag-specifications 'ResourceType=internet-gateway,Tags=[{Key=Name,Value=myvps-igw}]' \
  --query 'InternetGateway.InternetGatewayId' --output text)
aws ec2 attach-internet-gateway --vpc-id "$VPC_ID" --internet-gateway-id "$IGW_ID"

# Route table with a default route to the IGW
RTB_ID=$(aws ec2 create-route-table --vpc-id "$VPC_ID" \
  --tag-specifications 'ResourceType=route-table,Tags=[{Key=Name,Value=myvps-rtb}]' \
  --query 'RouteTable.RouteTableId' --output text)
aws ec2 create-route --route-table-id "$RTB_ID" \
  --destination-cidr-block 0.0.0.0/0 --gateway-id "$IGW_ID"
aws ec2 associate-route-table --route-table-id "$RTB_ID" --subnet-id "$SUBNET_ID"

echo "VPC=$VPC_ID SUBNET=$SUBNET_ID IGW=$IGW_ID RTB=$RTB_ID"
```

That `0.0.0.0/0 -> IGW` route is the entire difference between a public and a private subnet. A
private subnet routes `0.0.0.0/0` to a NAT Gateway instead — which is where the
${{ r.f.nat_mo }}/month comes from, and why you are not doing that.

## 5 · Security group {#sg}

**Do not open port 22 to the world.** Restrict it to your own address.

```bash
SG_ID=$(aws ec2 create-security-group \
  --group-name myvps-sg --description "myvps SSH access" \
  --vpc-id "$VPC_ID" --query 'GroupId' --output text)

MY_IP=$(curl -fsS https://checkip.amazonaws.com)
echo "Your current public IP: $MY_IP"

aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
  --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,\
IpRanges=[{CidrIp=${MY_IP}/32,Description='home'}]"
```

Security groups are **stateful** — allowing inbound 22 automatically permits the return traffic,
and the default outbound rule already allows everything out. There is nothing else to add.

### "From anywhere" with a dynamic home IP

You want to reach the box from arbitrary networks, but a `/32` allow-list breaks the moment your
ISP rotates your address. Options, best first:

1. **Re-authorise on demand.** Keep a shell function; it takes a second when you move.

   ```bash
   myvps-allow() {
     local ip; ip=$(curl -fsS https://checkip.amazonaws.com)
     aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
       --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,\
   IpRanges=[{CidrIp=${ip}/32,Description='adhoc'}]" 2>/dev/null \
       && echo "allowed $ip" || echo "$ip already allowed"
   }
   ```

2. **Overlay network — Tailscale or WireGuard.** Install Tailscale on the instance, remove the
   public 22 rule entirely, and SSH over the tailnet from any device. Best security posture, and
   Tailscale's free tier is free.

3. **EC2 Instance Connect Endpoint.** No public IP needed and no hourly charge, but you connect
   through `aws ec2-instance-connect ssh`, not a plain `ssh` to a hostname — so it does not
   satisfy "`ssh myvps.domain.com` from anywhere" on its own.

4. **Open 22 to `0.0.0.0/0`.** Works everywhere, and you will see continuous automated
   brute-force attempts within minutes. Only acceptable *with* key-only auth
   ([step 10](#harden)) and ideally fail2ban. If you do this, move sshd to a non-standard
   port as well — it is not real security, but it removes most of the log noise.

## 6 · Launch the instance {#launch}

Resolve the latest Amazon Linux 2023 arm64 AMI from SSM rather than pasting an AMI ID that goes
stale:

```bash
AMI_ID=$(aws ssm get-parameter \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --query 'Parameter.Value' --output text)
echo "AMI: $AMI_ID"
```

For Ubuntu 24.04 arm64 instead:

```bash
AMI_ID=$(aws ssm get-parameter \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --query 'Parameter.Value' --output text)
```

Launch it:

```bash
INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t4g.small \
  --key-name myvps-key \
  --security-group-ids "$SG_ID" \
  --subnet-id "$SUBNET_ID" \
  --credit-specification CpuCredits=standard \
  --metadata-options 'HttpTokens=required,HttpEndpoint=enabled' \
  --block-device-mappings '[{
      "DeviceName":"/dev/xvda",
      "Ebs":{"VolumeSize":8,"VolumeType":"gp3","DeleteOnTermination":true,"Encrypted":true}
    }]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=myvps}]' \
  --query 'Instances[0].InstanceId' --output text)

aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
echo "running: $INSTANCE_ID"
```

Four flags worth understanding:

| Flag | Why |
|---|---|
| `--instance-type t4g.small` | Graviton/arm64, cheapest per unit of work. `t4g.nano` if $8/mo matters more than RAM. |
| `--credit-specification CpuCredits=standard` | **Caps your bill.** Without it, T instances default to *unlimited* and bill surplus CPU credits with no ceiling. Throttles under sustained load instead of charging. |
| `"VolumeType":"gp3"` | ~20% cheaper than gp2 and includes 3,000 IOPS / 125 MB/s free at any size. |
| `HttpTokens=required` | Forces IMDSv2, which blocks the SSRF-to-credential-theft class of attack. Always set this. |

Note there is **no `--associate-public-ip-address`** — an auto-assigned public IP changes on every
stop/start, which would break your DNS record. You are attaching an Elastic IP instead.

## 7 · Allocate and attach an Elastic IP {#eip}

```bash
ALLOC_ID=$(aws ec2 allocate-address --domain vpc \
  --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=Name,Value=myvps-eip}]' \
  --query 'AllocationId' --output text)

aws ec2 associate-address \
  --instance-id "$INSTANCE_ID" --allocation-id "$ALLOC_ID"

EIP=$(aws ec2 describe-addresses --allocation-ids "$ALLOC_ID" \
  --query 'Addresses[0].PublicIp' --output text)
echo "Elastic IP: $EIP"
```

This address is now yours until you explicitly release it, and it survives stop/start — which is
what makes a static DNS record possible.

<div class="callout warn" markdown="1">
This is the ${{ r.f.ipv4_mo }}/month line item, and **it bills at the same
${{ r.f.ipv4_idle_hr }}/hour whether or not it is attached to anything.** If you tear the server
down, `release-address` — see the [teardown checklist](#teardown).
</div>

## 8 · Point myvps.domain.com at it {#dns}

### Option A — Route 53 (${{ m.route53_hosted_zone_mo }}/month per hosted zone)

If `domain.com` is already a Route 53 hosted zone:

```bash
ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name domain.com. \
  --query 'HostedZones[0].Id' --output text | sed 's|/hostedzone/||')

cat > /tmp/rrset.json <<JSON
{
  "Comment": "myvps A record",
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "myvps.domain.com.",
      "Type": "A",
      "TTL": 300,
      "ResourceRecords": [{"Value": "${EIP}"}]
    }
  }]
}
JSON

aws route53 change-resource-record-sets \
  --hosted-zone-id "$ZONE_ID" --change-batch file:///tmp/rrset.json
```

A hosted zone is ${{ m.route53_hosted_zone_mo }}/month for the first 25 zones plus
${{ m.route53_per_million_queries }} per million queries — for a personal domain, effectively just
the ${{ m.route53_hosted_zone_mo }}.

### Option B — your existing registrar ($0)

If `domain.com` already resolves via Cloudflare, Namecheap, Porkbun, GoDaddy or similar, just add
one record in their dashboard and pay nothing extra:

| Field | Value |
|---|---|
| Type | `A` |
| Name / Host | `myvps` |
| Value / Points to | your Elastic IP, e.g. `203.0.113.42` |
| TTL | `300` (5 minutes) |
| Proxy (Cloudflare only) | **must be OFF / "DNS only"** |

<div class="callout danger" markdown="1">
**Cloudflare users:** the orange-cloud proxy only handles HTTP/HTTPS. If `myvps` is proxied, the
record resolves to Cloudflare's IPs and SSH cannot reach your host. Click the cloud grey.
</div>

### Verify the record

```bash
dig +short myvps.domain.com A
# should print your Elastic IP

# check against a public resolver too, to rule out local DNS cache
dig +short @1.1.1.1 myvps.domain.com A
```

Also add an `AAAA` record if you enabled IPv6 on the instance.

## 9 · Connect {#connect}

```bash
ssh -i ~/.ssh/myvps ec2-user@myvps.domain.com     # Amazon Linux
ssh -i ~/.ssh/myvps ubuntu@myvps.domain.com       # Ubuntu
```

Make it a one-word command with `~/.ssh/config`:

```sshconfig
Host myvps
    HostName myvps.domain.com
    User ec2-user
    IdentityFile ~/.ssh/myvps
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

Now `ssh myvps` works from anywhere the security group permits. `ServerAliveInterval` stops idle
sessions being dropped by NAT timeouts on hotel and mobile networks.

### If it hangs or refuses

| Symptom | Cause | Fix |
|---|---|---|
| `Connection timed out` | Security group doesn't allow your current IP | Re-run the `authorize-security-group-ingress` from [step 5](#sg) |
| `Connection timed out`, SG is correct | Subnet has no `0.0.0.0/0 -> IGW` route | Check the route table from [step 4](#network) |
| `Connection refused` | Instance is up, sshd isn't ready | Wait ~60s after `instance-running`; check `get-console-output` |
| Resolves to the wrong IP | Stale DNS, or Cloudflare proxy on | Lower TTL, grey-cloud the record, `dig @1.1.1.1` |
| `Permission denied (publickey)` | Wrong username or key | `ec2-user` on Amazon Linux, `ubuntu` on Ubuntu; confirm `-i` path |
| `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED` | You rebuilt the host on the same IP | `ssh-keygen -R myvps.domain.com` |

```bash
# sshd's own view of the world, no SSH required
aws ec2 get-console-output --instance-id "$INSTANCE_ID" --output text | tail -40
```

## 10 · Harden sshd {#harden}

On a box reachable from the internet, do this before you install anything else.

```bash
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
MaxAuthTries 3
LoginGraceTime 20
X11Forwarding no
AllowAgentForwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
EOF

sudo sshd -t && sudo systemctl reload sshd
```

Run `sudo sshd -t` **before** reloading — a syntax error plus a reload can lock you out of a box
whose only access path is SSH. Keep your current session open until you have proved a new one
works.

Then automatic security updates:

```bash
# Amazon Linux 2023
sudo dnf install -y dnf-automatic
sudo systemctl enable --now dnf-automatic.timer

# Ubuntu
sudo apt-get update && sudo apt-get install -y unattended-upgrades fail2ban
sudo systemctl enable --now unattended-upgrades fail2ban
```

If port 22 is open to `0.0.0.0/0`, add `fail2ban` on Amazon Linux too:

```bash
sudo dnf install -y fail2ban
sudo systemctl enable --now fail2ban
```

## 11 · Billing alarm {#billing}

The estimates on this site hold only while your configuration matches them. A budget alert is the
one safeguard that catches every mistake — a forgotten NAT Gateway, unlimited burst credits, an
egress spike. AWS Budgets gives you **two budgets free**.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

cat > /tmp/budget.json <<'JSON'
{
  "BudgetName": "myvps-monthly",
  "BudgetLimit": {"Amount": "20", "Unit": "USD"},
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST"
}
JSON

cat > /tmp/notify.json <<'JSON'
[{
  "Notification": {
    "NotificationType": "FORECASTED",
    "ComparisonOperator": "GREATER_THAN",
    "Threshold": 80,
    "ThresholdType": "PERCENTAGE"
  },
  "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "you@example.com"}]
}]
JSON

aws budgets create-budget --account-id "$ACCOUNT_ID" \
  --budget file:///tmp/budget.json \
  --notifications-with-subscribers file:///tmp/notify.json
```

`FORECASTED` at 80% warns you while the month is still salvageable, rather than after the bill
lands. Also switch on **Cost Explorer** once — it is free and it is the only practical way to see
that "EC2-Other" line is actually a NAT Gateway.

## 12 · Teardown checklist {#teardown}

Deleting the instance is **not** enough. Work down this list or you will keep paying:

```bash
# 1. Terminate the instance (frees compute; also the volume if DeleteOnTermination=true)
aws ec2 terminate-instances --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-terminated --instance-ids "$INSTANCE_ID"

# 2. RELEASE the Elastic IP — disassociating is not enough, idle EIPs bill $0.005/hr
aws ec2 release-address --allocation-id "$ALLOC_ID"

# 3. Any volume left behind
aws ec2 describe-volumes --filters Name=status,Values=available \
  --query 'Volumes[].[VolumeId,Size,CreateTime]' --output table
# aws ec2 delete-volume --volume-id vol-...

# 4. Snapshots you no longer need
aws ec2 describe-snapshots --owner-ids self \
  --query 'Snapshots[].[SnapshotId,VolumeSize,StartTime]' --output table
# aws ec2 delete-snapshot --snapshot-id snap-...

# 5. Route 53 hosted zone, if you created one only for this ($0.50/mo otherwise)
# aws route53 delete-hosted-zone --id "$ZONE_ID"
```

Then confirm nothing is still holding a public IPv4:

```bash
aws ec2 describe-addresses --query 'Addresses[].[PublicIp,InstanceId,AllocationId]' --output table
```

An empty table means you are done. **VPC IPAM's Public IP Insights** gives the same answer
account-wide across every region if you have ever lost track of an address.

---

## Where the costs land, once more

| Line item | Monthly ({{ r.short }}, `t4g.small`) |
|---|---|
| EC2 compute — `t4g.small` @ ${{ r.picks['t4g.small'].hourly }}/hr × 730 hr | ${{ r.picks['t4g.small'].compute_mo }} |
| Public IPv4 — ${{ r.f.ipv4_hr }}/hr × 730 hr | ${{ r.f.ipv4_mo }} |
| EBS gp3 — 8 GB × ${{ r.f.gp3 }}/GB-mo | ${{ r.f.ebs8 }} |
| Route 53 hosted zone | ${{ m.route53_hosted_zone_mo }} |
| Data transfer out — 20 GB, within the {{ m.free_egress_gb }} GB free allowance | $0.00 |
| VPC, subnet, Internet Gateway, security group, key pair | $0.00 |
| **Total** | **${{ r.picks['t4g.small'].total_mo }}** |

Comfortably under ${{ m.budget_usd }}, with room for egress overage. Drop to `t4g.nano` for ~${{ r.picks['t4g.nano'].total_mo }} if you want
more margin, or read the [full cost model]({{ '/cost-model/' | relative_url }}) for every rate and
every trap.
