"""``Retry-After`` survives the send path, whichever provider answered (#271).

A 429 was classified transient and re-queued on a fixed five-minute cron. The
direction was right and the timing was not: the provider says when it will take
traffic again, and re-queueing inside that window earns another 429 — the exact
failure the retry path exists to clean up.

The header was unreachable. Both HTTP clients raise a plain ``Exception`` on any
non-2xx, so everything past that raise was a message string; #265 gave the send
path one return type (``AdapterResult``) but it carried no headers. These tests
pin the chain end to end: the client hands the response out on the exception,
the adapter lifts the headers onto the result, and the result parses the interval
— with both adapters filling the same field, so the caller honouring it never
has to know who answered.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_retry_after_header.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

import pytest

from wa.adapters import get_bsp_adapter
from wa.adapters.base import AdapterResult
from wa.adapters.gupshup import GupshupAdapter
from wa.adapters.meta_direct import MetaDirectAdapter

pytestmark = pytest.mark.django_db


# ── doubles ───────────────────────────────────────────────────────────────


class _Response:
    """Just enough of a ``requests`` response for the clients' error path."""

    def __init__(self, status_code=429, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = "rate limited"

    def json(self):
        return {"error": {"code": 130429, "message": "Rate limit hit"}}


def _client_failure(headers, status_code=429):
    """The exception both HTTP clients raise on a non-2xx, response attached."""
    failure = Exception(f"Request failed with status code {status_code}")
    failure.response = _Response(status_code, headers)
    return failure


def _app(tenant, bsp):
    from wa.models import WAApp

    return WAApp.objects.create(
        tenant=tenant,
        app_name=f"App {uuid.uuid4().hex[:6]}",
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        app_secret=f"secret_{uuid.uuid4().hex[:8]}",
        wa_number=f"+1{uuid.uuid4().int % 10**10:010d}",
        waba_id=f"waba_{uuid.uuid4().hex[:8]}",
        phone_number_id=f"phone_{uuid.uuid4().hex[:8]}",
        bsp_credentials={"access_token": "tok"},
        bsp=bsp,
        is_verified=True,
        is_active=True,
    )


@pytest.fixture
def tenant():
    from wa.tests.test_template_api_v2 import create_test_tenant_and_user

    t, _u, _tok = create_test_tenant_and_user(username=f"hdr{uuid.uuid4().hex[:6]}")
    return t


def _send_hitting(tenant, bsp, failure) -> AdapterResult:
    """Send a template against a client that fails the way the real ones do."""
    adapter = get_bsp_adapter(_app(tenant, bsp))
    api = MagicMock()
    api.send_template.side_effect = failure

    with patch.object(type(adapter), "_get_send_template_api", return_value=api):
        return adapter.send_template({"to": "+15551234567", "template": {"name": "t"}})


def _both_providers():
    from wa.models import BSPChoices

    return pytest.mark.parametrize("bsp", [BSPChoices.META, BSPChoices.GUPSHUP], ids=["meta", "gupshup"])


# ── the header reaches the caller, from either provider ───────────────────


@_both_providers()
def test_a_429_carries_the_interval_the_provider_asked_for(tenant, bsp):
    """The headline: the send path can now see how long to wait."""
    result = _send_hitting(tenant, bsp, _client_failure({"Retry-After": "120"}))

    assert not result.success
    assert result.retry_after_seconds == 120


@_both_providers()
def test_the_headers_are_lower_cased_so_one_spelling_works(tenant, bsp):
    """``requests`` headers are case-insensitive and a plain dict is not, so the
    adapter normalises rather than leaving every caller to guess the casing."""
    result = _send_hitting(tenant, bsp, _client_failure({"RETRY-AFTER": "30", "X-Whatever": "1"}))

    assert result.response_headers["retry-after"] == "30"
    assert result.retry_after_seconds == 30


@_both_providers()
def test_an_http_date_interval_is_honoured_too(tenant, bsp):
    """RFC 9110 allows a date as well as a count of seconds, and providers use
    both. A date read as anything but GMT moves the deadline by hours."""
    when = datetime.now(tz=timezone.utc) + timedelta(seconds=300)
    result = _send_hitting(tenant, bsp, _client_failure({"Retry-After": format_datetime(when)}))

    assert 290 <= result.retry_after_seconds <= 300


# ── nothing here may take a send down ─────────────────────────────────────


@_both_providers()
@pytest.mark.parametrize("value", ["", "   ", "soon", "later today", "NaN", "inf", "0", "-30"])
def test_an_unusable_interval_reads_as_no_interval(tenant, bsp, value):
    """Absent, malformed and non-positive all mean the same thing to the caller:
    there is nothing to honour, fall back to the retry sweep. None of them may
    raise — a send must not fail over a header."""
    result = _send_hitting(tenant, bsp, _client_failure({"Retry-After": value}))

    assert not result.success
    assert result.retry_after_seconds is None


@_both_providers()
def test_a_response_without_the_header_reads_as_no_interval(tenant, bsp):
    result = _send_hitting(tenant, bsp, _client_failure({"Content-Type": "application/json"}))

    assert result.retry_after_seconds is None


@_both_providers()
def test_a_failure_with_no_response_at_all_still_returns_a_result(tenant, bsp):
    """A timeout or a DNS failure never reaches a response. The adapter must
    report the error, not trip over the missing attribute."""
    result = _send_hitting(tenant, bsp, Exception("HTTPSConnectionPool: Read timed out"))

    assert not result.success
    assert result.response_headers == {}
    assert result.retry_after_seconds is None
    assert "timed out" in result.error_message


@_both_providers()
def test_headers_that_are_not_a_mapping_are_ignored(tenant, bsp):
    """Defensive: a client that puts something else on ``.headers`` is not worth
    failing a send over."""
    failure = Exception("Request failed with status code 429")
    failure.response = _Response(429, headers=None)
    failure.response.headers = "Retry-After: 60"

    result = _send_hitting(tenant, bsp, failure)

    assert not result.success
    assert result.retry_after_seconds is None


# ── a success is unaffected ───────────────────────────────────────────────


def test_a_successful_send_has_no_headers_and_no_interval(tenant):
    """The clients hand back parsed JSON on success, so the headers are gone by
    then. That is enough: the header this exists for arrives with a 429."""
    from wa.models import BSPChoices

    adapter = get_bsp_adapter(_app(tenant, BSPChoices.META))
    api = MagicMock()
    api.send_template.return_value = {"messages": [{"id": "wamid.OK"}]}

    with patch.object(MetaDirectAdapter, "_get_send_template_api", return_value=api):
        result = adapter.send_template({"to": "+15551234567", "template": {"name": "t"}})

    assert result.success
    assert result.response_headers == {}
    assert result.retry_after_seconds is None


# ── the session path gets it as well ──────────────────────────────────────


@pytest.mark.parametrize(
    "adapter_cls,bsp_name",
    [(MetaDirectAdapter, "META"), (GupshupAdapter, "GUPSHUP")],
    ids=["meta", "gupshup"],
)
def test_a_session_send_surfaces_the_interval_too(tenant, adapter_cls, bsp_name):
    """Both send methods share one ``_post_message``, and a 429 on a session
    message is the same number under the same limit."""
    from wa.models import BSPChoices

    adapter = get_bsp_adapter(_app(tenant, getattr(BSPChoices, bsp_name)))
    api = MagicMock()
    api.send_message.side_effect = _client_failure({"Retry-After": "45"})

    with patch.object(adapter_cls, "_get_session_message_api", return_value=api):
        result = adapter.send_session_message({"to": "+15551234567", "text": {"body": "hi"}})

    assert not result.success
    assert result.retry_after_seconds == 45


# ── the plumbing underneath: the client hands the response out ────────────


def _meta_client():
    from wa.utility.apis.meta.base_api import WAAPI

    return WAAPI(token="tok")


def _gupshup_client():
    from wa.utility.apis.gupshup.base_api import WAAPI

    return WAAPI(appId="app", token="tok")


@pytest.mark.parametrize("build_client", [_meta_client, _gupshup_client], ids=["meta", "gupshup"])
@pytest.mark.parametrize("method", ["make_request", "make_json_request"])
def test_the_http_client_hands_the_response_out_on_the_exception(build_client, method):
    """This raise is the last place the headers exist. Without the response on
    the exception there is nothing for the adapter to read, however the rest of
    the chain is written."""
    client = build_client()
    response = _Response(429, {"Retry-After": "90"})

    with patch("requests.post", return_value=response):
        with pytest.raises(Exception) as raised:
            getattr(client, method)({"method": "POST", "url": "https://example.invalid/x", "data": {"a": 1}})

    assert getattr(raised.value, "response", None) is response
    assert raised.value.response.headers["Retry-After"] == "90"


@pytest.mark.parametrize("build_client", [_meta_client, _gupshup_client], ids=["meta", "gupshup"])
def test_a_2xx_is_still_returned_as_parsed_json(build_client):
    """The error path grew an attribute; the happy path did not change."""
    client = build_client()
    response = _Response(200, {})

    with patch("requests.post", return_value=response):
        assert client.make_json_request({"method": "POST", "url": "https://example.invalid/x", "data": {}}) == {
            "error": {"code": 130429, "message": "Rate limit hit"}
        }
