terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_availability_zones" "available" {
  state = "available"
}

# -----------------------------------------------------------------------
# Networking: a minimal VPC with two private subnets (Neptune requires a
# subnet group spanning at least two AZs) and, only if enable_bastion is
# set, one public subnet for the bastion.
# -----------------------------------------------------------------------

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project}-vpc" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = { Name = "${var.project}-private-${count.index}" }
}

resource "aws_subnet" "public" {
  count                   = var.enable_bastion ? 1 : 0
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 100)
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project}-public" }
}

resource "aws_internet_gateway" "this" {
  count  = var.enable_bastion ? 1 : 0
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.project}-igw" }
}

resource "aws_route_table" "public" {
  count  = var.enable_bastion ? 1 : 0
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this[0].id
  }
  tags = { Name = "${var.project}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = var.enable_bastion ? 1 : 0
  subnet_id      = aws_subnet.public[0].id
  route_table_id = aws_route_table.public[0].id
}

# -----------------------------------------------------------------------
# Neptune
# -----------------------------------------------------------------------

resource "aws_neptune_subnet_group" "this" {
  name       = "${var.project}-subnet-group"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "neptune" {
  name        = "${var.project}-neptune-sg"
  description = "Allow Gremlin (8182) from inside the VPC, and from the bastion if enabled."
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "Gremlin from inside the VPC"
    from_port   = 8182
    to_port     = 8182
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-neptune-sg" }
}

resource "aws_neptune_cluster_parameter_group" "this" {
  name   = "${var.project}-cluster-params"
  family = "neptune1.3"

  parameter {
    name  = "neptune_enable_audit_log"
    value = "0"
  }
}

resource "aws_neptune_cluster" "this" {
  cluster_identifier                   = "${var.project}-cluster"
  engine                               = "neptune"
  engine_version                       = "1.3.2.1" # first engine version with Neptune Serverless support
  neptune_subnet_group_name            = aws_neptune_subnet_group.this.name
  vpc_security_group_ids               = [aws_security_group.neptune.id]
  neptune_cluster_parameter_group_name = aws_neptune_cluster_parameter_group.this.name

  iam_database_authentication_enabled = true # required for this repo's SigV4-signed client
  storage_encrypted                   = true
  skip_final_snapshot                 = true # POC cluster - set to false and set final_snapshot_identifier for anything real
  apply_immediately                   = true

  serverless_v2_scaling_configuration {
    min_capacity = var.min_capacity
    max_capacity = var.max_capacity
  }
}

resource "aws_neptune_cluster_instance" "this" {
  cluster_identifier        = aws_neptune_cluster.this.id
  engine                    = "neptune"
  instance_class            = "db.serverless"
  neptune_subnet_group_name = aws_neptune_subnet_group.this.name
  apply_immediately         = true
}

# -----------------------------------------------------------------------
# IAM role for anything (Lambda, EC2, ECS task, ...) that needs to sign
# Gremlin requests against this cluster. Attach it to whatever compute
# actually runs knowledge_fabric.graphrag - this stack does not attach it
# to anything by itself.
# -----------------------------------------------------------------------

data "aws_caller_identity" "current" {}

resource "aws_iam_policy" "neptune_access" {
  name        = "${var.project}-neptune-access"
  description = "Allows signed Gremlin connections to the ${var.project} Neptune cluster."
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "neptune-db:connect"
        Resource = "arn:aws:neptune-db:${var.region}:${data.aws_caller_identity.current.account_id}:${aws_neptune_cluster.this.cluster_resource_id}/*"
      }
    ]
  })
}
