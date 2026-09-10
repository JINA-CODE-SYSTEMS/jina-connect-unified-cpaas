"""Domain exceptions shared across apps."""


class WalletCreditError(Exception):
    """A wallet balance could not be adjusted, so the transaction must not stand.

    Raised instead of letting a low-level error escape a signal handler. The
    distinction matters for money: a recharge that cannot be applied has to
    fail loudly and roll back, never record a payment while leaving the
    balance untouched.
    """


class MissingExchangeRate(WalletCreditError):
    """No exchange rate is available to convert into the wallet's currency.

    ``djmoney.contrib.exchange`` needs rates loaded by ``manage.py
    update_rates``, which needs ``OPEN_EXCHANGE_RATES_APP_ID`` set. If neither
    has happened, any transaction in a currency other than the wallet's cannot
    be applied.
    """
