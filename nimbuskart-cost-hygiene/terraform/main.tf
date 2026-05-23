locals {
  common_tags = {
    Project     = var.project
    Environment = var.environment
    Owner       = var.owner
    ManagedBy   = "terraform"
  }
}

# ── Network ───────────────────────────────────────────────────────────────────

module "network" {
  source = "./modules/network"

  vpc_cidr            = var.vpc_cidr
  public_subnet_cidrs = var.public_subnet_cidrs
  availability_zones  = var.availability_zones
  project             = var.project
  environment         = var.environment
  owner               = var.owner
  common_tags         = local.common_tags
}

# ── Security Group ────────────────────────────────────────────────────────────
#
# DECISION: The spec says SSH CIDR defaults to 0.0.0.0/0 "but flag this".
# We replaced the default with 10.0.0.0/8 in variables.tf and call it out in
# the README. The resource below uses var.ssh_cidr so the value is always
# explicit and auditable. See README → Decisions & Deviations.

resource "aws_security_group" "web" {
  name        = "${var.project}-${var.environment}-web-sg"
  description = "NimbusKart web-tier: HTTP/HTTPS public, SSH from configurable CIDR"
  vpc_id      = module.network.vpc_id

  ingress {
    description = "HTTP from anywhere"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH — tighten this CIDR; never use 0.0.0.0/0 in production"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}-web-sg"
  })
}

# ── EC2 Web Tier ──────────────────────────────────────────────────────────────

resource "aws_instance" "web" {
  count                       = 2
  ami                         = var.ami_id
  instance_type               = "t3.micro"
  subnet_id                   = module.network.public_subnet_ids[count.index]
  vpc_security_group_ids      = [aws_security_group.web.id]
  associate_public_ip_address = true

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}-web-${count.index + 1}"
    Tier = "web"
  })
}

# ── S3 Application Logs ───────────────────────────────────────────────────────

resource "aws_s3_bucket" "app_logs" {
  bucket = var.log_bucket_name

  tags = merge(local.common_tags, {
    Name    = "${var.project}-${var.environment}-app-logs"
    Purpose = "application-logs"
  })
}

resource "aws_s3_bucket_versioning" "app_logs" {
  bucket = aws_s3_bucket.app_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Expire non-current object versions after 30 days to control storage costs.
resource "aws_s3_bucket_lifecycle_configuration" "app_logs" {
  bucket = aws_s3_bucket.app_logs.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    # Also clean up incomplete multipart uploads to avoid hidden cost.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.app_logs]
}

# ── Intentional Orphan — for Cost Janitor testing ─────────────────────────────
#
# DECISION: The spec requires one unattached EBS volume so Part B has a known
# orphan to detect. We deliberately omit Project/Environment/Owner tags here
# so the Janitor's tag-compliance scan also fires on this resource. This
# violates the "every resource must be tagged" rule, and is documented in the
# README. In a real account, provision orphan fixtures in a separate test stack.

resource "aws_ebs_volume" "orphan_test" {
  availability_zone = var.availability_zones[0]
  size              = var.orphan_volume_size_gb
  type              = "gp3"

  tags = {
    Name      = "${var.project}-orphan-fixture"
    ManagedBy = "terraform"
    # Project, Environment, Owner intentionally absent — triggers tag scanner
  }
}
