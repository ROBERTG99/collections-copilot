output "vpc_id" {
  description = "VPC id."
  value       = aws_vpc.this.id
}

output "vpc_cidr_block" {
  description = "VPC CIDR block."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "Public subnet ids (ALB and NAT only)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet ids (tasks and database)."
  value       = aws_subnet.private[*].id
}

output "nat_gateway_count" {
  description = "Number of NAT gateways (one for dev)."
  value       = 1
}

output "interface_endpoints" {
  description = "Services reached through interface endpoints."
  value       = sort(keys(aws_vpc_endpoint.interface))
}

output "subnets_map_public_ip" {
  description = "Whether any subnet assigns public IPs on launch (must be false)."
  value       = anytrue(concat(aws_subnet.public[*].map_public_ip_on_launch, aws_subnet.private[*].map_public_ip_on_launch))
}

output "flow_log_group" {
  description = "Flow-log group name, retention and key (for the observability tests)."
  value = {
    name      = aws_cloudwatch_log_group.flow.name
    retention = aws_cloudwatch_log_group.flow.retention_in_days
    kms       = aws_cloudwatch_log_group.flow.kms_key_id
  }
}

output "policy_documents" {
  description = "IAM policy documents created by this module (for the IAM tests)."
  value       = { vpc_flow_logs = local.flow_policy }
}
