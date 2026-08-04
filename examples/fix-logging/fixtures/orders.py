"""Tiny order pipeline. Debugging leftovers everywhere."""

ORDERS = [
    {"id": 1, "total": 42.0, "currency": "USD"},
    {"id": 2, "total": 0.0, "currency": "USD"},
    {"id": 3, "total": 17.5, "currency": "EUR"},
]


def process(orders):
    print("starting run with", len(orders), "orders")
    kept = []
    for order in orders:
        if order["total"] <= 0:
            print("skipping empty order", order["id"])
            continue
        kept.append(order)
    print("kept", len(kept), "orders")
    return kept


def main():
    kept = process(ORDERS)
    print("total is", sum(o["total"] for o in kept))


if __name__ == "__main__":
    main()
