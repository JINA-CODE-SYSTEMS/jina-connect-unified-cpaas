"""Meta Business Login + system-user token storage (#191).

Owns one model — :class:`meta.models.MetaBusinessConnection` — and the
OAuth flow that connects a tenant's Meta Business Account + ad account
to Jina Connect. Sibling to ``TenantWAApp`` rather than extension of
its ``bsp_credentials`` JSON: one WABA can serve many ad accounts and
vice versa.

The OAuth flow in this PR is structurally complete but the live token
exchange is stubbed pending #190 Meta App Review approval.
"""

default_app_config = "meta.apps.MetaConfig"
