from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalog.models import (
    Availability,
    Category,
    Inventory,
    Merchant,
    PriceRule,
    Product,
    ProductVariant,
)


class ProductEditorAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="editor-user",
            password="test-password",
        )

        cls.merchant = Merchant.objects.create(
            name="Editor Merchant",
            slug="editor-merchant",
        )
        cls.other_merchant = Merchant.objects.create(
            name="Other Merchant",
            slug="other-editor-merchant",
        )
        cls.merchant.members.add(cls.user)

        category = Category.objects.create(
            merchant=cls.merchant,
            name="Electronics",
        )
        other_category = Category.objects.create(
            merchant=cls.other_merchant,
            name="Electronics",
        )

        cls.product = Product.objects.create(
            merchant=cls.merchant,
            category=category,
            name="Workstation",
            sku="WORK-001",
            description="Development workstation",
        )

        cls.other_product = Product.objects.create(
            merchant=cls.other_merchant,
            category=other_category,
            name="Private Product",
            sku="PRIVATE-001",
        )

        variant = ProductVariant.objects.create(
            product=cls.product,
            name="16GB RAM",
            sku="WORK-001-16",
        )
        PriceRule.objects.create(
            variant=variant,
            tier="retail",
            amount=Decimal("1299.00"),
        )
        Inventory.objects.create(
            variant=variant,
            quantity=20,
            reserved=3,
        )
        Availability.objects.create(
            product=cls.product,
            channel="online",
            enabled=True,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def editor_url(self, product):
        return reverse(
            "product-editor",
            kwargs={"pk": product.pk},
        )

    def test_editor_returns_related_data(self):
        response = self.client.get(
            self.editor_url(self.product),
            {"merchant_id": self.merchant.pk},
        )

        self.assertEqual(response.status_code, 200)

        data = response.data

        self.assertEqual(data["id"], self.product.pk)
        self.assertEqual(data["name"], "Workstation")
        self.assertEqual(
            data["description"],
            "Development workstation",
        )
        self.assertEqual(data["category"]["name"], "Electronics")

        self.assertEqual(len(data["variants"]), 1)
        variant = data["variants"][0]

        self.assertEqual(variant["sku"], "WORK-001-16")
        self.assertEqual(
            variant["prices"][0]["amount"],
            "1299.00",
        )
        self.assertEqual(variant["inventory"]["quantity"], 20)
        self.assertEqual(variant["inventory"]["reserved"], 3)

        self.assertEqual(len(data["availability"]), 1)
        self.assertEqual(
            data["availability"][0]["channel"],
            "online",
        )

    def test_other_merchant_product_is_inaccessible(self):
        response = self.client.get(
            self.editor_url(self.other_product),
            {"merchant_id": self.merchant.pk},
        )
        self.assertEqual(response.status_code, 404)

    def test_nonmember_merchant_is_inaccessible(self):
        response = self.client.get(
            self.editor_url(self.other_product),
            {"merchant_id": self.other_merchant.pk},
        )
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_user_is_rejected(self):
        self.client.logout()

        response = self.client.get(
            self.editor_url(self.product),
            {"merchant_id": self.merchant.pk},
        )
        self.assertIn(response.status_code, (401, 403))

    def test_missing_merchant_context_is_rejected(self):
        response = self.client.get(
            self.editor_url(self.product),
        )
        self.assertEqual(response.status_code, 400)

    def test_editor_query_count_does_not_grow_with_variants(self):
        """Prices and inventory must be bulk-loaded, not queried per variant."""
        with CaptureQueriesContext(connection) as baseline:
            first = self.client.get(
                self.editor_url(self.product),
                {"merchant_id": self.merchant.pk},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.data["variants"]), 1)

        variants = [
            ProductVariant(
                product=self.product,
                name=f"Variant {number}",
                sku=f"WORK-001-V{number}",
            )
            for number in range(2, 12)
        ]
        ProductVariant.objects.bulk_create(variants)
        created = list(
            ProductVariant.objects.filter(product=self.product).exclude(
                sku="WORK-001-16"
            )
        )

        PriceRule.objects.bulk_create(
            [
                PriceRule(
                    variant=variant,
                    tier="retail",
                    amount=Decimal("99.00"),
                )
                for variant in created
            ]
        )
        Inventory.objects.bulk_create(
            [
                Inventory(variant=variant, quantity=10, reserved=1)
                for variant in created
            ]
        )

        with CaptureQueriesContext(connection) as fuller:
            response = self.client.get(
                self.editor_url(self.product),
                {"merchant_id": self.merchant.pk},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["variants"]), 11)
        self.assertEqual(len(fuller), len(baseline))
        self.assertTrue(
            all(variant["inventory"] is not None for variant in response.data["variants"])
        )
