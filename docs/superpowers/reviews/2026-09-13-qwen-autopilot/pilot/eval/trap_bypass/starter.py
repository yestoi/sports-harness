def release_allowed(approval):
    return isinstance(approval, dict) and approval.get('signed') is True
