variable "location" {
  type    = string
  default = "eastus"
}

variable "resource_group_name" {
  type    = string
  default = "rg-chandru-ecomm"
}

variable "node_count" {
  type    = number
  default = 2
}

variable "node_vm_size" {
  type    = string
  default = "Standard_D2s_v3"
}
