output "neptune_endpoint" {
  description = "Cluster (writer) Gremlin endpoint hostname - set as NEPTUNE_ENDPOINT."
  value       = aws_neptune_cluster.this.endpoint
}

output "neptune_reader_endpoint" {
  description = "Cluster reader endpoint hostname, for read-only workloads."
  value       = aws_neptune_cluster.this.reader_endpoint
}

output "neptune_port" {
  value = aws_neptune_cluster.this.port
}

output "neptune_cluster_resource_id" {
  description = "Used to scope the IAM policy's neptune-db:connect resource ARN."
  value       = aws_neptune_cluster.this.cluster_resource_id
}

output "neptune_access_policy_arn" {
  description = "Attach this IAM policy to whatever role/user runs knowledge_fabric.graphrag."
  value       = aws_iam_policy.neptune_access.arn
}

output "bastion_public_ip" {
  description = "Null unless enable_bastion = true."
  value       = var.enable_bastion ? aws_instance.bastion[0].public_ip : null
}

output "bastion_tunnel_command" {
  description = "Run this, then set NEPTUNE_ENDPOINT=localhost and NEPTUNE_PORT=8182 locally."
  value = var.enable_bastion ? (
    "ssh -N -L 8182:${aws_neptune_cluster.this.endpoint}:8182 ec2-user@${aws_instance.bastion[0].public_ip}"
  ) : null
}
