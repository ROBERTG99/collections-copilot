output "service_name" {
  description = "ECS service name."
  value       = aws_ecs_service.this.name
}

output "security_group_id" {
  description = "Task security group (the database allows ingress from it)."
  value       = aws_security_group.this.id
}

output "posture" {
  description = "Network and runtime settings (for the tests)."
  value = {
    assign_public_ip = one(aws_ecs_service.this.network_configuration).assign_public_ip
    subnets          = one(aws_ecs_service.this.network_configuration).subnets
    read_only_root   = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].readonlyRootFilesystem
    user             = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].user
    scales_on_queue  = local.scale_on_queue
    max_tasks        = aws_appautoscaling_target.this.max_capacity
    dropped_caps     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].linuxParameters.capabilities.drop
  }
}

output "security_group_rules" {
  description = "Ingress and egress of the task security group (for the network tests)."
  value = {
    ingress = [for r in aws_vpc_security_group_ingress_rule.from : {
      port           = r.from_port
      from_group     = r.referenced_security_group_id
      from_cidr_ipv4 = r.cidr_ipv4
    }]
    internet_egress  = length(aws_vpc_security_group_egress_rule.https) > 0
    vpc_egress_ports = sort([for r in aws_vpc_security_group_egress_rule.vpc : r.from_port])
  }
}
