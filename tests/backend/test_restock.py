"""
Tests for restock recommendation and restock order submission endpoints.
"""
import math
from datetime import datetime, timedelta

import pytest

from main import orders

# Cost to bring an item up to 1.5x its reorder point, mirrored from the API policy
TARGET_MULTIPLIER = 1.5


@pytest.fixture
def restore_orders():
    """Remove any orders appended to the in-memory list during a test."""
    count_before = len(orders)
    yield
    del orders[count_before:]


def full_restock_plan(client):
    """Compute the expected full restock plan from raw inventory + demand data."""
    inventory = {i["sku"]: i for i in client.get("/api/inventory").json()}
    expected = {}
    for forecast in client.get("/api/demand").json():
        inv = inventory.get(forecast["item_sku"])
        if not inv:
            continue
        target = math.ceil(inv["reorder_point"] * TARGET_MULTIPLIER)
        needed = target - inv["quantity_on_hand"]
        if needed > 0:
            expected[inv["sku"]] = {
                "needed": needed,
                "cost": round(needed * inv["unit_cost"], 2),
            }
    return expected


class TestRestockRecommendations:
    """Test suite for GET /api/restock/recommendations."""

    def test_recommendations_structure(self, client):
        """Test plan structure and item fields at a mid-range budget."""
        response = client.get("/api/restock/recommendations?budget=20000")
        assert response.status_code == 200

        plan = response.json()
        for field in ["budget", "total_cost", "remaining_budget",
                      "funded_item_count", "recommendation_count", "items"]:
            assert field in plan
        assert plan["budget"] == 20000
        assert isinstance(plan["items"], list)
        assert plan["recommendation_count"] == len(plan["items"])

        for item in plan["items"]:
            for field in ["sku", "name", "category", "warehouse", "quantity_on_hand",
                          "reorder_point", "target_stock", "recommended_quantity",
                          "funded_quantity", "unit_cost", "funded_cost", "full_cost",
                          "funding_status", "below_reorder_point", "forecasted_demand",
                          "current_demand", "trend", "lead_time_days"]:
                assert field in item
            assert item["funding_status"] in ["full", "partial", "unfunded"]
            assert item["trend"] in ["increasing", "stable", "decreasing"]
            assert item["lead_time_days"] > 0

    def test_zero_budget_funds_nothing(self, client):
        """Test that a zero budget still lists recommendations but funds none."""
        response = client.get("/api/restock/recommendations?budget=0")
        assert response.status_code == 200

        plan = response.json()
        assert plan["funded_item_count"] == 0
        assert plan["total_cost"] == 0
        assert plan["recommendation_count"] > 0
        for item in plan["items"]:
            assert item["funded_quantity"] == 0
            assert item["funding_status"] == "unfunded"

    def test_missing_budget_defaults_to_zero(self, client):
        """Test that the budget query param defaults to 0."""
        response = client.get("/api/restock/recommendations")
        assert response.status_code == 200
        assert response.json()["budget"] == 0

    def test_negative_budget_rejected(self, client):
        """Test that a negative budget fails validation."""
        response = client.get("/api/restock/recommendations?budget=-1")
        assert response.status_code == 422

    def test_large_budget_funds_everything(self, client):
        """Test that a huge budget fully funds every recommendation."""
        expected = full_restock_plan(client)
        expected_total = round(sum(e["cost"] for e in expected.values()), 2)

        response = client.get("/api/restock/recommendations?budget=1000000")
        plan = response.json()

        assert plan["recommendation_count"] == len(expected)
        assert plan["funded_item_count"] == len(expected)
        assert abs(plan["total_cost"] - expected_total) < 0.01
        assert abs(plan["remaining_budget"] - (1000000 - expected_total)) < 0.01
        for item in plan["items"]:
            assert item["funding_status"] == "full"
            assert item["funded_quantity"] == expected[item["sku"]]["needed"]

    def test_only_items_needing_stock_are_recommended(self, client):
        """Test that recommendations target 1.5x reorder point and skip healthy items."""
        inventory = {i["sku"]: i for i in client.get("/api/inventory").json()}
        plan = client.get("/api/restock/recommendations?budget=1000").json()

        for item in plan["items"]:
            inv = inventory[item["sku"]]
            assert item["target_stock"] == math.ceil(inv["reorder_point"] * TARGET_MULTIPLIER)
            assert item["recommended_quantity"] == item["target_stock"] - inv["quantity_on_hand"]
            assert item["recommended_quantity"] > 0

    def test_recommendations_come_from_demand_forecasts(self, client):
        """Test that every recommended SKU has a demand forecast."""
        forecast_skus = {f["item_sku"] for f in client.get("/api/demand").json()}
        plan = client.get("/api/restock/recommendations?budget=5000").json()

        assert plan["recommendation_count"] > 0
        for item in plan["items"]:
            assert item["sku"] in forecast_skus

    def test_urgency_ordering(self, client):
        """Test below-reorder-point items come first, then larger forecast gaps."""
        plan = client.get("/api/restock/recommendations?budget=0").json()
        items = plan["items"]
        assert len(items) > 1

        # All below-reorder-point items must precede all others
        below_flags = [item["below_reorder_point"] for item in items]
        assert below_flags == sorted(below_flags, reverse=True)

        # Within each urgency group, the forecast-to-stock gap never increases
        for prev, curr in zip(items, items[1:]):
            if prev["below_reorder_point"] == curr["below_reorder_point"]:
                prev_gap = prev["forecasted_demand"] - prev["quantity_on_hand"]
                curr_gap = curr["forecasted_demand"] - curr["quantity_on_hand"]
                assert prev_gap >= curr_gap

    @pytest.mark.parametrize("budget", [500, 9000, 20000, 33333.33, 58000])
    def test_allocation_never_exceeds_budget(self, client, budget):
        """Test greedy allocation math stays within budget at various budgets."""
        plan = client.get(f"/api/restock/recommendations?budget={budget}").json()

        total = 0
        for item in plan["items"]:
            assert 0 <= item["funded_quantity"] <= item["recommended_quantity"]
            assert abs(item["funded_cost"] - item["funded_quantity"] * item["unit_cost"]) < 0.01
            if item["funding_status"] == "full":
                assert item["funded_quantity"] == item["recommended_quantity"]
            elif item["funding_status"] == "partial":
                assert 0 < item["funded_quantity"] < item["recommended_quantity"]
            else:
                assert item["funded_quantity"] == 0
            total += item["funded_cost"]

        assert total <= budget + 0.01
        assert abs(plan["total_cost"] - round(total, 2)) < 0.01
        assert abs(plan["remaining_budget"] - (budget - plan["total_cost"])) < 0.01

    def test_partial_funding_when_budget_runs_short(self, client):
        """Test the first unaffordable item gets a partial whole-unit allocation."""
        plan_full = client.get("/api/restock/recommendations?budget=1000000").json()
        first = plan_full["items"][0]

        # Budget covers the first item plus half a unit of the second
        second = plan_full["items"][1]
        budget = first["full_cost"] + second["unit_cost"] * 1.5
        plan = client.get(f"/api/restock/recommendations?budget={budget}").json()

        assert plan["items"][0]["funding_status"] == "full"
        assert plan["items"][1]["funded_quantity"] == 1
        assert plan["items"][1]["funding_status"] == "partial"


class TestRestockOrderSubmission:
    """Test suite for POST /api/orders/restock."""

    def test_submit_restock_order(self, client, restore_orders):
        """Test submitting a restock order returns a well-formed Submitted order."""
        response = client.post("/api/orders/restock", json={
            "items": [{"sku": "TMP-201", "quantity": 100}]
        })
        assert response.status_code == 201

        order = response.json()
        assert order["status"] == "Submitted"
        assert order["customer"] == "Internal Restocking"
        assert order["order_number"].startswith("ORD-")
        assert len(order["items"]) == 1
        assert order["items"][0]["sku"] == "TMP-201"
        assert order["items"][0]["quantity"] == 100
        assert order["actual_delivery"] is None

        # TMP-201 is a Sensor: 10-day lead time drives the delivery date
        assert order["lead_time_days"] == 10
        order_date = datetime.fromisoformat(order["order_date"])
        expected_delivery = datetime.fromisoformat(order["expected_delivery"])
        assert expected_delivery - order_date == timedelta(days=10)

        # Total value matches quantity x inventory unit cost
        calculated = sum(i["quantity"] * i["unit_price"] for i in order["items"])
        assert abs(order["total_value"] - calculated) < 0.01

    def test_submitted_order_appears_in_orders_list(self, client, restore_orders):
        """Test the submitted order is returned by GET /api/orders."""
        created = client.post("/api/orders/restock", json={
            "items": [{"sku": "HMD-202", "quantity": 25}]
        }).json()

        all_orders = client.get("/api/orders").json()
        match = [o for o in all_orders if o["order_number"] == created["order_number"]]
        assert len(match) == 1
        assert match[0]["status"] == "Submitted"
        assert match[0]["lead_time_days"] == created["lead_time_days"]

    def test_lead_time_uses_slowest_category(self, client, restore_orders):
        """Test a mixed order takes the slowest category's lead time."""
        # Sensors ship in 10 days, Actuators in 18 - the order waits for both
        response = client.post("/api/orders/restock", json={
            "items": [
                {"sku": "TMP-201", "quantity": 10},
                {"sku": "SRV-301", "quantity": 5},
            ]
        })
        assert response.status_code == 201
        assert response.json()["lead_time_days"] == 18

    def test_order_ids_increment(self, client, restore_orders):
        """Test consecutive submissions get increasing ids and order numbers."""
        first = client.post("/api/orders/restock", json={
            "items": [{"sku": "TMP-201", "quantity": 1}]
        }).json()
        second = client.post("/api/orders/restock", json={
            "items": [{"sku": "TMP-201", "quantity": 1}]
        }).json()

        assert int(second["id"]) == int(first["id"]) + 1
        assert first["order_number"] != second["order_number"]

    def test_unknown_sku_rejected(self, client, restore_orders):
        """Test an unknown SKU returns 400 with a helpful message."""
        count_before = len(orders)
        response = client.post("/api/orders/restock", json={
            "items": [{"sku": "NOPE-999", "quantity": 5}]
        })
        assert response.status_code == 400
        assert "NOPE-999" in response.json()["detail"]
        assert len(orders) == count_before

    def test_duplicate_skus_merged(self, client, restore_orders):
        """Test repeated SKUs in one request collapse into a single line item."""
        response = client.post("/api/orders/restock", json={
            "items": [
                {"sku": "TMP-201", "quantity": 10},
                {"sku": "TMP-201", "quantity": 15},
            ]
        })
        assert response.status_code == 201

        order = response.json()
        assert len(order["items"]) == 1
        assert order["items"][0]["sku"] == "TMP-201"
        assert order["items"][0]["quantity"] == 25

    def test_empty_items_rejected(self, client):
        """Test an empty items list fails validation."""
        response = client.post("/api/orders/restock", json={"items": []})
        assert response.status_code == 422

    def test_zero_quantity_rejected(self, client):
        """Test a zero quantity fails validation."""
        response = client.post("/api/orders/restock", json={
            "items": [{"sku": "TMP-201", "quantity": 0}]
        })
        assert response.status_code == 422


class TestSubmittedOrderAggregates:
    """Submitted restocking orders are spending, not revenue - existing
    aggregate endpoints must not count them."""

    def submit_order(self, client):
        response = client.post("/api/orders/restock", json={
            "items": [{"sku": "TMP-201", "quantity": 50}]
        })
        assert response.status_code == 201
        return response.json()

    def test_dashboard_revenue_unaffected(self, client, restore_orders):
        """Test dashboard total_orders_value ignores Submitted orders."""
        before = client.get("/api/dashboard/summary").json()["total_orders_value"]
        self.submit_order(client)
        after = client.get("/api/dashboard/summary").json()["total_orders_value"]
        assert abs(after - before) < 0.01

    def test_monthly_trends_unaffected(self, client, restore_orders):
        """Test monthly trends gain no bucket and no revenue from Submitted orders."""
        before = client.get("/api/reports/monthly-trends").json()
        self.submit_order(client)
        after = client.get("/api/reports/monthly-trends").json()
        assert after == before

    def test_quarterly_reports_unaffected(self, client, restore_orders):
        """Test quarterly reports ignore Submitted orders."""
        before = client.get("/api/reports/quarterly").json()
        self.submit_order(client)
        after = client.get("/api/reports/quarterly").json()
        assert after == before
