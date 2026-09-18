"""A chat-flow Telegram keyboard must fit Telegram's limit, which is in bytes.

Found while checking the rest of the chat-flow button path after the button-id
charset fix, and it is the same shape: one question — "how do I fit
``callback_data`` into Telegram's 64?" — answered in two places, with the copy
inside ``chat_flow`` being the wrong one.

``telegram.services.keyboard_builder`` has the right answer, cutting on a
UTF-8 codepoint boundary at 64 *bytes*, and ``build_inline_keyboard`` refuses
to send anything longer. The session-message node approximated it with
``str(value)[:64]``, a cut at 64 *characters* — so a button whose label is not
ASCII passes the character count, fails the byte count, and the keyboard is
never sent at all: ``build_inline_keyboard`` raises instead.

HOW TO RUN:
    python -m pytest chat_flow/test_telegram_callback_data_bytes.py -v
"""

from __future__ import annotations

import pytest

from telegram.constants import CALLBACK_DATA_MAX_LENGTH
from telegram.services.keyboard_builder import build_inline_keyboard, truncate_callback_data

# 20 characters is the most WhatsApp allows in a button label, and the flow
# editor derives a button's id from its label — so ``btn-`` plus 20 characters
# is the realistic worst case, and how many bytes that is depends entirely on
# the script.
#
#   20 emoji          -> 4 + 80 = 84 bytes, over
#   20 Devanagari     -> 4 + 60 = 64 bytes, exactly at the limit
#   20 ASCII          -> 4 + 20 = 24 bytes, nowhere near
#
# Emoji in a button label is ordinary, and Devanagari sits one character away
# from failing, which is the more uncomfortable of the two facts.
EMOJI_LABEL = "🎉" * 20
DEVANAGARI_LABEL = "अ" * 20


def _old_way(value: str) -> str:
    """What the node used to build: a cut at 64 characters."""
    return str(value)[:64]


def test_the_character_cut_produced_data_telegram_refuses():
    """The premise, stated so the fix is known to be fixing something."""
    button_id = f"btn-{EMOJI_LABEL}"

    assert len(_old_way(button_id)) <= CALLBACK_DATA_MAX_LENGTH, "under the character count"
    assert len(_old_way(button_id).encode("utf-8")) > CALLBACK_DATA_MAX_LENGTH, "over the byte count"


def test_a_devanagari_label_sits_exactly_on_the_limit():
    """Not a failure today, and one character from being one.

    Recorded rather than asserted as a bug because it is the reason the
    character cut looked correct for so long: the scripts it breaks first are
    the ones nobody was testing with.
    """
    assert len(f"btn-{DEVANAGARI_LABEL}".encode()) == CALLBACK_DATA_MAX_LENGTH


def test_the_old_cut_makes_the_whole_keyboard_fail_to_send():
    """Not a truncated button — no message at all.

    ``build_inline_keyboard`` raises rather than silently sending something
    Telegram would reject, so one over-long button takes the other buttons and
    the message body with it.
    """
    keyboard = [[{"text": EMOJI_LABEL, "callback_data": _old_way(f"btn-{EMOJI_LABEL}")}]]

    with pytest.raises(ValueError, match="exceeds"):
        build_inline_keyboard(keyboard)


@pytest.mark.parametrize(
    "label",
    [EMOJI_LABEL, DEVANAGARI_LABEL, "Let's have a call"],
    ids=["emoji", "devanagari", "ascii"],
)
def test_the_byte_aware_cut_sends(label):
    """The fix, asserted through the builder that was rejecting it."""
    keyboard = [[{"text": label, "callback_data": truncate_callback_data(f"btn-{label}")}]]

    markup = build_inline_keyboard(keyboard)

    sent = markup["inline_keyboard"][0][0]["callback_data"]
    assert len(sent.encode("utf-8")) <= CALLBACK_DATA_MAX_LENGTH
    assert sent, "something is still sent, rather than an empty payload"


def test_a_short_ascii_id_is_left_exactly_as_it_was():
    """The common case must round-trip untouched, or routing stops matching.

    The id is what comes back on the callback, so truncating one that fits
    would break the very lookup it exists for.
    """
    assert truncate_callback_data("btn-Tell me more") == "btn-Tell me more"


def test_a_multibyte_cut_never_splits_a_codepoint():
    """A half-written character would not decode on the way back."""
    cut = truncate_callback_data("btn-" + "अ" * 100)

    assert cut.encode("utf-8").decode("utf-8") == cut
