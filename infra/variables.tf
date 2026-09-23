variable "region" {
  description = "AWS region to provision into."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix applied to every resource this stack creates."
  type        = string
  default     = "knowledge-fabric"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC created for the Neptune cluster."
  type        = string
  default     = "10.42.0.0/16"
}

variable "min_capacity" {
  description = "Neptune Serverless minimum NCUs (Neptune Capacity Units)."
  type        = number
  default     = 1
}

variable "max_capacity" {
  description = "Neptune Serverless maximum NCUs. Keep low for a POC - this is the main cost lever."
  type        = number
  default     = 4
}

variable "enable_bastion" {
  description = <<-EOT
    Neptune has no public endpoint by design - it's only reachable from
    inside its VPC. Setting this to true adds a small EC2 bastion in a
    public subnet that you can SSH-tunnel through to reach the cluster
    from outside AWS (e.g. `ssh -N -L 8182:<neptune-endpoint>:8182 ec2-user@<bastion-ip>`),
    which is what running this repo's Neptune-backed test suite from
    somewhere other than this VPC requires. Off by default since it adds a
    public-facing instance and its own cost/security surface.
  EOT
  type        = bool
  default     = false
}

variable "bastion_key_name" {
  description = "Existing EC2 key pair name for the bastion. Required if enable_bastion is true."
  type        = string
  default     = null
}

variable "bastion_allowed_cidr" {
  description = "CIDR allowed to SSH into the bastion (port 22). Restrict this to your own IP/32 - do not leave it open."
  type        = string
  default     = null
}
