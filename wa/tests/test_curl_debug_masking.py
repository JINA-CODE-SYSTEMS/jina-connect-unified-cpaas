"""A BSP credential never reaches stdout, a log record or ``last_curl_command`` (#336).

Every BSP client reconstructs its outbound request as a pasteable ``curl``
command. The reconstruction emitted the ``Authorization`` header verbatim and was
written with ``print``, so each Graph call published a live per-tenant access
token — and ``last_curl_command`` is read back by ``wa.tasks``, which returns it
in a task result a caller may store. So this was not only a log leak.

(#336 also named a template debug blob in ``meta_template_service`` as a
persisted copy. #337 established that neither that blob nor the three in
``wa.tasks`` could ever execute — they wrote a ``submission_debug_info``
attribute that was not a model field — and deleted them, so the blobs are gone
and masking at the build site is what covers what is left.)

These tests assert the *consequence* rather than that a masking helper was
called: one request per client against a mocked transport, then the synthetic
token is looked for in the real captured stdout, in the real captured log
records, and in the string the client kept. A helper can be called and still
leak; captured output cannot.

They also pin the other half of the acceptance criterion, which is easy to
satisfy by deleting the diagnostic altogether: the method, URL, body and
non-credential headers must still be recoverable from a failed submission.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_curl_debug_masking.py -v
"""

from __future__ import annotations

import io
import json
import logging
from unittest.mock import patch

import pytest

from wa.utility.apis.curl_debug import REDACTED

# ── synthetic credentials ─────────────────────────────────────────────────
#
# Long and distinctive so a single substring search over captured output is
# conclusive, and so the value-level scrub (which ignores anything under 8
# characters) is exercised rather than only the name-based masking. None of these
# is a real credential shape from any provider; bandit skips the tests tree, so
# the names can say plainly what each one stands in for.

SENTINEL_BEARER = "EAAsyntheticBEARER0000000000000000notreal"
SENTINEL_PARTNER = "sk_syntheticPARTNER0000000000000notreal"
SENTINEL_APP_SECRET = "synthetiicAPPSECRET00000000000000notreal"
SENTINEL_API_KEY = "synthetiicAPIKEY000000000000000000notreal"

ALL_SENTINELS = (SENTINEL_BEARER, SENTINEL_PARTNER, SENTINEL_APP_SECRET, SENTINEL_API_KEY)


# ── doubles ───────────────────────────────────────────────────────────────


class _Response:
    """Just enough of a ``requests`` response for the clients to walk."""

    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}
        self._payload = payload if payload is not None else {"id": "1234567890"}
        self.text = json.dumps(self._payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=8192):
        yield self.content


@pytest.fixture
def logs(caplog):
    """Capture records from the BSP clients.

    ``wa`` and ``wa.utility.apis`` both carry ``propagate: False`` in settings
    (that is the switch a deployment throws to silence this output), so records
    never reach the root logger ``caplog`` listens on. Attaching caplog's own
    handler to the client logger is what makes the capture real — without it the
    absence assertions below would pass on an empty capture and prove nothing,
    which is why each test also asserts the masked command *was* captured.
    """
    client_logger = logging.getLogger("wa.utility.apis")
    previous_level = client_logger.level
    client_logger.setLevel(logging.DEBUG)
    client_logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        yield caplog
    finally:
        client_logger.removeHandler(caplog.handler)
        client_logger.setLevel(previous_level)


def _everything_written(capsys, logs) -> str:
    """Every byte a human could have seen: stdout, stderr and the log records."""
    captured = capsys.readouterr()
    return "\n".join([captured.out, captured.err, logs.text] + [record.getMessage() for record in logs.records])


def _assert_no_credential_anywhere(written: str, kept: str) -> None:
    for sentinel in ALL_SENTINELS:
        assert sentinel not in written, f"{sentinel} reached stdout or a log record"
        assert sentinel not in kept, f"{sentinel} is held on last_curl_command"


# ── client builders ───────────────────────────────────────────────────────


def _meta_client():
    from wa.utility.apis.meta.base_api import WAAPI

    return WAAPI(token=SENTINEL_BEARER)


def _gupshup_client():
    from wa.utility.apis.gupshup.base_api import WAAPI

    return WAAPI(appId="app1", token=SENTINEL_PARTNER)


def _wati_client():
    from wa.utility.apis.wati.base_api import WAAPI

    return WAAPI(api_endpoint="tenant.example.invalid", token=SENTINEL_BEARER)


_CLIENTS = pytest.mark.parametrize(
    "build_client",
    [_meta_client, _gupshup_client, _wati_client],
    ids=["meta", "gupshup", "wati"],
)
_METHODS = pytest.mark.parametrize("api_method", ["make_request", "make_json_request"])


# ── the headline ──────────────────────────────────────────────────────────


@_CLIENTS
@_METHODS
def test_one_request_publishes_no_credential_anywhere(build_client, api_method, capsys, logs):
    """The acceptance criterion, for every client and both request shapes."""
    client = build_client()

    with patch("requests.post", return_value=_Response()):
        getattr(client, api_method)(
            {
                "method": "POST",
                "url": "https://graph.example.invalid/v24.0/1234/messages",
                # ``make_request`` takes its headers from the caller, the way the
                # send and template paths pass them; ``make_json_request``
                # defaults to ``self.json_headers``.
                "headers": client.headers,
                "data": {"messaging_product": "whatsapp", "to": "+15551234567"},
            }
        )

    written = _everything_written(capsys, logs)

    # The capture is live: the masked command really was logged.
    assert "EQUIVALENT CURL COMMAND" in written
    assert REDACTED in client.last_curl_command

    _assert_no_credential_anywhere(written, client.last_curl_command)


@_CLIENTS
def test_nothing_is_written_to_stdout_at_all(build_client, capsys, logs):
    """``print`` cannot be filtered, which is why none of this may use it: a
    deployment turns the output off by raising the ``wa.utility.apis`` level."""
    client = build_client()

    with patch("requests.post", return_value=_Response()):
        client.make_json_request({"method": "POST", "url": "https://graph.example.invalid/x", "data": {"a": 1}})

    captured = capsys.readouterr()
    assert captured.out == "", f"something still prints: {captured.out[:200]}"
    assert logs.records, "the diagnostic went nowhere at all, which is not the fix either"


@_CLIENTS
def test_a_failed_submission_stays_diagnosable(build_client, capsys, logs):
    """The non-2xx path is the one anybody actually reads. Masking it by deleting
    it would pass the credential assertions and lose the reason the output
    exists, so the request has to still be reconstructable from what is written."""
    client = build_client()
    rejected = _Response(400, {"error": {"message": "Template name already exists"}})

    with patch("requests.post", return_value=rejected):
        with pytest.raises(Exception) as raised:
            client.make_json_request(
                {
                    "method": "POST",
                    "url": "https://graph.example.invalid/v24.0/1234/message_templates",
                    "data": {"name": "order_update", "language": "en_US"},
                }
            )

    written = _everything_written(capsys, logs)

    _assert_no_credential_anywhere(written, client.last_curl_command)

    # Still recoverable: where it went, how, and with what.
    assert "graph.example.invalid/v24.0/1234/message_templates" in written
    assert "POST" in written
    assert "order_update" in written
    assert "Content-Type: application/json" in client.last_curl_command
    # And the provider's reason still rides out on the exception.
    assert "Template name already exists" in str(raised.value)


@_CLIENTS
def test_the_mask_keeps_the_auth_scheme(build_client, capsys, logs):
    """``Bearer [redacted]`` beats ``[redacted]``: substitute your own token and
    the command runs, and a request sent with the wrong scheme is still visible."""
    client = build_client()

    with patch("requests.post", return_value=_Response()):
        client.make_json_request({"method": "POST", "url": "https://graph.example.invalid/x", "data": {"a": 1}})

    kept = client.last_curl_command
    assert f"Authorization: Bearer {REDACTED}" in kept or f"Authorization: {REDACTED}" in kept
    _assert_no_credential_anywhere(_everything_written(capsys, logs), kept)


# ── every credential-shaped header, not just Authorization ────────────────


def test_a_header_added_later_is_masked_by_its_name(capsys, logs):
    """#311 added an app secret and #306 will read it. Masking a fixed list of
    known credentials would miss it; masking by the *shape of the name* does not."""
    client = _meta_client()

    with patch("requests.post", return_value=_Response()):
        client.make_json_request(
            {
                "method": "POST",
                "url": "https://graph.example.invalid/x",
                "json_headers": {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {SENTINEL_BEARER}",
                    "X-App-Secret": SENTINEL_APP_SECRET,
                    "x-api-key": SENTINEL_API_KEY,
                    "X-Request-Id": "req-42",
                },
                "data": {"a": 1},
            }
        )

    kept = client.last_curl_command
    _assert_no_credential_anywhere(_everything_written(capsys, logs), kept)

    # The harmless headers are untouched — they are half the diagnostic.
    assert "X-Request-Id: req-42" in kept
    assert "Content-Type: application/json" in kept
    assert kept.count(REDACTED) >= 3


def test_a_credential_in_the_query_string_is_masked_too(capsys, logs):
    """Graph takes ``?access_token=`` as an alternative to the header, so a
    header-only mask would still publish the token for those calls.

    The value here is deliberately *not* the client's own token: scrubbing by
    value would hide a weakness in the query-string masking otherwise.
    """
    client = _meta_client()
    url = f"https://graph.example.invalid/v24.0/1234?fields=name&access_token={SENTINEL_API_KEY}"

    with patch("requests.get", return_value=_Response()):
        client.make_json_request({"method": "GET", "url": url, "data": {}})

    kept = client.last_curl_command
    _assert_no_credential_anywhere(_everything_written(capsys, logs), kept)
    assert "fields=name" in kept, "masking the query string must not erase the rest of it"


def test_a_credential_in_the_body_is_masked_too(capsys, logs):
    """The resumable-upload path passes ``access_token`` as a parameter rather
    than a header, and a body is reconstructed verbatim by design.

    Again a value that is not the client's own token, so only the body masking
    can be what saves it.
    """
    client = _meta_client()

    with patch("requests.post", return_value=_Response()):
        client.make_request(
            {
                "method": "POST",
                "url": "https://graph.example.invalid/v24.0/app/uploads",
                "headers": client.headers,
                "data": {"file_length": 1024, "access_token": SENTINEL_APP_SECRET},
            }
        )

    kept = client.last_curl_command
    _assert_no_credential_anywhere(_everything_written(capsys, logs), kept)
    assert "file_length=1024" in kept


def test_a_credential_hiding_inside_an_innocent_field_is_scrubbed(capsys, logs):
    """Name-based masking cannot see a token that is part of some other value —
    a callback URL carrying it as a parameter, a provider error quoting it back.

    So the client's own credential is also scrubbed by value, the way
    ``meta_preflight._redact`` does it. This is the layer that catches the paths
    nobody thought to name.
    """
    client = _meta_client()

    with patch("requests.post", return_value=_Response()):
        client.make_json_request(
            {
                "method": "POST",
                "url": "https://graph.example.invalid/v24.0/1234/subscribed_apps",
                "data": {"callback_url": f"https://tenant.example.invalid/hook?auth={SENTINEL_BEARER}"},
            }
        )

    kept = client.last_curl_command
    _assert_no_credential_anywhere(_everything_written(capsys, logs), kept)
    assert "tenant.example.invalid/hook" in kept, "scrubbing the value must not erase the field"


# ── the clients that hand-roll their own curl string ──────────────────────


def _meta_media_upload(capsys):
    from wa.utility.apis.meta.media_api import MetaMediaAPI

    client = MetaMediaAPI(token=SENTINEL_BEARER, phone_number_id="1234")
    with patch("requests.post", return_value=_Response()):
        client.upload_media_from_file_object(io.BytesIO(b"x"), "photo.png", "image/png", file_size=1)
    return client


def _meta_media_get_url(capsys):
    from wa.utility.apis.meta.media_api import MetaMediaAPI

    client = MetaMediaAPI(token=SENTINEL_BEARER, phone_number_id="1234")
    with patch("requests.get", return_value=_Response()):
        client.get_media_url("9876")
    return client


def _meta_media_delete(capsys):
    from wa.utility.apis.meta.media_api import MetaMediaAPI

    client = MetaMediaAPI(token=SENTINEL_BEARER, phone_number_id="1234")
    with patch("requests.delete", return_value=_Response()):
        client.delete_media("9876")
    return client


def _meta_media_download(capsys):
    from wa.utility.apis.meta.media_api import MetaMediaAPI

    client = MetaMediaAPI(token=SENTINEL_BEARER, phone_number_id="1234")
    signed = f"https://lookaside.example.invalid/m/9876?access_token={SENTINEL_BEARER}&ext=1"
    with patch("requests.get", return_value=_Response()):
        client.download_media(signed)
    return client


def _gupshup_template_upload(capsys):
    from wa.utility.apis.gupshup.template_api import TemplateAPI

    client = TemplateAPI(appId="app1", token=SENTINEL_PARTNER)
    with patch("requests.post", return_value=_Response()):
        client.upload_media_from_file_object(io.BytesIO(b"x"), "photo.png", "image/png")
    return client


def _gupshup_template_sync(capsys):
    from wa.utility.apis.gupshup.template_api import TemplateAPI

    client = TemplateAPI(appId="app1", token=SENTINEL_PARTNER)
    with patch("requests.get", return_value=_Response()):
        client.sync_templates_with_meta()
    return client


def _gupshup_template_by_name(capsys):
    from wa.utility.apis.gupshup.template_api import TemplateAPI

    client = TemplateAPI(appId="app1", token=SENTINEL_PARTNER)
    with patch("requests.get", return_value=_Response()):
        client.get_template_by_element_name("order_update")
    return client


def _wati_media_download(capsys, tmp_path=None):
    from wa.utility.apis.wati.media_api import MediaAPI

    client = MediaAPI(api_endpoint="tenant.example.invalid", token=SENTINEL_BEARER)
    with patch("requests.get", return_value=_Response()):
        client.download_media("inbox/photo.png", "/dev/null")
    return client


@pytest.mark.parametrize(
    "exercise",
    [
        _meta_media_upload,
        _meta_media_get_url,
        _meta_media_delete,
        _meta_media_download,
        _gupshup_template_upload,
        _gupshup_template_sync,
        _gupshup_template_by_name,
        _wati_media_download,
    ],
    ids=[
        "meta-media-upload",
        "meta-media-get-url",
        "meta-media-delete",
        "meta-media-download",
        "gupshup-template-upload",
        "gupshup-template-sync",
        "gupshup-template-by-name",
        "wati-media-download",
    ],
)
def test_the_hand_rolled_reconstructions_mask_too(exercise, capsys, logs):
    """A mask on the two base clients is a signpost to the rest: these build
    their curl string inline, each one its own chance to publish the token.

    Two more of these were masked but cannot be exercised from here:
    ``wa.utility.apis.meta.template_api``'s two upload methods both read
    ``self.upload_media_to_whatsapp``, whose property is commented out in that
    class, so they raise ``AttributeError`` on their first statement. Separate
    defect, separate ticket — but the curl strings behind it are masked anyway,
    in case someone restores the property.
    """
    client = exercise(capsys)

    written = _everything_written(capsys, logs)
    _assert_no_credential_anywhere(written, client.last_curl_command)
    assert REDACTED in client.last_curl_command
    assert logs.records, "this call logged nothing, so the assertion above proves nothing"


# ── what the persisting callers would copy ────────────────────────────────


def test_what_a_caller_would_persist_carries_no_credential(capsys, logs):
    """``wa.tasks`` copies ``last_curl_command`` into a task result a caller may
    store. Masking at the build site is what closes that without the caller
    knowing — which is also why deleting the debug blobs in #337 did not weaken
    this: the blob was one consumer of the string, not the leak itself."""
    client = _meta_client()

    with patch("requests.post", return_value=_Response()):
        client.make_json_request(
            {
                "method": "POST",
                "url": "https://graph.example.invalid/v24.0/1234/message_templates",
                "data": {"name": "order_update"},
            }
        )

    would_be_persisted = f"=== TEMPLATE SUBMISSION DEBUG INFO ===\n{client.last_curl_command}\n"

    _assert_no_credential_anywhere(_everything_written(capsys, logs), would_be_persisted)
    # Still worth storing: the request is reconstructable from the blob.
    assert "message_templates" in would_be_persisted
    assert "order_update" in would_be_persisted
