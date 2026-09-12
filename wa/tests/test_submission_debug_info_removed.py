"""The two orphaned template-submission paths now run instead of raising (#337).

``submission_debug_info`` was written in four places and defined nowhere — not a
field on ``WATemplate``, not on ``BaseTemplateMessages``, and in no migration
since the repository was opened. Django rejects an unknown model constructor
kwarg with ``TypeError`` and an unknown name in ``update_fields`` with
``ValueError``, so none of the four sites could execute:

    wa/services/meta_template_service.py  _save_to_model
    wa/tasks.py  submit_template_to_gupshup, all three branches

#337 required establishing whether those paths were live before choosing a fix,
because the two readings have opposite ones. They are **not** live:

    * ``MetaTemplateService`` is instantiated nowhere in the repository. The live
      template-create path is ``WATemplateV2ViewSet.create`` →
      ``MetaDirectAdapter.submit_template``, which is what
      ``test_meta_path_template_submission.py`` (#277) exercises — and why that
      suite passed over a path that could not run.
    * ``submit_template_to_gupshup``'s only in-repo reference is the
      ``submit_template_to_meta`` alias, which has none of its own. It is in no
      ``CRONJOBS`` entry and no beat schedule, and ``wa.signals``'
      ``handle_pending_template`` — the trigger its docstring still described —
      is deliberately passive.

Reachability was judged from the code rather than from the absence of production
errors, because this deployment runs periodic work through django-crontab with
several ``CRONJOBS`` entries not installed on the box (#267) and no celery beat:
"never seen to fail" would only have meant "never ran".

So the writes were deleted rather than given a column. What makes that safe to
assert is that these tests *call* both paths with the HTTP boundary faked — and
calling them is also what settled the reading beyond argument. ``_save_to_model``
and its caller turned out to carry **four** independent unknown-name defects,
not one, each fatal on the first line that reached it:

    submission_debug_info=…           TypeError   — not a field (the ticket)
    template_id=…                     TypeError   — a read-only @property
    save(skip_legacy_validation=True) TypeError   — Model.save takes no such flag
    template.bsp_id                   AttributeError — the column is bsp_template_id

The Gupshup task carried two: the same ``submission_debug_info`` in all three
branches, and ``template.template_id = …`` in the success branch — which is the
same read-only property, and lands one line *before* the ValueError the ticket
predicted. Code with four fatal name errors in sixty lines has never run once.

Each test below therefore fails loudly if any of those comes back, which is the
anti-revert guard #337 asked for: the failure mode of this area was silence, and
the three-branch task in particular swallowed its own exception.

HOW TO RUN:
    python -m pytest wa/tests/test_submission_debug_info_removed.py -v
"""

from __future__ import annotations

import ast
import json
import pathlib
import uuid
from unittest.mock import MagicMock, patch

import pytest

from wa.tests.meta_path import FakeGraph, meta_wa_app, tenant, wa_template

pytestmark = pytest.mark.django_db

#: The per-app Meta credential every assertion below hunts for. Long and
#: distinctive so one substring search is conclusive, and not any real
#: provider's token shape.
SENTINEL_TOKEN = "EAAsyntheticTEMPLATETOKEN000000000notreal"

META_ID = "9876543210"

#: The attribute the four deleted sites wrote. Built from halves so it is never
#: a bare string constant in this file either: the scan below skips the tests
#: tree, and this keeps that skip from being the only thing standing between a
#: real finding and this module matching itself.
ATTR = "submission_" + "debug_info"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def owner():
    return tenant("DebugInfo")


@pytest.fixture()
def app(owner):
    """A Meta-Direct app whose WABA id is reachable the way the service reads it.

    ``get_waba_id`` walks ``wa_app.waba_info.waba_id`` — the related
    ``WABAInfo`` row, not the column of the same name on the app — and that row
    is created blank by ``tenants.signals``, so the id has to be filled in here.
    Without it ``MetaTemplateService.__init__`` refuses to construct and the
    test never reaches the code it is about.

    The row is reached through ``wa_app.waba_info`` rather than through the
    manager, because creating it with ``wa_app=instance`` populated the app's
    reverse-relation cache: a manager-side update writes the database and leaves
    ``get_waba_id`` reading the stale blank object it still holds.
    """
    wa_app = meta_wa_app(owner, access_token=SENTINEL_TOKEN)
    waba_info = wa_app.waba_info
    waba_info.waba_id = f"waba_{uuid.uuid4().hex[:8]}"
    waba_info.save(update_fields=["waba_id"])
    return wa_app


@pytest.fixture()
def graph(monkeypatch):
    return FakeGraph().install(monkeypatch)


def _marketing_payload(name: str) -> dict:
    """Template data in the shape ``MarketingTemplateRequestValidator`` accepts.

    Component types are lowercase because that is what the Pydantic validators
    pin (``Literal["body"]``); ``_save_to_model`` upper-cases them again when it
    pulls the body text back out.
    """
    return {
        "name": name,
        "language": "en",
        "category": "MARKETING",
        "components": [{"type": "body", "text": "Your order has shipped."}],
    }


# ─────────────────────────────────────────────────────────────────────────────
# The Meta service path — TypeError, after Meta had already accepted
# ─────────────────────────────────────────────────────────────────────────────


def test_the_meta_service_path_records_the_template_it_just_submitted(app, graph):
    """``create_template(save_to_db=True)`` reaches the end.

    This is the site that mattered most: the Graph POST happens *earlier in the
    same method*, so the ``TypeError`` landed after Meta had accepted the
    template — an accepted submission with no local row and a 500 for the
    caller. The assertion is that the row exists, which is only reachable
    through the constructor that used to raise.
    """
    from wa.models import WATemplate
    from wa.services.meta_template_service import MetaTemplateService

    name = f"tpl_{uuid.uuid4().hex[:8]}"
    graph.post("message_templates", {"id": META_ID, "status": "PENDING"})

    result = MetaTemplateService(app).create_template("marketing", _marketing_payload(name), save_to_db=True)

    # Meta really was called, as this tenant — otherwise "no local row" would
    # have been the harmless outcome rather than the damaging one.
    submitted = graph.only("POST", "message_templates")
    assert SENTINEL_TOKEN in submitted.headers.get("Authorization", "")

    assert result["template_id"] == META_ID
    row = WATemplate.objects.get(id=result["db_id"])
    assert row.element_name == name
    assert row.meta_template_id == META_ID
    assert row.content == "Your order has shipped."


def test_the_meta_service_result_carries_no_credential_and_no_curl_blob(app, graph):
    """The ``debug_info`` blob is gone, and what remains holds no token.

    #336 was that ``debug_info`` carried a live bearer token via
    ``last_curl_command``; #339 masked it where the curl string is built, which
    is upstream of everything here. Both halves are asserted: the token is
    genuinely in play (it is in the request Authorization header, found by the
    same substring search), and it is in neither the returned result nor any
    value persisted on the row.
    """
    from wa.models import WATemplate
    from wa.services.meta_template_service import MetaTemplateService

    graph.post("message_templates", {"id": META_ID, "status": "PENDING"})

    result = MetaTemplateService(app).create_template(
        "marketing", _marketing_payload(f"tpl_{uuid.uuid4().hex[:8]}"), save_to_db=True
    )

    # Falsifiability: the search finds the token where it *should* be, so its
    # absence below is a fact about the result and not about the search.
    assert SENTINEL_TOKEN in graph.only("POST", "message_templates").headers.get("Authorization", "")

    assert "debug_info" not in result
    assert SENTINEL_TOKEN not in json.dumps(result, default=str)

    row = WATemplate.objects.get(id=result["db_id"])
    persisted = json.dumps(
        {f.name: getattr(row, f.name, None) for f in row._meta.concrete_fields},
        default=str,
    )
    assert SENTINEL_TOKEN not in persisted
    assert "curl" not in persisted.lower()
    # The row is not empty — the absence assertions are over real content.
    assert META_ID in persisted


# ─────────────────────────────────────────────────────────────────────────────
# The Gupshup task path — all three branches, none of which could save
# ─────────────────────────────────────────────────────────────────────────────


def _gupshup_api(response: dict) -> MagicMock:
    """Stand in for the Gupshup client, curl string and all.

    ``last_curl_command`` is deliberately populated: it is what the deleted debug
    blob copied, so a test that left it unset could not tell a removed blob from
    an empty one. The success test asserts on it in both directions.
    """
    api = MagicMock()
    api.apply_for_template.return_value = response
    api.last_curl_command = "curl -X POST https://partner.example.invalid/app/template"
    return api


def _pending_template(app):
    from wa.models import TemplateStatus

    return wa_template(app, status=TemplateStatus.PENDING, needs_sync=True)


def test_a_successful_gupshup_submission_stores_the_returned_id(app):
    """The success branch reaches its ``save``.

    Two defects stacked here. The ``ValueError`` from
    ``update_fields=[..., "submission_debug_info"]`` is the one #337 predicted,
    but an ``AttributeError`` landed a line earlier still: the branch assigned
    ``template.template_id``, which is a read-only property.
    """
    from wa.tasks import submit_template_to_gupshup

    template = _pending_template(app)
    api = _gupshup_api({"status": "success", "template": {"id": "gs-777"}})

    with patch("wa.utility.apis.gupshup.template_api.TemplateAPI", return_value=api):
        result = submit_template_to_gupshup(template.id)

    assert result["status"] == "success"

    # The curl string survives in the task result — that is a documented return
    # value, masked at the build site by #339, and deliberately kept. It is only
    # the copy *on the row* that went away.
    assert result["curl_command"] == api.last_curl_command

    template.refresh_from_db()
    # The BSP's id lands in the column that holds it, and the read-only
    # ``template_id`` property then returns it — the assignment the branch used
    # to make was to that property itself.
    assert template.bsp_template_id == "gs-777"
    assert template.template_id == "gs-777"


def test_a_rejected_gupshup_submission_records_why(app):
    """The failure branch: ``error_message`` is what an operator reads, and the
    ``ValueError`` on the same ``save`` meant it was never written."""
    from wa.tasks import submit_template_to_gupshup

    template = _pending_template(app)

    with patch(
        "wa.utility.apis.gupshup.template_api.TemplateAPI",
        return_value=_gupshup_api({"status": "error", "message": "Template name already exists"}),
    ):
        result = submit_template_to_gupshup(template.id)

    assert result["status"] == "failed"
    template.refresh_from_db()
    assert template.error_message == "Template name already exists"


def test_a_transport_failure_is_recorded_rather_than_swallowed(app):
    """The exception branch was the worst of the three.

    Its ``save`` sat inside ``try: ... except Exception: pass``, so the
    ``ValueError`` was swallowed silently: the real error never reached
    ``error_message`` and the template never reached ``FAILED``. It stayed
    ``PENDING`` for ever with nothing saying why.
    """
    from wa.models import TemplateStatus
    from wa.tasks import submit_template_to_gupshup

    template = _pending_template(app)
    api = _gupshup_api({})
    api.apply_for_template.side_effect = RuntimeError("connection reset by peer")

    with patch("wa.utility.apis.gupshup.template_api.TemplateAPI", return_value=api):
        with pytest.raises(Exception, match="connection reset by peer"):
            submit_template_to_gupshup(template.id)

    template.refresh_from_db()
    assert template.error_message == "connection reset by peer"
    assert template.status == TemplateStatus.FAILED


# ─────────────────────────────────────────────────────────────────────────────
# One step further down the same chain
# ─────────────────────────────────────────────────────────────────────────────


def test_the_bsp_sync_task_writes_the_column_that_exists(app):
    """``sync_template_with_bsp_task`` is the tail of the same dead chain.

    ``_save_to_model`` calls ``_sync_with_bsp``, which dispatches this task for a
    non-Meta app. It wrote ``update(bsp_id=…)`` — not a field on ``WATemplate``,
    so ``QuerySet.update()`` raised ``FieldError``: the fifth instance of #337's
    defect class, and doubly invisible, because the only dispatcher wraps the
    ``.delay()`` in a ``try/except`` that merely logs.

    Called directly rather than through ``_sync_with_bsp`` for exactly that
    reason — dispatched, the failure would be swallowed and this test would pass
    over a broken write.
    """
    from wa.tasks import sync_template_with_bsp_task

    template = wa_template(app)

    sync_service = MagicMock()
    sync_service.sync_and_get_bsp_id.return_value = "gs-sync-4242"

    with patch("wa.services.sync_templates.get_sync_service", return_value=sync_service):
        result = sync_template_with_bsp_task(template_id=template.pk, wa_id=app.pk)

    assert result["status"] == "success"
    template.refresh_from_db()
    assert template.bsp_template_id == "gs-sync-4242"
    # And the read-only property now resolves through it.
    assert template.template_id == "gs-sync-4242"


# ─────────────────────────────────────────────────────────────────────────────
# The consistency rule: a field, or no writers — never one of each
# ─────────────────────────────────────────────────────────────────────────────


def _sites_touching(attr: str, path: pathlib.Path) -> list[str]:
    """Every place *path* uses *attr* as a name rather than as prose.

    Deliberately not a substring grep. The fix for #337 documents itself in two
    docstrings that name the attribute, and a grep cannot tell those from the
    code it warns about — it would make the guard below unfixable. The three
    shapes that actually reach Django are all visible in the AST:

        WATemplate(submission_debug_info=...)            → a keyword argument
        template.submission_debug_info = ...             → an attribute
        save(update_fields=["submission_debug_info"])    → a bare string constant

    A docstring is a string constant too, but one *containing* the name, never
    one equal to it, so prose is excluded by construction.
    """
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.keyword) and node.arg == attr:
            found.append(f"keyword argument, line {node.value.lineno}")
        elif isinstance(node, ast.Attribute) and node.attr == attr:
            found.append(f"attribute access, line {node.lineno}")
        elif isinstance(node, ast.Constant) and node.value == attr:
            found.append(f"string constant, line {node.lineno}")
    return found


def test_nothing_writes_the_attribute_unless_it_is_a_real_field():
    """#337's acceptance criterion, as one assertion.

    Either the column exists with a migration, or nothing writes it — never one
    of each. This scans the shipped source of the two apps that own the model
    and forbids the second half without the first, so re-adding a write site
    fails here whether or not someone also adds the field, and adding the field
    properly later does not fail here at all.
    """
    from wa.models import WATemplate

    is_field = ATTR in {f.name for f in WATemplate._meta.get_fields()}

    root = pathlib.Path(__file__).resolve().parent.parent.parent
    scanned = 0
    writers = {}
    for app_dir in ("wa", "message_templates"):
        for path in sorted((root / app_dir).rglob("*.py")):
            if "tests" in path.parts or path.name == "conftest.py":
                continue
            scanned += 1
            sites = _sites_touching(ATTR, path)
            if sites:
                writers[str(path.relative_to(root))] = sites

    if writers:
        assert is_field, (
            f"{ATTR} is used as a name in {writers} but is not a field on "
            "WATemplate — add the field and a migration, or remove the uses (#337)"
        )

    # The scan really walked the two files the four write sites lived in, so an
    # empty result is a finding rather than an empty haystack.
    assert scanned > 20, scanned
    for owner in ("wa/tasks.py", "wa/services/meta_template_service.py"):
        assert (root / owner).exists(), owner
