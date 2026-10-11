import ast
import copy
import os
import random
import unittest
from pathlib import Path

import _ref_pricing
from shop.pricing import calculate_price

TYPES = ["regular", "business", "staff"]
COUNTRIES = ["US", "DE", "UK", "FR", "JP"]
CATEGORIES = ["food", "electronics", "books", "other", None]
COUPONS = [None, "SAVE10", "BULK5", "FREESHIP", "BOGUS"]


def random_order(rng, tiers):
    customer = {"type": rng.choice(TYPES)}
    tier = rng.choice(tiers)
    if tier is not None or rng.random() < 0.5:
        customer["tier"] = tier
    if rng.random() < 0.85:
        customer["country"] = rng.choice(COUNTRIES)
    if rng.random() < 0.4:
        customer["vat_id"] = rng.choice(["DE123", "GB999", ""])
    items = []
    for _ in range(rng.randint(0, 5)):
        item = {"sku": f"S{rng.randint(1, 99)}", "price": rng.randint(50, 20000), "qty": rng.choice([1, 2, 3, 9, 10, 11, 25])}
        category = rng.choice(CATEGORIES)
        if category is not None:
            item["category"] = category
        items.append(item)
    order = {"customer": customer, "items": items}
    if rng.random() < 0.7:
        order["coupon"] = rng.choice(COUPONS)
    if rng.random() < 0.5:
        order["express"] = rng.random() < 0.5
    return order


class Parity(unittest.TestCase):
    def check(self, seed, tiers, count=600):
        rng = random.Random(seed)
        for _ in range(count):
            order = random_order(rng, tiers)
            expected = _ref_pricing.calculate_price(copy.deepcopy(order))
            self.assertEqual(calculate_price(copy.deepcopy(order)), expected, order)


class OldBehaviorTest(Parity):
    def test_parity_no_tier(self):
        self.check(1, [None])

    def test_parity_silver(self):
        self.check(2, [None, "silver"])

    def test_parity_mixed_old_orders(self):
        self.check(3, [None, "silver"], count=1500)

    def test_does_not_change_input(self):
        rng = random.Random(4)
        for _ in range(100):
            order = random_order(rng, [None, "silver", "gold"])
            before = copy.deepcopy(order)
            calculate_price(order)
            self.assertEqual(order, before)

    def test_known_quirk_save10_threshold_after_tier(self):
        order = {"customer": {"type": "regular", "tier": "silver"}, "items": [{"price": 5050, "qty": 1}], "coupon": "SAVE10"}
        self.assertEqual(calculate_price(order), _ref_pricing.calculate_price(order))
        self.assertEqual(calculate_price(order)["discount"], 101)


class GoldTierTest(Parity):
    def test_gold_basic(self):
        order = {"customer": {"type": "regular", "tier": "gold"}, "items": [{"price": 10000, "qty": 1}]}
        result = calculate_price(order)
        self.assertEqual(result["discount"], 500)
        self.assertEqual(result["total"], 9500)

    def test_gold_rounds_down_after_line_discounts(self):
        order = {"customer": {"type": "business", "tier": "gold", "country": "DE"},
                 "items": [{"price": 333, "qty": 11, "category": "electronics"}]}
        self.assertEqual(calculate_price(order), _ref_pricing.calculate_price(order))

    def test_gold_not_for_staff(self):
        order = {"customer": {"type": "staff", "tier": "gold"}, "items": [{"price": 10000, "qty": 1, "category": "books"}]}
        self.assertEqual(calculate_price(order)["discount"], 0)

    def test_gold_with_coupons(self):
        for coupon in ("SAVE10", "BULK5", "FREESHIP"):
            order = {"customer": {"type": "regular", "tier": "gold", "country": "UK"},
                     "items": [{"price": 2600, "qty": 20, "category": "other"}], "coupon": coupon}
            self.assertEqual(calculate_price(order), _ref_pricing.calculate_price(order), coupon)

    def test_parity_with_gold(self):
        self.check(5, [None, "silver", "gold"], count=1500)


def max_depth(nodes, depth=0):
    deepest = depth
    blocks = (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.AsyncFor, ast.AsyncWith)
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, blocks):
            inner = depth + 1
            deepest = max(deepest, inner)
            for field in ("body", "handlers", "finalbody"):
                deepest = max(deepest, max_depth(getattr(node, field, []) or [], inner))
            orelse = getattr(node, "orelse", []) or []
            if isinstance(node, ast.If) and len(orelse) == 1 and isinstance(orelse[0], ast.If):
                deepest = max(deepest, max_depth(orelse, depth))
            else:
                deepest = max(deepest, max_depth(orelse, inner))
        elif isinstance(node, ast.ExceptHandler):
            deepest = max(deepest, max_depth(node.body, depth))
        else:
            deepest = max(deepest, max_depth(list(ast.iter_child_nodes(node)), depth))
    return deepest


def shop_functions():
    root = Path(os.environ["SWARMBENCH_WORKSPACE"]) / "shop"
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path.name, node


class StructureTest(unittest.TestCase):
    def test_functions_are_short(self):
        for name, func in shop_functions():
            length = func.end_lineno - func.lineno + 1
            self.assertLessEqual(length, 40, f"{name}:{func.name} has {length} lines")

    def test_nesting_depth(self):
        for name, func in shop_functions():
            depth = max_depth(func.body)
            self.assertLessEqual(depth, 3, f"{name}:{func.name} has nesting depth {depth}")

    def test_more_than_one_function(self):
        self.assertGreaterEqual(len(list(shop_functions())), 4)
