output "vpc_id" {
  description = "ID of the NimbusKart staging VPC"
  value       = module.network.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of the two public subnets"
  value       = module.network.public_subnet_ids
}

output "log_bucket_name" {
  description = "Name of the S3 application-log bucket"
  value       = aws_s3_bucket.app_logs.id
}

output "web_instance_ids" {
  description = "EC2 instance IDs for the web tier"
  value       = aws_instance.web[*].id
}

output "web_security_group_id" {
  description = "Security group ID for the web tier"
  value       = aws_security_group.web.id
}

output "orphan_volume_id" {
  description = "ID of the intentionally unattached EBS volume (Cost Janitor test fixture)"
  value       = aws_ebs_volume.orphan_test.id
}
