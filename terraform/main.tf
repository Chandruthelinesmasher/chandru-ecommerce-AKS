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
