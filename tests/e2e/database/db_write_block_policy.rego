package openbox

default result := {"decision": "CONTINUE", "reason": ""}

write_verbs := {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE"}

result := {"decision": "BLOCK", "reason": "Write operations blocked by policy"} if {
    some span in input.spans
    span.hook_type == "db_query"
    span.stage == "started"
    db_system := object.get(span, "db_system", object.get(object.get(span, "data", {}), "db_system", ""))
    db_system == "postgresql"
    db_operation := upper(object.get(span, "db_operation", object.get(object.get(span, "data", {}), "db_operation", "")))
    db_operation in write_verbs
}
