from dataclasses import dataclass


@dataclass
class Item:
    sku: str
    name: str
    quantity: int = 0
    price: float = 0.0
    status: str = "active"

    def total_value(self) -> float:
        return self.quantity * self.price
