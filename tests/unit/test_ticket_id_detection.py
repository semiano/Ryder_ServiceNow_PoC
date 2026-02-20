from function_app import is_guid_ticket_id, resolve_ticket_key_type


def test_guid_detection_true_for_canonical_guid() -> None:
    ticket_id = "123e4567-e89b-42d3-a456-426614174000"
    assert is_guid_ticket_id(ticket_id) is True
    assert resolve_ticket_key_type(ticket_id) == "sys_id"


def test_guid_detection_false_for_incident_number() -> None:
    ticket_id = "INC0012345"
    assert is_guid_ticket_id(ticket_id) is False
    assert resolve_ticket_key_type(ticket_id) == "number"
