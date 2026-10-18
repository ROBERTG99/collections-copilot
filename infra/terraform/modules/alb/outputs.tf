output "dns_name" {
  description = "Load balancer DNS name."
  value       = aws_lb.this.dns_name
}

output "arn_suffix" {
  description = "Load balancer ARN suffix (CloudWatch dimension)."
  value       = aws_lb.this.arn_suffix
}

output "target_group_arns" {
  description = "Target group ARNs by service."
  value       = { for k, t in aws_lb_target_group.this : k => t.arn }
}

output "security_group_id" {
  description = "Load balancer security group (tasks allow ingress from it)."
  value       = aws_security_group.this.id
}

output "routes" {
  description = "Path patterns routed to each non-default target (for the tests)."
  value       = { for k, r in aws_lb_listener_rule.paths : k => one(r.condition).path_pattern[0].values }
}

output "listeners" {
  description = "HTTPS policy and HTTP redirect (for the tests)."
  value = {
    ssl_policy    = aws_lb_listener.https.ssl_policy
    http_redirect = one(aws_lb_listener.http.default_action).type
    idle_timeout  = aws_lb.this.idle_timeout
  }
}

output "waf_acl_arn" {
  description = "WAF web ACL attached to the load balancer."
  value       = aws_wafv2_web_acl.this.arn
}

output "waf_log_group" {
  description = "WAF log group retention and key (for the tests)."
  value       = { retention = aws_cloudwatch_log_group.waf.retention_in_days, kms = aws_cloudwatch_log_group.waf.kms_key_id }
}

output "ingress_rules" {
  description = "Ingress of the load balancer's security group (for the network tests)."
  value = [for r in [aws_vpc_security_group_ingress_rule.https, aws_vpc_security_group_ingress_rule.http_redirect] : {
    cidr_ipv4 = r.cidr_ipv4
    port      = r.from_port
    to_port   = r.to_port
  }]
}
