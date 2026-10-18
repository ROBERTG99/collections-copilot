# One Fargate service: private subnets only, no public IP, read-only root filesystem as UID 10001,
# secrets injected from Secrets Manager, Service Connect for service-to-service calls, and
# autoscaling on CPU or, for queue consumers, on SQS backlog per running task.

data "aws_region" "current" {}

locals {
  scale_on_queue = var.autoscaling.queue_name != null
  # The main port is published under the service name; extra ports under their own names.
  ports = merge(
    var.container_port == null ? {} : { (var.name) = var.container_port },
    var.extra_ports,
  )
  port_mappings = [for name, port in local.ports : {
    name          = name
    containerPort = port
    protocol      = "tcp"
  }]
}

resource "aws_security_group" "this" {
  name_prefix = "${var.cluster_name}-${var.name}-"
  description = "${var.name} tasks"
  vpc_id      = var.vpc_id
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "from" {
  for_each                     = var.container_port == null ? {} : { for i, sg in var.ingress_security_group_ids : tostring(i) => sg }
  security_group_id            = aws_security_group.this.id
  description                  = "Container port from an allowed caller"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
}

# Twilio and the identity provider publish no fixed IP ranges; egress is HTTPS only, through NAT,
# and only for services that call out (internet_egress).
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "https" {
  count             = var.internet_egress ? 1 : 0
  security_group_id = aws_security_group.this.id
  description       = "HTTPS to AWS endpoints, Twilio and the identity provider"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# HTTPS to the VPC endpoints (ECR, logs, Secrets Manager, KMS, SQS) even without internet egress.
resource "aws_vpc_security_group_egress_rule" "endpoints" {
  security_group_id = aws_security_group.this.id
  description       = "HTTPS to the VPC interface endpoints"
  cidr_ipv4         = var.vpc_cidr_block
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "vpc" {
  for_each          = toset([for p in var.vpc_egress_ports : tostring(p)])
  security_group_id = aws_security_group.this.id
  description       = "Port ${each.key} inside the VPC (database, cache or a Service Connect peer)"
  cidr_ipv4         = var.vpc_cidr_block
  ip_protocol       = "tcp"
  from_port         = tonumber(each.key)
  to_port           = tonumber(each.key)
}

resource "aws_ecs_task_definition" "this" {
  family                   = "${var.cluster_name}-${var.name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume {
    name = "tmp"
  }
  container_definitions = jsonencode([{
    name                   = var.name
    image                  = var.image
    essential              = true
    user                   = "10001"
    readonlyRootFilesystem = true
    linuxParameters = {
      capabilities       = { drop = ["ALL"] }
      initProcessEnabled = true
    }
    command      = var.command
    portMappings = local.port_mappings
    environment  = [for k, v in var.environment : { name = k, value = v }]
    secrets      = [for k, arn in var.secrets : { name = k, valueFrom = arn }]
    mountPoints  = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    healthCheck = var.health_check_command == null ? null : {
      command     = var.health_check_command
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = var.log_group_name
        awslogs-region        = data.aws_region.current.region
        awslogs-stream-prefix = var.name
      }
    }
  }])
}

resource "aws_ecs_service" "this" {
  name                               = var.name
  cluster                            = var.cluster_arn
  task_definition                    = aws_ecs_task_definition.this.arn
  desired_count                      = var.autoscaling.min
  launch_type                        = "FARGATE"
  platform_version                   = "LATEST"
  propagate_tags                     = "SERVICE"
  enable_execute_command             = false
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.this.id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = var.target_group_arn == null ? [] : [var.target_group_arn]
    content {
      target_group_arn = load_balancer.value
      container_name   = var.name
      container_port   = var.container_port
    }
  }
  service_connect_configuration {
    enabled   = true
    namespace = var.service_connect_namespace_arn
    dynamic "service" {
      for_each = local.ports
      content {
        port_name = service.key
        client_alias {
          port     = service.value
          dns_name = service.key
        }
      }
    }
  }
  lifecycle {
    ignore_changes = [desired_count] # owned by autoscaling
  }
}

resource "aws_appautoscaling_target" "this" {
  service_namespace  = "ecs"
  resource_id        = "service/${var.cluster_name}/${aws_ecs_service.this.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.autoscaling.min
  max_capacity       = var.autoscaling.max
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "${var.name}-cpu"
  service_namespace  = aws_appautoscaling_target.this.service_namespace
  resource_id        = aws_appautoscaling_target.this.resource_id
  scalable_dimension = aws_appautoscaling_target.this.scalable_dimension
  policy_type        = "TargetTrackingScaling"
  target_tracking_scaling_policy_configuration {
    target_value = var.autoscaling.cpu_target
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

# Queue consumers: keep visible messages per running task near the target.
resource "aws_appautoscaling_policy" "backlog" {
  count              = local.scale_on_queue ? 1 : 0
  name               = "${var.name}-sqs-backlog"
  service_namespace  = aws_appautoscaling_target.this.service_namespace
  resource_id        = aws_appautoscaling_target.this.resource_id
  scalable_dimension = aws_appautoscaling_target.this.scalable_dimension
  policy_type        = "TargetTrackingScaling"
  target_tracking_scaling_policy_configuration {
    target_value       = var.autoscaling.backlog_per_task
    scale_in_cooldown  = 120
    scale_out_cooldown = 30
    customized_metric_specification {
      metrics {
        id          = "visible"
        return_data = false
        metric_stat {
          stat = "Sum"
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesVisible"
            dimensions {
              name  = "QueueName"
              value = var.autoscaling.queue_name
            }
          }
        }
      }
      metrics {
        id          = "tasks"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "ECS/ContainerInsights"
            metric_name = "RunningTaskCount"
            dimensions {
              name  = "ClusterName"
              value = var.cluster_name
            }
            dimensions {
              name  = "ServiceName"
              value = aws_ecs_service.this.name
            }
          }
        }
      }
      metrics {
        id          = "backlog_per_task"
        expression  = "visible / IF(tasks > 0, tasks, 1)"
        label       = "SQS backlog per task"
        return_data = true
      }
    }
  }
}
