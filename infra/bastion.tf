# Optional bastion for reaching the (VPC-only) Neptune cluster from outside
# AWS - e.g. to run this repo's test suite against the real cluster from a
# laptop or CI runner. Off by default (var.enable_bastion = false); see the
# variable's description and infra/README.md before turning it on.

resource "aws_security_group" "bastion" {
  count       = var.enable_bastion ? 1 : 0
  name        = "${var.project}-bastion-sg"
  description = "SSH in from bastion_allowed_cidr; nothing else."
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.bastion_allowed_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-bastion-sg" }
}

resource "aws_security_group_rule" "neptune_from_bastion" {
  count                    = var.enable_bastion ? 1 : 0
  type                     = "ingress"
  from_port                = 8182
  to_port                  = 8182
  protocol                 = "tcp"
  security_group_id        = aws_security_group.neptune.id
  source_security_group_id = aws_security_group.bastion[0].id
  description              = "Gremlin from the bastion"
}

data "aws_ami" "al2023" {
  count       = var.enable_bastion ? 1 : 0
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}

resource "aws_instance" "bastion" {
  count                       = var.enable_bastion ? 1 : 0
  ami                         = data.aws_ami.al2023[0].id
  instance_type               = "t3.micro"
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.bastion[0].id]
  key_name                    = var.bastion_key_name
  associate_public_ip_address = true

  tags = { Name = "${var.project}-bastion" }
}
