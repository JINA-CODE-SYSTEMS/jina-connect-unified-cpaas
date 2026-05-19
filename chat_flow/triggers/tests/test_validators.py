"""ChatFlow.triggers validator tests for #188."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from chat_flow.triggers.validators import validate_trigger_list


class TestValidator:
    def test_empty_and_none_accepted(self):
        validate_trigger_list(None)
        validate_trigger_list([])

    def test_non_list_rejected(self):
        with pytest.raises(ValidationError):
            validate_trigger_list({"type": "inbound_keyword_match"})

    def test_entry_must_be_dict(self):
        with pytest.raises(ValidationError):
            validate_trigger_list(["string-entry"])

    def test_entry_missing_type_rejected(self):
        with pytest.raises(ValidationError):
            validate_trigger_list([{"config": {}}])

    def test_unknown_type_rejected(self):
        with pytest.raises(ValidationError, match="unknown trigger type"):
            validate_trigger_list([{"type": "never_registered", "config": {}}])

    def test_bad_config_rejected(self):
        # inbound_keyword_match requires non-empty `keywords`.
        with pytest.raises(ValidationError):
            validate_trigger_list([{"type": "inbound_keyword_match", "config": {"keywords": []}}])

    def test_valid_keyword_match_accepted(self):
        validate_trigger_list([{"type": "inbound_keyword_match", "config": {"keywords": ["sales"]}}])

    def test_duplicate_within_flow_rejected(self):
        with pytest.raises(ValidationError, match="duplicate"):
            validate_trigger_list(
                [
                    {"type": "inbound_keyword_match", "config": {"keywords": ["sales"]}},
                    {"type": "inbound_keyword_match", "config": {"keywords": ["sales"]}},
                ]
            )

    def test_different_configs_same_type_not_duplicates(self):
        # Two keyword triggers with different keyword lists are valid.
        validate_trigger_list(
            [
                {"type": "inbound_keyword_match", "config": {"keywords": ["sales"]}},
                {"type": "inbound_keyword_match", "config": {"keywords": ["support"]}},
            ]
        )

    def test_ctwa_referral_any_mode_accepted(self):
        validate_trigger_list([{"type": "ctwa_referral_received", "config": {"campaign_ids": "any"}}])

    def test_ctwa_referral_specific_ids_accepted(self):
        validate_trigger_list(
            [
                {
                    "type": "ctwa_referral_received",
                    "config": {"campaign_ids": ["00000000-0000-0000-0000-000000000001"]},
                }
            ]
        )

    def test_ctwa_referral_bad_mode_rejected(self):
        with pytest.raises(ValidationError):
            validate_trigger_list([{"type": "ctwa_referral_received", "config": {"campaign_ids": "all"}}])
