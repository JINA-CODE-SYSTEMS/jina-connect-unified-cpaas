"""Voice-channel exception hierarchy.

Concrete error types are added by later PRs (e.g. adapter errors in #159,
IVR compilation errors in #168). This module defines the root so callers
can ``except VoiceError`` and catch anything voice-related.
"""

from __future__ import annotations


class VoiceError(Exception):
    """Base class for all voice-channel errors."""


class VoiceCredentialError(VoiceError):
    """Provider credentials are missing or malformed."""


class VoiceProviderError(VoiceError):
    """The upstream provider returned an error response."""
