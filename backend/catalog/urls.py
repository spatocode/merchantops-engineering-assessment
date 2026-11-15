from django.urls import path

from catalog.views import (
    InventoryAdjustView,
    ProductActivityView,
    ProductDetailView,
    ProductEditorView,
    ProductListView,
)

urlpatterns = [
    path(
        "products/",
        ProductListView.as_view(),
        name="product-list",
    ),
    path(
        "products/<int:pk>/editor/",
        ProductEditorView.as_view(),
        name="product-editor",
    ),
    path(
        "products/<int:product_id>/variants/<int:variant_id>/inventory/adjust/",
        InventoryAdjustView.as_view(),
        name="inventory-adjust",
    ),
    path(
        "products/<int:pk>/",
        ProductDetailView.as_view(),
        name="product-detail",
    ),
    path(
        "products/<int:pk>/activity/",
        ProductActivityView.as_view(),
        name="product-activity",
    ),
]
