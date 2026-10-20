"""python -m shop.migrate <orders file>: rewrite the orders in schema 2 (API_V2.md)."""

import sys

from shop.store import Store


def migrate(path):
    store = Store(path)
    total = len(store.orders)
    if store.legacy:
        store.save()
    return store.legacy, total


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m shop.migrate <orders file>", file=sys.stderr)
        return 2
    done, total = migrate(argv[0])
    print(f"migrated {done} of {total} orders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
