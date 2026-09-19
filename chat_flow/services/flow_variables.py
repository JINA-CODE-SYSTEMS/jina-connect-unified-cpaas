"""The variables a flow can substitute, in one place.

The API node's body hint reads "Use {{variable}} to insert contact
attributes" and never says which attributes. It could not: the set was a
closure inside ``graph_executor``, written as six ``setdefault`` calls, so
nothing outside that function could list it — not the editor, not the rules,
not the operator staring at an empty body box.

Each variable is now one row carrying its own key, its description and how it
is read off the contact. The executor substitutes from this table and
``GET /chat-flow/flows/variables/`` publishes it, so the list the operator
sees is the list that gets substituted.

Note this is *not* the same set as ``TenantContact.RESERVED_VARS``, which is
what a WhatsApp template placeholder resolves against. Templates and flows
answer the same question in two places; merging them is #265's shape and is
not attempted here, so the endpoint says which surface it is describing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContactVariable:
    """A ``{{placeholder}}`` an operator may write, and where its value comes from."""

    key: str
    description: str
    resolve: Callable[[Any], str]


CONTACT_VARIABLES: Tuple[ContactVariable, ...] = (
    ContactVariable("first_name", "The contact's first name", lambda c: c.first_name or ""),
    ContactVariable("last_name", "The contact's last name", lambda c: c.last_name or ""),
    ContactVariable("full_name", "First and last name together", lambda c: c.full_name or ""),
    ContactVariable("contact_name", "Same as full_name, kept for older flows", lambda c: c.full_name or ""),
    ContactVariable("phone", "The contact's phone number, with country code", lambda c: str(c.phone) if c.phone else ""),
    ContactVariable("email", "The contact's email address, if one is stored", lambda c: getattr(c, "email", "") or ""),
    ContactVariable("tag", "The tag on the contact record", lambda c: c.tag or ""),
    ContactVariable("status", "The contact's status", lambda c: c.status or ""),
    ContactVariable(
        "assigned_team",
        "The team the contact is assigned to, if any",
        lambda c: str(c.assigned_to_id) if c.assigned_to_type == "TEAM" else "",
    ),
)

#: Values that come from the running session rather than the contact row.
LAST_MESSAGE = "last_message"

SESSION_VARIABLES: Tuple[Dict[str, str], ...] = (
    {"key": LAST_MESSAGE, "description": "The contact's most recent reply"},
)


def published_contact_variables() -> list[dict]:
    """The table as the editor consumes it."""
    return [{"key": v.key, "description": v.description} for v in CONTACT_VARIABLES]


def published_session_variables() -> list[dict]:
    return [dict(v) for v in SESSION_VARIABLES]


def resolve_variable(state: Dict[str, Any], variable: str) -> str:
    """One variable's value for a condition node: session, then context, then contact.

    Condition nodes kept their own field map with three fields the API node
    had never heard of and one it had (``full_name``) that conditions could
    not see. Both now read :data:`CONTACT_VARIABLES`.
    """
    from contacts.models import TenantContact

    if variable == LAST_MESSAGE:
        return str(state.get("user_input") or "")

    context = state.get("context", {})
    if variable in context:
        return str(context[variable])

    contact_id = state.get("contact_id")
    if not contact_id:
        return ""

    resolver = next((v.resolve for v in CONTACT_VARIABLES if v.key == variable), None)
    if resolver is None:
        return ""

    try:
        return str(resolver(TenantContact.objects.get(id=contact_id)) or "")
    except TenantContact.DoesNotExist:
        logger.warning("Cannot resolve {{%s}}: contact %s not found", variable, contact_id)
        return ""


def build_placeholder_vars(state: Dict[str, Any]) -> Dict[str, Any]:
    """Flow context plus contact fields, for ``{{placeholder}}`` substitution.

    Context wins: a variable an earlier API node stored under ``first_name``
    is the one the operator meant, not the contact row.
    """
    from contacts.models import TenantContact

    context = dict(state.get("context", {}))
    contact_id = state.get("contact_id")
    if not contact_id:
        return context

    try:
        contact = TenantContact.objects.get(id=contact_id)
    except TenantContact.DoesNotExist:
        return context

    for variable in CONTACT_VARIABLES:
        try:
            context.setdefault(variable.key, variable.resolve(contact))
        except Exception:  # noqa: BLE001 — one unreadable field must not stop a flow
            logger.warning("Could not resolve {{%s}} for contact %s", variable.key, contact_id)
            context.setdefault(variable.key, "")

    return context
