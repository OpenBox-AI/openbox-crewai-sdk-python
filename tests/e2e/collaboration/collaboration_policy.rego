package openbox

default result := {"decision": "CONTINUE", "reason": ""}

activity_items := input.activity_input if input.activity_input != null
activity_items := input.input if input.input != null

# Block when a task description (or delegated sub-task description) contains BLOCK_THIS.
result := {"decision": "BLOCK", "reason": "Collaboration input contains restricted BLOCK trigger"} if {
    some item in activity_items
    contains(item.description, "BLOCK_THIS")
}

# Halt the agent session when a description contains HALT_THIS.
result := {"decision": "STOP", "reason": "Collaboration input contains restricted HALT trigger"} if {
    some item in activity_items
    contains(item.description, "HALT_THIS")
}
