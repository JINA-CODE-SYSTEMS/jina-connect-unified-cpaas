"""The call-to-action in an email is white on indigo, in the client too.

Both mails put the button on a #4F46E5 ground and both declared `color: white`
in a `<style>` block — which Gmail strips outright in some views and Outlook.com
rewrites. What is left is the client's own link colour: blue text on an indigo
button, which is the one word in the mail the recipient has to be able to read.

So the colour is asserted twice per button, and the second one is the load
bearing half:

* inline on the `<a>`, because inline survives what `<style>` does not
* again on a `<span>` *inside* it, because a client rule that repaints links
  targets the anchor element — a child carrying its own colour is not what it
  matches

Pinned as source text rather than by sending a mail: there is no rendering
engine here that would tell the difference, and what actually broke was the
markup.
"""

import re

import pytest

from users.services.email_verification import EmailVerificationService
from users.services.password_reset import PasswordResetService

BUTTONS = [
    pytest.param(EmailVerificationService.send_verification_email, id="verification"),
    pytest.param(PasswordResetService.send_password_reset_email, id="password-reset"),
]

# The anchor, whatever it wraps. Non-greedy so a second anchor cannot be swallowed.
ANCHOR = re.compile(r'<a\s[^>]*class="button"[^>]*>.*?</a>', re.DOTALL)


def _button(sender) -> str:
    import inspect

    match = ANCHOR.search(inspect.getsource(sender))
    assert match, "the mail no longer has a button with class=\"button\""
    return match.group(0)


@pytest.mark.parametrize("sender", BUTTONS)
def test_the_anchor_carries_its_colour_inline(sender):
    """A `<style>` block is not where this can live — Gmail drops it."""
    opening_tag = _button(sender).split(">", 1)[0]

    assert "color: #ffffff !important" in opening_tag


@pytest.mark.parametrize("sender", BUTTONS)
def test_the_label_carries_it_too(sender):
    """The half that survives a client repainting links.

    An anchor whose text is a bare node inherits whatever the client decides an
    `<a>` should look like. A span between the two does not.
    """
    button = _button(sender)
    label = re.search(r"<span[^>]*>([^<]+)</span>", button)

    assert label, f"the button label is a bare text node, so a client can recolour it: {button}"
    assert "color: #ffffff !important" in button[: button.index(label.group(1))]


@pytest.mark.parametrize("sender", BUTTONS)
def test_the_button_is_still_dark_enough_to_need_white(sender):
    """If the ground ever goes light, white text is the bug rather than the fix."""
    assert "background-color: #4F46E5" in _button(sender)
