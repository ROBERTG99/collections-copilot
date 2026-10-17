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
  }
}
