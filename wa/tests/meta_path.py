"""Shared scaffolding for the Meta-path integration tests (#277).

Not a test module — pytest collects ``test_*.py`` only, and this file is
imported by the five ``test_meta_path_*`` modules across ``wa``,
``broadcast``, ``chat_flow`` and ``team_inbox``.

Why it exists
-------------
#277's closing finding is that every one of the five audited areas had no
test exercising the Meta path, and that the unit tests around each area
passed *through* the bug. Three properties turn a unit test into one that
would have caught these, and all three are easy to lose by accident — so
they live here once rather than in five files:

1. **A real Meta app.** ``meta_wa_app()`` sets ``bsp=META`` explicitly plus
   a ``waba_id``, a ``phone_number_id``, a ``meta_app_id`` and a token in
   ``bsp_credentials``. A blank ``bsp`` column also resolves to Meta
   (``DEFAULT_BSP``), so a test built on the default would pass whether or
   not the Meta branch was ever reached — which is half of #265.

2. **A mock at the HTTP boundary, not at the adapter.** ``FakeGraph``
   replaces ``requests.get``/``post``/``put``/``delete`` and records every
   call. Patching ``get_bsp_adapter`` with a ``MagicMock`` — which the
   existing broadcast tests do, legitimately, for what they test — cannot
   tell a Meta send from a Gupshup one. Here the request shape *is* the
   assertion: ``assert_meta_call`` checks the Graph host, the path and the
   bearer token the per-tenant credentials should have produced.

3. **No silent fallthrough.** An unrouted request raises. A Meta-path test
   whose Meta call never happens must fail loudly, not quietly pass on a
   default.

``sign_meta_webhook`` posts to the real public receiver with a valid
``X-Hub-Signature-256`` so the tests enter through the same door Meta uses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

#: The app secret the tests configure, and sign with. Any value works; it
#: only has to be the same one ``settings.META_APP_SECRET`` holds, which the
#: test modules set via ``override_settings`` / the ``settings`` fixture.
APP_SECRET = "meta-path-test-app-secret"

#: Graph host and version, spelled out so a test asserts against the real
#: endpoint rather than against whatever the client happened to build.
GRAPH_HOST = "https://graph.facebook.com"
GRAPH_VERSION = "v24.0"
GRAPH_BASE = f"{GRAPH_HOST}/{GRAPH_VERSION}/"

WEBHOOK_URL = "/wa/v2/webhooks/meta/"


# ─────────────────────────────────────────────────────────────────────────────
# Model factories
# ─────────────────────────────────────────────────────────────────────────────


def tenant(name: str = "MetaPath") -> Any:
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"{name}-{uuid.uuid4().hex[:8]}", is_active=True)


def meta_wa_app(
    owner,
    *,
    waba_id: str | None = None,
    phone_number_id: str | None = None,
    access_token: str = "meta-tenant-token",
    **overrides,
) -> Any:
    """A ``TenantWAApp`` that is unambiguously on Meta Direct.

    ``bsp`` is written explicitly. The column's default is ``GUPSHUP`` and a
    blank value resolves to Meta, so neither the default nor a blank proves
    the Meta branch ran — only an explicit ``META`` does.

    The token goes in ``bsp_credentials`` rather than relying on the global
    ``META_PERM_TOKEN``, which is the configuration #275 is about and the
    one the request assertions below can actually distinguish.
    """
    from tenants.models import BSPChoices
    from wa.models import WAApp

    suffix = uuid.uuid4().hex[:8]
    fields = {
        "tenant": owner,
        "app_name": f"meta-app-{suffix}",
        "app_id": f"gs_{suffix}",
        "app_secret": f"secret_{suffix}",
        "meta_app_id": f"fbapp_{suffix}",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": waba_id or f"waba_{suffix}",
        "phone_number_id": phone_number_id or f"pn_{suffix}",
        "bsp": BSPChoices.META,
        "bsp_credentials": {"access_token": access_token},
        "is_verified": True,
        "is_active": True,
    }
    fields.update(overrides)
    return WAApp.objects.create(**fields)


def wa_template(app, *, element_name: str | None = None, **overrides) -> Any:
    """A ``WATemplate`` on *app*, linked through a ``TemplateNumber``.

    The ``TemplateNumber`` link is what ``Broadcast.template_number`` walks
    to reach the template (and from there the ``wa_app``), so broadcasts
    created against this template resolve their sending number the way
    production does.
    """
    from message_templates.models import TemplateNumber
    from wa.models import TemplateStatus, WATemplate

    name = element_name or f"tpl_{uuid.uuid4().hex[:8]}"
    fields = {
        "wa_app": app,
        "number": TemplateNumber.objects.create(),
        "name": name,
        "element_name": name,
        "language_code": "en",
        "category": "MARKETING",
        "template_type": "TEXT",
        "status": TemplateStatus.APPROVED,
        "content": "Hello there",
        "needs_sync": False,
    }
    fields.update(overrides)
    return WATemplate.objects.create(**fields)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP boundary
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Call:
    """One outbound HTTP request, as the production client made it."""

    method: str
    url: str
    params: dict = field(default_factory=dict)
    json: Any = None
    data: Any = None
    headers: dict = field(default_factory=dict)

    @property
    def authorization(self) -> str:
        for key, value in (self.headers or {}).items():
            if key.lower() == "authorization":
                return str(value)
        return ""

    @property
    def path(self) -> str:
        """The URL with the Graph base stripped, or the URL unchanged."""
        return self.url[len(GRAPH_BASE) :] if self.url.startswith(GRAPH_BASE) else self.url


class FakeResponse:
    """Enough of ``requests.Response`` for every Meta client in the repo."""

    def __init__(self, payload: Any = None, *, status_code: int = 200, content: bytes = b"", headers: dict = None):
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.content = content if content else json.dumps(self._payload).encode()
        self.headers = headers or {}

    @property
    def text(self) -> str:
        return self.content.decode(errors="replace")

    def json(self) -> Any:
        return self._payload

    def iter_content(self, chunk_size: int = 8192):
        yield self.content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} for fake response")


class UnroutedRequest(AssertionError):
    """Raised when production code makes a call the test did not expect.

    Deliberately fatal. The whole point of these tests is that the Meta
    request happened and had the right shape; a default response for an
    unexpected URL would let a test pass while calling the wrong endpoint,
    or the wrong provider's.
    """


class FakeGraph:
    """Records outbound HTTP and answers it from routes the test registers.

    Routes match on ``(method, substring of URL)`` in registration order.
    The response may be a ``FakeResponse``, a plain dict (wrapped in a 200),
    or a callable taking the :class:`Call` — the callable form is how a test
    answers paginated reads differently on each page.
    """

    def __init__(self):
        self.calls: list[Call] = []
        self._routes: list[tuple[str, str, Any]] = []

    # ── route registration ────────────────────────────────────────────
    def on(self, method: str, url_contains: str, response: Any) -> "FakeGraph":
        self._routes.append((method.upper(), url_contains, response))
        return self

    def get(self, url_contains: str, response: Any) -> "FakeGraph":
        return self.on("GET", url_contains, response)

    def reset_routes(self) -> "FakeGraph":
        """Drop every route, keeping the recorded calls.

        For a test that runs the same operation twice and needs the second run
        answered differently — routes match in registration order, so adding a
        second route for the same URL would never be reached.
        """
        self._routes.clear()
        return self

    def post(self, url_contains: str, response: Any) -> "FakeGraph":
        return self.on("POST", url_contains, response)

    # ── installation ──────────────────────────────────────────────────
    def install(self, monkeypatch) -> "FakeGraph":
        """Replace ``requests``' verb functions for the duration of the test.

        The Meta clients all do a function-local ``import requests`` and then
        ``requests.post(...)``, so the attribute is looked up at call time and
        patching the module attribute is enough — including for
        ``template_sync``'s module-level ``import requests as http_requests``.
        """
        import requests

        for verb in ("get", "post", "put", "delete"):
            monkeypatch.setattr(requests, verb, self._verb(verb.upper()))
        return self

    def _verb(self, method: str) -> Callable:
        def _call(url, **kwargs):
            call = Call(
                method=method,
                url=str(url),
                params=kwargs.get("params") or {},
                json=kwargs.get("json"),
                data=kwargs.get("data"),
                headers=kwargs.get("headers") or {},
            )
            self.calls.append(call)
            return self._answer(call)

        return _call

    def _answer(self, call: Call) -> FakeResponse:
        for method, fragment, response in self._routes:
            if method == call.method and fragment in call.url:
                if callable(response):
                    response = response(call)
                if isinstance(response, FakeResponse):
                    return response
                return FakeResponse(response)
        raise UnroutedRequest(
            f"No route for {call.method} {call.url}\nRegistered: " + ", ".join(f"{m} *{f}*" for m, f, _ in self._routes)
        )

    # ── assertions ────────────────────────────────────────────────────
    def only(self, method: str, path_contains: str) -> Call:
        """The single call matching ``(method, path_contains)``.

        Fails when there is none — an integration test that asserts nothing
        about the request it claims to make is the blind spot, not the fix —
        and when there is more than one, because "which one" then matters.
        """
        found = [c for c in self.calls if c.method == method.upper() and path_contains in c.url]
        assert found, f"no {method.upper()} to *{path_contains}*; calls were: {[(c.method, c.url) for c in self.calls]}"
        assert len(found) == 1, f"expected one {method.upper()} to *{path_contains}*, got {len(found)}"
        return found[0]

    def all(self, method: str, path_contains: str) -> list[Call]:
        return [c for c in self.calls if c.method == method.upper() and path_contains in c.url]


def assert_meta_call(call: Call, *, path: str, token: str, scheme: str = "Bearer") -> None:
    """The request really went to Graph, on this path, as this tenant.

    ``token`` is checked because it is the only thing in the request that
    distinguishes the per-app credential from the deployment-wide
    ``META_PERM_TOKEN`` fallback, and the path because the Cloud API uses
    one host for template CRUD, sends, media and uploads — a send posted to
    the template edge would otherwise look identical to a correct one.
    """
    assert call.url.startswith(GRAPH_BASE), f"not a Graph {GRAPH_VERSION} URL: {call.url}"
    assert call.path.split("?")[0] == path, f"expected path {path!r}, got {call.path!r}"
    assert call.authorization == f"{scheme} {token}", f"expected {scheme} token {token!r}, got {call.authorization!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Inbound webhook delivery
# ─────────────────────────────────────────────────────────────────────────────


def sign_meta_webhook(client, payload: dict, *, secret: str = APP_SECRET):
    """POST *payload* to the public Meta receiver, signed as Meta signs it.

    Entering through the real view matters: the receiver verifies the
    signature, classifies the event, picks the ``WAApp`` by
    ``phone_number_id``/``waba_id`` and persists a ``WAWebhookEvent`` whose
    ``post_save`` starts processing. A test that builds the event row by hand
    skips all five of those, and #265 and #309 both lived in that stretch.
    """
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        WEBHOOK_URL,
        data=raw,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature,
    )


def run_webhooks_in_process(settings) -> None:
    """Make the ``WAWebhookEvent`` ``post_save`` process the event inline.

    ``wa.signals._dispatch`` queues through Celery when
    ``CELERY_BROKER_URL`` is set and otherwise runs the task in-process. In
    CI a broker *is* reachable and ``conftest`` leaves eager mode off, so
    ``.delay`` really enqueues and the assertion "a task was queued" passes
    while nothing has run. Blanking the broker takes the in-process branch,
    so these tests exercise the processing they are about in both
    environments rather than only on a developer's machine.
    """
    settings.CELERY_BROKER_URL = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = False


# ─────────────────────────────────────────────────────────────────────────────
# Payload builders — the shapes Meta actually delivers
# ─────────────────────────────────────────────────────────────────────────────


def text_message(wamid: str, body: str, *, sender: str = "27821234567") -> dict:
    return {
        "id": wamid,
        "from": sender,
        "timestamp": "1789000000",
        "type": "text",
        "text": {"body": body},
    }


def image_message(wamid: str, media_id: str, *, caption: str = "", sender: str = "27821234567") -> dict:
    image: dict = {"id": media_id, "mime_type": "image/jpeg"}
    if caption:
        image["caption"] = caption
    return {
        "id": wamid,
        "from": sender,
        "timestamp": "1789000001",
        "type": "image",
        "image": image,
    }


def messages_value(app, *messages: dict, sender: str = "27821234567", profile_name: str = "Thandi") -> dict:
    """One ``changes[].value`` carrying *messages* from one sender.

    ``contacts`` and ``metadata`` appear once per value, not per message —
    Meta's real batching shape, and the reason a per-message slice has to
    carry them forward (#268).
    """
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": str(app.wa_number).lstrip("+"),
            "phone_number_id": app.phone_number_id,
        },
        "contacts": [{"wa_id": sender, "profile": {"name": profile_name}}],
        "messages": list(messages),
    }


def inbound_envelope(app, *values: dict) -> dict:
    """A delivery with one ``entry`` holding one ``changes`` per value."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": app.waba_id,
                "changes": [{"field": "messages", "value": v} for v in values],
            }
        ],
    }


def multi_entry_envelope(app, *entries: list) -> dict:
    """A delivery with several ``entry`` items, each holding several changes.

    Meta batches on all three levels — entries, changes and messages — and
    the parser read ``[0]`` at each (#268). Only a payload that is plural at
    every level distinguishes a fix at one level from a fix at all three.
    """
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": app.waba_id, "changes": [{"field": "messages", "value": v} for v in values]} for values in entries
        ],
    }


def template_status_envelope(app, *, field: str, value: dict) -> dict:
    """A template lifecycle delivery (status / category / quality update)."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": app.waba_id, "changes": [{"field": field, "value": value}]}],
    }


__all__ = [
    "APP_SECRET",
    "GRAPH_BASE",
    "GRAPH_HOST",
    "GRAPH_VERSION",
    "WEBHOOK_URL",
    "Call",
    "FakeGraph",
    "FakeResponse",
    "UnroutedRequest",
    "assert_meta_call",
    "image_message",
    "inbound_envelope",
    "messages_value",
    "meta_wa_app",
    "multi_entry_envelope",
    "run_webhooks_in_process",
    "sign_meta_webhook",
    "template_status_envelope",
    "tenant",
    "text_message",
    "wa_template",
]
