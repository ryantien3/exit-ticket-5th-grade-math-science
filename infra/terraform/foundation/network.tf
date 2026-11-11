# Custom VPC for a private GKE Autopilot cluster and Cloud SQL private IP (Phase 5C).
# All ranges are RFC 1918, inside 10.0.0.0/9, so none can overlap the default network's
# 10.128.0.0/9. Secondary ranges and the PSA range are effectively fixed once a cluster or
# Cloud SQL uses them.
locals {
  subnet_range   = "10.10.0.0/20" # nodes: 4,092 usable
  pods_range     = "10.20.0.0/17" # 32,768 = 512 nodes x /26 (Autopilot: 32 Pods per node)
  services_range = "10.30.0.0/22" # 1,024 ClusterIPs
  psa_address    = "10.40.0.0"    # with psa_prefix: 10.40.0.0/20 for Google's producer services
  psa_prefix     = 20
}

resource "google_compute_network" "vpc" {
  name        = "exit-ticket-vpc"
  description = "Exit Ticket: private GKE Autopilot and Cloud SQL private IP"

  # Custom mode: only the subnets below exist, and no allow rules come with it (the implied
  # rules deny all ingress and allow all egress).
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  # Keep the default 0.0.0.0/0 route: Private Google Access sends API traffic along it.
  # With no public IPs and no Cloud NAT, nothing else can use it.
  delete_default_routes_on_create = false

  # The cluster and data layers depend on this network from other states.
  deletion_policy = "PREVENT"

  depends_on = [google_project_service.this["compute.googleapis.com"]]
}

resource "google_compute_subnetwork" "us_east4" {
  name          = "exit-ticket-us-east4"
  region        = var.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = local.subnet_range

  # Nodes have no public IPs; this lets them reach Google APIs (Artifact Registry, Secret
  # Manager, Logging) anyway.
  private_ip_google_access = true

  # VPC-native GKE: Pods get alias IPs from "pods", Services get ClusterIPs from "services".
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = local.pods_range
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = local.services_range
  }
}

# Private services access, step 1: a range in this VPC that Google may use for Cloud SQL.
resource "google_compute_global_address" "psa" {
  name          = "exit-ticket-psa"
  description   = "Private services access range (Cloud SQL private IP)"
  network       = google_compute_network.vpc.id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  address       = local.psa_address
  prefix_length = local.psa_prefix
}

# Private services access, step 2: peer this VPC with Google's service producer network.
resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa.name]

  # Deleting the connection fails while Cloud SQL (another layer, another state) still uses
  # it, and Google can take days to release it after the instance is gone. PREVENT makes a
  # wrong-order destroy impossible. End-of-project teardown: destroy cluster, then data, then
  # change this to "REMOVE_PEERING" and destroy foundation (docs/phase-5c-network.md).
  deletion_policy = "PREVENT"

  depends_on = [google_project_service.this["servicenetworking.googleapis.com"]]
}

# No firewall rules of our own: GKE creates (and deletes) the rules a cluster needs, and the
# Ingress controller adds the health-checker rule. Phase 8: remove the default network (and
# its default-allow-ssh / -rdp rules open to 0.0.0.0/0) once nothing uses it.
