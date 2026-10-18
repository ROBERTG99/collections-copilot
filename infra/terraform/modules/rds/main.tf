# PostgreSQL 16 in the private subnets only: KMS-encrypted storage, an RDS-managed master password
# in Secrets Manager, TLS required, backups for at least 7 days, reachable only from the tasks.
# The exported log groups are created here first (KMS, 30 days); otherwise RDS creates them with
# neither. Statement logs never carry bind values (debtor data).

data "aws_caller_identity" "current" {}

locals {
  log_exports = toset(["postgresql", "upgrade"])
}

resource "aws_cloudwatch_log_group" "exports" {
  #checkov:skip=CKV_AWS_338:§16.3 sets 30-day retention for this environment
  for_each          = local.log_exports
  name              = "/aws/rds/instance/${var.name}/${each.key}"
  retention_in_days = 30
  kms_key_id        = var.kms_key_arn
}

resource "aws_db_subnet_group" "this" {
  name_prefix = "${var.name}-"
  subnet_ids  = var.subnet_ids
}

resource "aws_security_group" "this" {
  name_prefix = "${var.name}-db-"
  description = "PostgreSQL from the application tasks only"
  vpc_id      = var.vpc_id
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "from_tasks" {
  for_each                     = var.allowed_security_group_ids
  security_group_id            = aws_security_group.this.id
  description                  = "PostgreSQL from an application task"
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_parameter_group" "this" {
  name_prefix = "${var.name}-pg16-"
  family      = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "500"
  }
  parameter {
    name  = "log_parameter_max_length"
    value = "0"
  }
  parameter {
    name  = "log_parameter_max_length_on_error"
    value = "0"
  }
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role" "monitoring" {
  name_prefix = "${var.name}-rds-mon-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "monitoring" {
  role       = aws_iam_role.monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_db_instance" "this" {
  #checkov:skip=CKV_AWS_157:Multi-AZ is the multi_az variable; dev runs a single instance for cost (docs/runbook.md)
  identifier                            = var.name # the exported log groups are named after it
  engine                                = "postgres"
  engine_version                        = var.engine_version
  instance_class                        = var.instance_class
  allocated_storage                     = var.allocated_storage_gb
  max_allocated_storage                 = var.allocated_storage_gb * 4
  storage_type                          = "gp3"
  storage_encrypted                     = true
  kms_key_id                            = var.kms_key_arn
  db_name                               = "cc"
  username                              = "cc_admin"
  manage_master_user_password           = true
  master_user_secret_kms_key_id         = var.kms_key_arn
  db_subnet_group_name                  = aws_db_subnet_group.this.name
  vpc_security_group_ids                = [aws_security_group.this.id]
  parameter_group_name                  = aws_db_parameter_group.this.name
  publicly_accessible                   = false
  multi_az                              = var.multi_az
  backup_retention_period               = var.backup_retention_days
  copy_tags_to_snapshot                 = true
  deletion_protection                   = true
  delete_automated_backups              = false # keep the point-in-time window if the instance goes
  skip_final_snapshot                   = false
  final_snapshot_identifier             = "${var.name}-final"
  auto_minor_version_upgrade            = true
  iam_database_authentication_enabled   = true
  performance_insights_enabled          = true
  performance_insights_kms_key_id       = var.kms_key_arn
  performance_insights_retention_period = 7
  enabled_cloudwatch_logs_exports       = ["postgresql", "upgrade"]
  monitoring_interval                   = 60
  monitoring_role_arn                   = aws_iam_role.monitoring.arn
  depends_on                            = [aws_cloudwatch_log_group.exports]
}
