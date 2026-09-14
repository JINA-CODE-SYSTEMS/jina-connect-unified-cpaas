"""The key walker the write-only acceptance tests lean on (#370 fallout).

``json_field_names`` is the difference between "this body has a field called
``meta_app_secret``" and "the letters ``meta_app_secret`` occur somewhere in
this body". Two security assertions ask the first question through it, so a
walker that quietly missed a nesting level would weaken both without failing
anything. These pin the shapes a DRF read response actually takes.

HOW TO RUN:
    python -m pytest wa/tests/test_json_field_names.py -v
"""

from __future__ import annotations

from wa.tests.json_keys import json_field_names


def test_a_flat_object_gives_its_own_keys():
    assert json_field_names({"id": 1, "app_name": "x"}) == {"id", "app_name"}


def test_a_prefix_is_not_the_name():
    """The whole point: the hint field is not the secret field."""
    fields = json_field_names({"meta_app_secret_hint": "…6789"})

    assert "meta_app_secret_hint" in fields
    assert "meta_app_secret" not in fields


def test_a_paginated_list_response_is_walked_into():
    """A leak one level down is the same leak.

    ``/wa/v2/apps/`` answers ``{"results": [ {...}, {...} ]}``, so a walker
    that stopped at the top level would find nothing but ``count``, ``next``,
    ``previous`` and ``results`` — and pass on a body full of secrets.
    """
    body = {
        "count": 2,
        "next": None,
        "results": [{"id": 1, "bsp_access_token": "leaked"}, {"id": 2}],
    }

    assert "bsp_access_token" in json_field_names(body)


def test_objects_inside_objects_are_walked_into():
    assert "meta_app_secret" in json_field_names({"app": {"credentials": {"meta_app_secret": "leaked"}}})


def test_a_scalar_has_no_keys():
    assert json_field_names("meta_app_secret") == set()
    assert json_field_names(None) == set()
