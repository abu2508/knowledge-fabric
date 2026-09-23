# Infrastructure: Amazon Neptune

Terraform for the Neptune cluster `knowledge_fabric.graphrag.graphstore`
connects to. Provisions:

- A VPC with two private subnets (Neptune requires a subnet group across
  at least two AZs) and a Neptune subnet group
- A Neptune Serverless cluster (`min_capacity`/`max_capacity` NCUs,
  IAM database authentication enabled, storage encrypted) plus its one
  `db.serverless` instance
- A security group allowing Gremlin (port 8182) from inside the VPC
- An IAM policy (`neptune-db:connect`, scoped to this cluster) to attach to
  whatever compute actually runs the pipeline - this stack does not attach
  it to anything itself
- Optionally (`enable_bastion = true`), a small public EC2 bastion for
  reaching the cluster from outside AWS

## Why a bastion might be optional but isn't skippable in practice

**Neptune has no public endpoint.** It's reachable only from inside its
VPC (or from a peered VPC / Transit Gateway / VPN). That's true for every
Neptune cluster, not a limitation of this Terraform. If nothing in your
setup runs inside `aws_vpc.this` - a Lambda, an ECS task, an EC2 instance -
you need some way in from outside: a bastion with an SSH tunnel
(`enable_bastion = true` sets one up), a Site-to-Site VPN, or a
self-managed WebSocket proxy (API Gateway + Lambda, or an ALB in front of a
small forwarder) if you want a long-lived non-SSH path. This repo ships
the bastion option because it's the smallest thing that works; it is not
turned on by default because it adds a public-facing instance and its own
security surface (`bastion_allowed_cidr` restricts who can SSH to it - set
it to your own IP, never `0.0.0.0/0`).

## Usage

```bash
cd infra
terraform init
terraform apply \
  -var="enable_bastion=true" \
  -var="bastion_key_name=<your-ec2-key-pair>" \
  -var="bastion_allowed_cidr=<your-ip>/32"

# Point the app at the cluster:
export NEPTUNE_ENDPOINT=$(terraform output -raw neptune_endpoint)
export NEPTUNE_PORT=$(terraform output -raw neptune_port)
export AWS_REGION=<your region>

# If running from outside the VPC (e.g. this repo's own test suite from a
# laptop or CI runner), tunnel through the bastion first:
terraform output -raw bastion_tunnel_command   # run this in another terminal
export NEPTUNE_ENDPOINT=localhost
```

Credentials for the Gremlin client (SigV4 signing) and for `terraform
apply` both resolve the normal AWS way (env vars, shared config, an
assumed role, etc.) - this stack never takes a key as a Terraform
variable.

## Cost

Neptune Serverless bills per NCU-hour while the cluster is up, not per
request - `min_capacity`/`max_capacity` (default 1-4 NCUs) are the main
lever for a POC. `terraform destroy` when you're done with a benchmark
run; nothing here is meant to stay up permanently.

## What this build's status is against this infrastructure

The code in `knowledge_fabric/graphrag/graphstore.py` was written against
Neptune's documented Gremlin + IAM SigV4 auth model but has not been
exercised against a live cluster from this repository's own build
environment - that environment's outbound network policy doesn't reach
AWS service endpoints in general, and no cluster has been provisioned yet
from here. Run `terraform apply`, set the environment variables above, and
`pytest tests/graphrag/` (see the repo README) to validate it end-to-end;
this repo's own automated runs cannot do that step for you.
