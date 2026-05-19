"""Ad creation + management against Meta Marketing API (#196).

Three models in this PR:

* :class:`ads.models.AdCreative` — image / video / caption with
  validation against Meta's spec at upload time.
* :class:`ads.models.AudienceTemplate` — reusable saved audience
  configurations.
* :class:`ads.models.CampaignInsights` — hourly-polled metrics per
  campaign per day.

The Marketing API client (:class:`meta.client.MetaApiClient`) is
stubbed pending #190; the per-tenant BUC token bucket
(:mod:`ads.rate_limit`) is real Redis and works today.
"""

default_app_config = "ads.apps.AdsConfig"
