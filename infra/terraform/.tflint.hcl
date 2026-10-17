# tflint for every module and env (scripts/tf.sh; make tf-check). The bundled terraform ruleset
# only: plugin downloads are not needed offline.
config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_naming_convention" {
  enabled = true
}
