import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TransactionTestCase
from django.urls import reverse

from catalog.models import (
    AuditLog,
    Category,
    Inventory,
    Merchant,
    Product,
    ProductVariant,
)
from catalog.services.inventory import INVENTORY_ADJUSTED, adjust_variant_inventory


class InventoryAdjustAPITests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="inventory-user",
            password="test-password",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other-inventory-user",
            password="test-password",
        )

        self.merchant = Merchant.objects.create(
            name="Inventory Merchant",
            slug="inventory-merchant",
        )
        self.other_merchant = Merchant.objects.create(
            name="Other Inventory Merchant",
            slug="other-inventory-merchant",
        )
        self.merchant.members.add(self.user)

        category = Category.objects.create(
            merchant=self.merchant,
            name="Electronics",
        )
        other_category = Category.objects.create(
            merchant=self.other_merchant,
            name="Electronics",
        )

        self.product = Product.objects.create(
            merchant=self.merchant,
            category=category,
            name="Workstation",
            sku="INV-001",
        )
        self.other_product = Product.objects.create(
            merchant=self.other_merchant,
            category=other_category,
            name="Private Product",
            sku="PRIVATE-INV-001",
        )

        self.variant = ProductVariant.objects.create(
            product=self.product,
            name="16GB RAM",
            sku="INV-001-16",
        )
        self.other_variant = ProductVariant.objects.create(
            product=self.other_product,
            name="Private Variant",
            sku="PRIVATE-INV-001-A",
        )

        self.inventory = Inventory.objects.create(
            variant=self.variant,
            quantity=20,
            reserved=5,
        )
        self.other_inventory = Inventory.objects.create(
            variant=self.other_variant,
            quantity=10,
            reserved=0,
        )

        self.client.force_login(self.user)

    def adjust_url(self, product, variant):
        return reverse(
            "inventory-adjust",
            kwargs={
                "product_id": product.pk,
                "variant_id": variant.pk,
            },
        )

    def adjust(self, product, variant, payload, merchant_id=None):
        if merchant_id is None:
            merchant_id = self.merchant.pk
        return self.client.post(
            self.adjust_url(product, variant) + f"?merchant_id={merchant_id}",
            data=payload,
            content_type="application/json",
        )

    def test_increase_updates_quantity_and_creates_audit(self):
        response = self.adjust(
            self.product,
            self.variant,
            {"delta": 4, "reason": "Received shipment"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["quantity_before"], 20)
        self.assertEqual(response.data["quantity_after"], 24)
        self.assertEqual(response.data["delta"], 4)

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 24)

        log = AuditLog.objects.get(product=self.product)
        self.assertEqual(log.actor, self.user)
        self.assertEqual(log.action, INVENTORY_ADJUSTED)
        self.assertEqual(log.details["variant_id"], self.variant.pk)
        self.assertEqual(log.details["variant_sku"], "INV-001-16")
        self.assertEqual(log.details["reason"], "Received shipment")
        self.assertEqual(log.details["delta"], 4)
        self.assertEqual(log.details["quantity_before"], 20)
        self.assertEqual(log.details["quantity_after"], 24)

    def test_decrease_within_available_stock(self):
        response = self.adjust(
            self.product,
            self.variant,
            {"delta": -3, "reason": "Cycle count"},
        )

        self.assertEqual(response.status_code, 200)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 17)

    def test_zero_delta_rejected(self):
        response = self.adjust(
            self.product,
            self.variant,
            {"delta": 0, "reason": "No change"},
        )

        self.assertEqual(response.status_code, 400)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 20)
        self.assertFalse(AuditLog.objects.filter(product=self.product).exists())

    def test_blank_reason_rejected(self):
        response = self.adjust(
            self.product,
            self.variant,
            {"delta": 1, "reason": "   "},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(AuditLog.objects.filter(product=self.product).exists())

    def test_missing_reason_rejected(self):
        response = self.adjust(
            self.product,
            self.variant,
            {"delta": 1},
        )

        self.assertEqual(response.status_code, 400)

    def test_non_integer_delta_rejected(self):
        response = self.adjust(
            self.product,
            self.variant,
            {"delta": 1.5, "reason": "Fractional"},
        )

        self.assertEqual(response.status_code, 400)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 20)
        self.assertFalse(AuditLog.objects.filter(product=self.product).exists())

    def test_missing_merchant_id_rejected(self):
        response = self.client.post(
            self.adjust_url(self.product, self.variant),
            data={"delta": 1, "reason": "Missing merchant"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 20)

    def test_variant_without_inventory_is_rejected(self):
        bare_variant = ProductVariant.objects.create(
            product=self.product,
            name="No stock row",
            sku="INV-001-BARE",
        )

        response = self.adjust(
            self.product,
            bare_variant,
            {"delta": 1, "reason": "Missing inventory"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(AuditLog.objects.filter(product=self.product).exists())

    def test_adjustment_appears_in_activity_feed(self):
        adjust_response = self.adjust(
            self.product,
            self.variant,
            {"delta": 2, "reason": "Counted extras"},
        )
        self.assertEqual(adjust_response.status_code, 200)

        activity = self.client.get(
            reverse("product-activity", kwargs={"pk": self.product.pk}),
            {"merchant_id": self.merchant.pk},
        )

        self.assertEqual(activity.status_code, 200)
        self.assertEqual(activity.data["count"], 1)
        record = activity.data["results"][0]
        self.assertEqual(record["action"], INVENTORY_ADJUSTED)
        self.assertEqual(record["actor"], self.user.username)
        self.assertEqual(record["details"]["variant_sku"], "INV-001-16")
        self.assertEqual(record["details"]["delta"], 2)
        self.assertEqual(record["details"]["quantity_before"], 20)
        self.assertEqual(record["details"]["quantity_after"], 22)
        self.assertEqual(record["details"]["reason"], "Counted extras")

    def test_cannot_reduce_below_reserved(self):
        response = self.adjust(
            self.product,
            self.variant,
            {"delta": -16, "reason": "Too aggressive"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("reserved stock", str(response.data))
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 20)
        self.assertFalse(AuditLog.objects.filter(product=self.product).exists())

    @patch("catalog.services.inventory.AuditLog.objects.create")
    def test_audit_failure_rolls_back_quantity(self, mock_create):
        mock_create.side_effect = IntegrityError("forced audit failure")

        with self.assertRaises(IntegrityError):
            adjust_variant_inventory(
                merchant_id=self.merchant.pk,
                product_id=self.product.pk,
                variant_id=self.variant.pk,
                delta=5,
                reason="Should roll back",
                actor=self.user,
            )

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 20)
        self.assertFalse(AuditLog.objects.filter(product=self.product).exists())

    def test_other_merchant_variant_is_inaccessible(self):
        response = self.adjust(
            self.other_product,
            self.other_variant,
            {"delta": 1, "reason": "Should fail"},
            merchant_id=self.merchant.pk,
        )
        self.assertEqual(response.status_code, 404)
        self.other_inventory.refresh_from_db()
        self.assertEqual(self.other_inventory.quantity, 10)
        self.assertFalse(
            AuditLog.objects.filter(product=self.other_product).exists()
        )

    def test_nonmember_merchant_is_inaccessible(self):
        response = self.adjust(
            self.other_product,
            self.other_variant,
            {"delta": 1, "reason": "Should fail"},
            merchant_id=self.other_merchant.pk,
        )
        self.assertEqual(response.status_code, 404)
        self.other_inventory.refresh_from_db()
        self.assertEqual(self.other_inventory.quantity, 10)
        self.assertFalse(
            AuditLog.objects.filter(product=self.other_product).exists()
        )

    def test_unauthorized_operator_cannot_adjust_inventory(self):
        """Operators cannot change inventory for merchants they are not members of."""
        self.client.force_login(self.other_user)

        response = self.adjust(
            self.product,
            self.variant,
            {"delta": 5, "reason": "Unauthorized attempt"},
            merchant_id=self.merchant.pk,
        )

        self.assertEqual(response.status_code, 404)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 20)
        self.assertFalse(AuditLog.objects.filter(product=self.product).exists())

    def test_variant_product_mismatch_is_rejected(self):
        response = self.adjust(
            self.product,
            self.other_variant,
            {"delta": 1, "reason": "Mismatched"},
        )
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_user_is_rejected(self):
        self.client.logout()
        response = self.adjust(
            self.product,
            self.variant,
            {"delta": 1, "reason": "Anonymous"},
        )
        self.assertIn(response.status_code, (401, 403))

    def test_concurrent_adjustments_both_apply(self):
        barrier = threading.Barrier(2)
        results = []

        def run_adjust(delta):
            from django.test import Client

            client = Client()
            client.force_login(self.user)
            barrier.wait(timeout=5)
            response = client.post(
                self.adjust_url(self.product, self.variant)
                + f"?merchant_id={self.merchant.pk}",
                data={"delta": delta, "reason": f"Concurrent {delta}"},
                content_type="application/json",
            )
            results.append(response.status_code)
            connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(run_adjust, 3),
                executor.submit(run_adjust, 5),
            ]
            for future in futures:
                future.result(timeout=10)

        self.assertEqual(sorted(results), [200, 200])

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, 28)

        logs = AuditLog.objects.filter(
            product=self.product,
            action=INVENTORY_ADJUSTED,
        )
        self.assertEqual(logs.count(), 2)
        deltas = sorted(log.details["delta"] for log in logs)
        self.assertEqual(deltas, [3, 5])
