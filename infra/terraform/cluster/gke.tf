# GKE Autopilot, created and destroyed every session (Phase 5E). Stateless: everything that
# must survive lives in Cloud SQL (data layer) or Secret Manager / Artifact Registry
# (foundation), so a destroy loses nothing.
resource "google_container_cluster" "main" {
  name     = "exit-ticket"
  location = var.region # a region: Autopilot clusters are always regional

  enable_autopilot = true

  # This layer is meant to be destroyed every session, and the cluster holds no data. The
  # provider's default (true) would make every `terraform destroy` fail until it is edited.
  # The layers that hold state (foundation, data) keep their locks.
  deletion_protection = false

  release_channel {
    channel = "REGULAR"
  }

  resource_labels = {
    app = "exit-ticket"
    env = "dev"
  }

  # VPC-native on our network (5C): nodes from the subnet's primary range, Pods and Services
  # from its two secondary ranges.
  network         = local.foundation.network_id
  subnetwork      = local.foundation.subnet_id
  networking_mode = "VPC_NATIVE"

  ip_allocation_policy {
    cluster_secondary_range_name  = local.foundation.pods_range_name
    services_secondary_range_name = local.foundation.services_range_name
  }

  # Private nodes: internal IPs only. They reach Google APIs (Artifact Registry, Secret
  # Manager, Cloud SQL Admin, Logging) through Private Google Access on the subnet; no Cloud NAT.
  # enable_private_endpoint = true turns off the control plane's EXTERNAL IP endpoint; its
  # internal IP endpoint stays (nodes and anything in the VPC).
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = true
  }

  # Required by the API once the external IP endpoint is off ("enable_master_authorized_networks
  # should be enabled if private endpoint is enabled"). No cidr_blocks = no external networks
  # allowed on the IP endpoints (GKE always allows the cluster's own nodes). It doesn't apply to
  # the DNS endpoint, which IAM guards instead.
  master_authorized_networks_config {
    gcp_public_cidrs_access_enabled      = false # not even Google Cloud's public IP ranges
    private_endpoint_enforcement_enabled = false # internal IP endpoint: open to our VPC (only this cluster lives there)
  }

  # How kubectl on the Mac reaches the control plane: the DNS-based endpoint
  # (gke-....REGION.gke.goog). Every request is authorized by IAM (container.clusters.connect
  # + Kubernetes RBAC), so no IP allowlist is needed and it works from any network.
  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
    }
    ip_endpoints_config {
      enabled = true # the internal IP endpoint, kept for the nodes
    }
  }

  # Autopilot always has Workload Identity; written out so the pool is explicit. The pool is
  # per PROJECT, not per cluster, so the workloadIdentityUser binding in foundation
  # (PROJECT_ID.svc.id.goog[exit-ticket/api]) keeps working across cluster rebuilds.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # The GKE Secret Manager add-on (CSI driver secrets-store-gke.csi.k8s.io).
  secret_manager_config {
    enabled = true
  }

  # Autopilot creates the node pools, so the node SA is a default for node auto-provisioning:
  # exit-ticket-nodes (container.defaultNodeServiceAccount + artifactregistry.reader on one
  # repository), never the Compute Engine default SA.
  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = local.foundation.nodes_sa_email
    }
  }
}
