package openbox

default result := {"decision": "CONTINUE", "reason": ""}

is_write_span(span) if {
    span.semantic_type == "file_write"
}

is_write_span(span) if {
    span.file_operation == "write"
}

span_path(span) := path if {
    path := object.get(span, "file_path", "")
    path != ""
}

span_path(span) := path if {
    path := object.get(object.get(span, "data", {}), "file_path", "")
    path != ""
}

# Block writes to paths containing "restricted".
result := {"decision": "BLOCK", "reason": "File write to restricted path blocked by policy"} if {
    some span in input.spans
    span.hook_type == "file_operation"
    span.stage == "started"
    is_write_span(span)
    path := span_path(span)
    contains(path, "restricted")
}
