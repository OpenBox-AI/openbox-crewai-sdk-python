package openbox

default result := {"decision": "CONTINUE", "reason": ""}

activity_items := input.activity_input if input.activity_input != null
activity_items := input.input if input.input != null

# Block the task if the description contains BLOCK_THIS
result := {"decision": "BLOCK", "reason": "Input contains restricted BLOCK trigger"} if {
    some item in activity_items
    contains(item.description, "BLOCK_THIS")
}

# Halt the agent session if the description contains HALT_THIS
result := {"decision": "STOP", "reason": "Input contains restricted HALT trigger"} if {
    some item in activity_items
    contains(item.description, "HALT_THIS")
}

# Require human approval if the description contains APPROVE_THIS
result := {"decision": "REQUIRE_APPROVAL", "reason": "Input contains content requiring approval"} if {
    some item in activity_items
    contains(item.description, "APPROVE_THIS")
}
