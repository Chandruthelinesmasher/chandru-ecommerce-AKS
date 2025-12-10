resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location
  tags = { project = "chandru-ecomm" }
}

resource "random_id" "acr_id" {
  byte_length = 4
}

resource "random_id" "aks_id" {
  byte_length = 4
}
resource "azurerm_role_assignment" "aks_acr_pull" {
  principal_id                     = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
  role_definition_name             = "AcrPull"
  scope                            = azurerm_container_registry.acr.id
  skip_service_principal_aad_check = true
}