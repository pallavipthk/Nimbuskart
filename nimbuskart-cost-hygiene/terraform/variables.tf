variable "region" {
  description = "AWS region to deploy resources into"
  type        = string
  default     = "us-east-1"
}

variable "localstack_endpoint" {
  description = "LocalStack API endpoint URL. Override via TF_VAR_localstack_endpoint in CI."
  type        = string
  default     = "http://localhost:4566"
}

variable "project" {
  description = "Project name used in resource naming and cost-attribution tags"
  type        = string
  default     = "nimbuskart"
}

variable "environment" {
  description = "Deployment environment name"
  type        = string
  default     = "staging"
}

variable "owner" {
  description = "Team responsible for these resources (used in Owner tag)"
  type        = string
  default     = "platform-team"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.20.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for the two public subnets"
  type        = list(string)
  default     = ["10.20.1.0/24", "10.20.2.0/24"]
}

variable "availability_zones" {
  description = "Two AZs for the public subnets"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

# DECISION: The spec says default SSH CIDR = 0.0.0.0/0 but flags it.
# We default to a private RFC-1918 range instead. Reviewers must supply
# their own CIDR via -var or tfvars. See README → Decisions & Deviations.
variable "ssh_cidr" {
  description = "CIDR allowed inbound on port 22. NEVER set to 0.0.0.0/0 in production."
  type        = string
  default     = "10.0.0.0/8"
}

variable "ami_id" {
  description = "AMI ID for EC2 instances. Any non-empty string works with LocalStack."
  type        = string
  default     = "ami-0c02fb55956c7d316" # Amazon Linux 2 (us-east-1) — LocalStack ignores this
}

variable "log_bucket_name" {
  description = "Name of the S3 bucket for application logs. Must be globally unique."
  type        = string
  default     = "nimbuskart-staging-app-logs"
}

variable "orphan_volume_size_gb" {
  description = "Size (GB) of the intentionally unattached EBS volume used to test the Janitor"
  type        = number
  default     = 20
}
