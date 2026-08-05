provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project = "sealfleet-hosted-aws"
      Managed = "terraform"
    }
  }
}

provider "random" {}
provider "tls" {}
