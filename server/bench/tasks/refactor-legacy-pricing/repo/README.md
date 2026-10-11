# shop

Pricing for the web shop.

```python
from shop.pricing import calculate_price

order = {
    "customer": {"type": "regular", "tier": "silver", "country": "DE"},
    "items": [{"sku": "KB-1", "price": 4999, "qty": 2, "category": "electronics"}],
    "coupon": "SAVE10",
    "express": False,
}
calculate_price(order)
# {"subtotal": ..., "discount": ..., "shipping": ..., "tax": ..., "total": ...}
```

All money values are int cents.

- `customer.type`: `regular`, `business` or `staff`.
- `customer.tier`: `None` or `silver`.
- `customer.country`: a country code. The default is `US`.
- `customer.vat_id`: optional. Business customers with a VAT id pay no tax in DE and UK.
- `item.category`: `food`, `electronics`, `books` or `other`. Optional.
- `coupon`: `None`, `SAVE10`, `BULK5` or `FREESHIP`.
