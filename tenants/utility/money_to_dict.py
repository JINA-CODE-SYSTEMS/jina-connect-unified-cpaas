def money_to_dict(m):
    if m is None:
        return {"amount": None, "currency": None}
    return {
        "amount": str(m.amount),
        "currency": m.currency.code
    }