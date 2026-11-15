from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from catalog.models import AuditLog, Inventory

INVENTORY_ADJUSTED = "inventory.adjusted"


def adjust_variant_inventory(
    *,
    merchant_id,
    product_id,
    variant_id,
    delta,
    reason,
    actor,
):
    """Apply a stock delta under a row lock and write an audit record atomically."""

    with transaction.atomic():
        try:
            inventory = (
                Inventory.objects.select_for_update()
                .select_related("variant", "variant__product")
                .get(
                    variant_id=variant_id,
                    variant__product_id=product_id,
                    variant__product__merchant_id=merchant_id,
                )
            )
        except Inventory.DoesNotExist as exc:
            raise NotFound() from exc

        quantity_before = inventory.quantity
        quantity_after = quantity_before + delta

        if quantity_after < inventory.reserved:
            raise ValidationError(
                "Adjustment would reduce quantity below reserved "
                f"stock ({inventory.reserved})."
            )

        inventory.quantity = quantity_after
        inventory.save(update_fields=["quantity"])

        audit = AuditLog.objects.create(
            product_id=inventory.variant.product_id,
            actor=actor,
            action=INVENTORY_ADJUSTED,
            details={
                "variant_id": inventory.variant_id,
                "variant_sku": inventory.variant.sku,
                "reason": reason,
                "delta": delta,
                "quantity_before": quantity_before,
                "quantity_after": quantity_after,
            },
        )

        return {
            "variant_id": inventory.variant_id,
            "quantity": inventory.quantity,
            "reserved": inventory.reserved,
            "quantity_before": quantity_before,
            "quantity_after": quantity_after,
            "delta": delta,
            "reason": reason,
            "audit_id": audit.pk,
        }
