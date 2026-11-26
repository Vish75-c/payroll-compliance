"""Business-rule errors and role checks shared across features."""
from __future__ import annotations


class RuleViolation(Exception):
    """A request that breaks a business rule (wrong state, four-eyes, stale data...). Never retried."""

    def __init__(self, message: str, code: str = "rule_violation"):
        super().__init__(message)
        self.message = message
        self.code = code


def is_in_group(user, group: str) -> bool:
    return bool(user) and (user.is_superuser or user.groups.filter(name=group).exists())


# How each role is named where people read it.
ROLE_NAMES = {"maker": "a finance team employee", "checker": "the finance head"}


def require_role(user, group: str, what: str):
    if not is_in_group(user, group):
        raise RuleViolation(f"Only {ROLE_NAMES.get(group, 'a ' + group)} can {what}.", code="forbidden")
