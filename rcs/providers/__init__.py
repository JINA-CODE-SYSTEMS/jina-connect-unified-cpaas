from rcs.providers.base import BaseRCSProvider

_PROVIDER_REGISTRY = {}


def _lazy_load():
    if _PROVIDER_REGISTRY:
        return
    from rcs.providers.google_rbm_provider import GoogleRBMProvider
    from rcs.providers.meta_rcs_provider import MetaRCSProvider

    _PROVIDER_REGISTRY.update(
        {
            "GOOGLE_RBM": GoogleRBMProvider,
            "META_RCS": MetaRCSProvider,
        }
    )


def get_rcs_provider(rcs_app) -> BaseRCSProvider:
    _lazy_load()
    provider_cls = _PROVIDER_REGISTRY.get(rcs_app.provider)
    if provider_cls is None:
        raise NotImplementedError(f"No RCS provider for '{rcs_app.provider}'")
    return provider_cls(rcs_app)
