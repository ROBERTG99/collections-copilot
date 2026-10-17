# Internet-facing ALB: HTTPS only (HTTP redirects), TLS 1.3 policy, 120 s idle timeout for SSE.
# Everything goes to the web BFF except provider webhooks (`/webhooks/*`), which go to the API.
# The API's /metrics is never routed.

resource "aws_security_group" "this" {
  name_prefix = "${var.name}-alb-"
  description = "Public HTTPS (and HTTP for the redirect) to the load balancer"
  vpc_id      = var.vpc_id
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.this.id
  description       = "HTTPS from the internet"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "http_redirect" {
  #checkov:skip=CKV_AWS_260:port 80 only answers with a 301 to HTTPS (aws_lb_listener.http)
  security_group_id = aws_security_group.this.id
  description       = "HTTP, answered only with a redirect to HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "targets" {
  for_each          = var.targets
  security_group_id = aws_security_group.this.id
  description       = "To the ${each.key} tasks"
  cidr_ipv4         = var.vpc_cidr_block
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
}

# The console is served to the internet by design; the WAF and HTTPS-only listeners guard it.
#trivy:ignore:AVD-AWS-0053
resource "aws_lb" "this" {
  #checkov:skip=CKV2_AWS_76:the attached web ACL runs AWSManagedRulesKnownBadInputsRuleSet (Log4JRCE included); the check does not follow aws_wafv2_web_acl_association
  name_prefix                = substr(replace(var.name, "-", ""), 0, 6)
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.this.id]
  subnets                    = var.public_subnet_ids
  idle_timeout               = var.idle_timeout_seconds
  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection
  dynamic "access_logs" {
    for_each = var.access_logs_bucket == null ? [] : [var.access_logs_bucket]
    content {
      bucket  = access_logs.value
      prefix  = var.name
      enabled = true
    }
  }
}

resource "aws_lb_target_group" "this" {
  #checkov:skip=CKV_AWS_378:TLS ends at the load balancer; targets sit in private subnets reachable only from it
  for_each             = var.targets
  name_prefix          = substr(each.key, 0, 6)
  port                 = each.value.port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30
  health_check {
    path                = each.value.health_path
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  #checkov:skip=CKV_AWS_378:this listener only redirects to HTTPS
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[var.default_target].arn
  }
}

resource "aws_lb_listener_rule" "paths" {
  for_each     = { for k, t in var.targets : k => t if length(t.path_patterns) > 0 }
  listener_arn = aws_lb_listener.https.arn
  priority     = each.value.priority
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[each.key].arn
  }
  condition {
    path_pattern {
      values = each.value.path_patterns
    }
  }
}

# WAF: AWS managed rules for common exploits and known bad inputs, plus a per-IP rate limit.
resource "aws_wafv2_web_acl" "this" {
  name  = "${var.name}-alb"
  scope = "REGIONAL"
  default_action {
    allow {}
  }
  rule {
    name     = "aws-common"
    priority = 10
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-common"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "aws-known-bad-inputs"
    priority = 20
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "rate-per-ip"
    priority = 30
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_per_5min
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "rate-per-ip"
      sampled_requests_enabled   = true
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-alb"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "this" {
  resource_arn = aws_lb.this.arn
  web_acl_arn  = aws_wafv2_web_acl.this.arn
}

# WAF logs: the group name must start with aws-waf-logs-; KMS-encrypted, 30 days.
resource "aws_cloudwatch_log_group" "waf" {
  #checkov:skip=CKV_AWS_338:§16.3 sets 30-day retention for this environment
  name              = "aws-waf-logs-${var.name}"
  retention_in_days = 30
  kms_key_id        = var.kms_key_arn
}

resource "aws_wafv2_web_acl_logging_configuration" "this" {
  resource_arn            = aws_wafv2_web_acl.this.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
  redacted_fields {
    single_header {
      name = "cookie"
    }
  }
}
