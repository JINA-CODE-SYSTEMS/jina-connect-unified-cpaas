from typing import Any, Dict

from djmoney.money import Money
from pydantic import BaseModel, ConfigDict

from tenants.utility.money_to_dict import money_to_dict


class WalletData(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    currency: str = "N/A"
    current_balance: float = 0.0
    overdraft_limit: float = 0.0
    money: Money

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "WalletData":
        wallet = data.get("walletResponse", {}) or {}
        return cls(
            currency=wallet.get("currency", "N/A"),
            current_balance=wallet.get("currentBalance", 0.0),
            overdraft_limit=wallet.get("overDraftLimit", 0.0),
            money=Money(wallet.get("currentBalance", 0.0), wallet.get("currency", "USD")),
        )

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Override model_dump to properly serialize Money field"""
        data = super().model_dump(**kwargs)
        # Convert Money field to dict format
        data["money"] = money_to_dict(self.money)
        return data
