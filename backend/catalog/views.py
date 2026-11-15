from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import (
    AuditLog,
    Availability,
    Inventory,
    Merchant,
    PriceRule,
    Product,
    ProductVariant,
)
from catalog.serializers import (
    ProductActivitySerializer,
    ProductListSerializer,
    ProductUpdateSerializer,
)


class ProductPagination(PageNumberPagination):
    page_size = 25


class ProductListView(generics.ListAPIView):
    serializer_class = ProductListSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ProductPagination

    def get_queryset(self):
        merchant_id = self.request.query_params.get("merchant_id")

        if not merchant_id:
            raise ValidationError({"merchant_id": "This parameter is required."})

        try:
            merchant_id = int(merchant_id)
        except (TypeError, ValueError):
            raise ValidationError({"merchant_id": "Must be a valid integer."})

        if merchant_id <= 0:
            raise ValidationError({"merchant_id": "Must be a positive integer."})

        get_object_or_404(
            Merchant.objects.filter(members=self.request.user),
            pk=merchant_id,
        )

        queryset = Product.objects.select_related("category").filter(merchant_id=merchant_id).order_by("id")

        search = self.request.query_params.get("search", "").strip()

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(sku__icontains=search)
            )

        return queryset


class ProductEditorView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        merchant_id = request.query_params.get("merchant_id")

        if not merchant_id:
            raise ValidationError({"merchant_id": "This parameter is required."})

        try:
            merchant_id = int(merchant_id)
        except (TypeError, ValueError):
            raise ValidationError({"merchant_id": "Must be a valid integer."})

        if merchant_id <= 0:
            raise ValidationError({"merchant_id": "Must be a positive integer."})

        get_object_or_404(
            Merchant.objects.filter(members=request.user),
            pk=merchant_id,
        )

        product = get_object_or_404(
            Product.objects.select_related("category"),
            pk=pk,
            merchant_id=merchant_id,
        )

        variants = list(
            ProductVariant.objects.filter(product_id=product.pk).order_by("id")
        )
        variant_ids = [variant.pk for variant in variants]

        prices_by_variant = {variant_id: [] for variant_id in variant_ids}
        for price in PriceRule.objects.filter(variant_id__in=variant_ids).order_by(
            "id"
        ):
            prices_by_variant[price.variant_id].append(
                {
                    "id": price.pk,
                    "tier": price.tier,
                    "amount": f"{price.amount:.2f}",
                }
            )

        inventory_by_variant = {
            inventory.variant_id: {
                "id": inventory.pk,
                "quantity": inventory.quantity,
                "reserved": inventory.reserved,
            }
            for inventory in Inventory.objects.filter(variant_id__in=variant_ids)
        }

        availability = list(
            Availability.objects.filter(product_id=product.pk)
            .order_by("id")
            .values(
                "id",
                "channel",
                "enabled",
            )
        )

        return Response(
            {
                "id": product.pk,
                "name": product.name,
                "sku": product.sku,
                "description": product.description,
                "status": product.status,
                "category": {
                    "id": product.category_id,
                    "name": product.category.name,
                },
                "variants": [
                    {
                        "id": variant.pk,
                        "name": variant.name,
                        "sku": variant.sku,
                        "prices": prices_by_variant[variant.pk],
                        "inventory": inventory_by_variant.get(variant.pk),
                    }
                    for variant in variants
                ],
                "availability": availability,
            }
        )


class ProductDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ProductUpdateSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = [
        "get",
        "patch",
        "head",
        "options",
    ]

    def get_queryset(self):
        merchant_id = self.request.query_params.get("merchant_id")

        if not merchant_id:
            raise ValidationError({"merchant_id": "This parameter is required."})

        try:
            merchant_id = int(merchant_id)
        except (TypeError, ValueError):
            raise ValidationError({"merchant_id": "Must be a valid integer."})

        if merchant_id <= 0:
            raise ValidationError({"merchant_id": "Must be a positive integer."})

        get_object_or_404(
            Merchant.objects.filter(members=self.request.user),
            pk=merchant_id,
        )

        return Product.objects.filter(merchant_id=merchant_id).select_related(
            "category"
        )

    def perform_update(self, serializer):
        tracked_fields = (
            "name",
            "sku",
            "description",
            "status",
            "category_id",
            "image_url",
        )

        with transaction.atomic():
            product = serializer.instance

            # Lock the row before comparing old and new values.
            locked_product = Product.objects.select_for_update().get(pk=product.pk)

            before = {field: getattr(locked_product, field) for field in tracked_fields}

            # Use the locked instance for the actual update.
            serializer.instance = locked_product
            updated_product = serializer.save()

            changes = {}

            for field in tracked_fields:
                old_value = before[field]
                new_value = getattr(updated_product, field)

                if old_value != new_value:
                    changes[field] = {
                        "from": old_value,
                        "to": new_value,
                    }

            if changes:
                AuditLog.objects.create(
                    product=updated_product,
                    actor=self.request.user,
                    action="product.updated",
                    details={"changes": changes},
                )


class ProductActivityView(generics.ListAPIView):
    serializer_class = ProductActivitySerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ProductPagination

    def get_queryset(self):
        merchant_id = self.request.query_params.get("merchant_id")

        if not merchant_id:
            raise ValidationError({"merchant_id": "This parameter is required."})

        try:
            merchant_id = int(merchant_id)
        except (TypeError, ValueError):
            raise ValidationError({"merchant_id": "Must be a valid integer."})

        if merchant_id <= 0:
            raise ValidationError({"merchant_id": "Must be a positive integer."})

        get_object_or_404(
            Merchant.objects.filter(members=self.request.user),
            pk=merchant_id,
        )

        product = get_object_or_404(
            Product.objects.filter(merchant_id=merchant_id),
            pk=self.kwargs["pk"],
        )

        return (
            AuditLog.objects.filter(product_id=product.pk)
            .select_related("actor")
            .order_by("-created_at", "-id")
        )
