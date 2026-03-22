"""
apps/buyers/urls.py

URL configuration for the buyers app.

All routes registered under /v1/ (configured in the project's root urls.py).

Router registrations:
  buyers/                           BuyerViewSet
  wishlists/                        WishlistViewSet
  cart/                             CartViewSet
  orders/                           OrderViewSet
  payments/                         PaymentViewSet
  coupons/                          CouponViewSet
  notifications/                    BuyerNotificationViewSet

Note: Product reviews are registered in apps/farmers/urls.py at /v1/reviews/
because ProductReview is a farmers-domain model (reviews of farm products).

Generated URL patterns:
  GET    /v1/buyers/                              list
  POST   /v1/buyers/                              create
  GET    /v1/buyers/<id>/                         detail
  PUT    /v1/buyers/<id>/                         update
  DELETE /v1/buyers/<id>/                         soft-delete
  POST   /v1/buyers/<id>/verify/                  admin verify
  POST   /v1/buyers/<id>/reject/                  admin reject
  GET    /v1/buyers/<id>/documents/               list KYC docs
  POST   /v1/buyers/<id>/upload-document/         upload KYC doc
  GET    /v1/buyers/<id>/addresses/               list addresses
  POST   /v1/buyers/<id>/addresses/               add address
  GET    /v1/buyers/<id>/orders/                  buyer's orders

  GET    /v1/wishlists/                           list
  POST   /v1/wishlists/                           create
  DELETE /v1/wishlists/<id>/                      soft-delete
  POST   /v1/wishlists/<id>/add-item/             add item
  DELETE /v1/wishlists/<id>/remove-item/<pid>/    remove item
  POST   /v1/wishlists/<id>/move-to-cart/         move all to cart

  GET    /v1/cart/                                retrieve / create active cart
  POST   /v1/cart/add-item/                       add item
  PATCH  /v1/cart/update-item/                    update quantity
  DELETE /v1/cart/remove-item/                    remove item
  POST   /v1/cart/clear/                          clear cart
  POST   /v1/cart/apply-coupon/                   apply coupon
  DELETE /v1/cart/remove-coupon/                  remove coupon
  POST   /v1/cart/checkout/                       checkout → order

  GET    /v1/orders/                              list
  GET    /v1/orders/<id>/                         detail
  POST   /v1/orders/<id>/confirm/                 admin confirm
  POST   /v1/orders/<id>/dispatch/                admin dispatch
  POST   /v1/orders/<id>/deliver/                 admin deliver
  POST   /v1/orders/<id>/cancel/                  cancel
  GET    /v1/orders/<id>/track/                   tracking info
  POST   /v1/orders/<id>/reorder/                 reorder
  GET    /v1/orders/<id>/status-history/          audit trail

  GET    /v1/payments/                            list
  GET    /v1/payments/<id>/                       detail
  POST   /v1/payments/initiate/                   initiate payment
  POST   /v1/payments/webhook/                    provider webhook
  POST   /v1/payments/<id>/refund/                admin refund
  GET    /v1/payments/<id>/receipt/               receipt redirect

  GET    /v1/coupons/                             admin list
  POST   /v1/coupons/                             admin create
  PUT    /v1/coupons/<id>/                        admin update
  POST   /v1/coupons/validate/                    validate code (authenticated)

  GET    /v1/notifications/                       list
  GET    /v1/notifications/<id>/                  detail
  POST   /v1/notifications/<id>/read/             mark one read
  POST   /v1/notifications/mark-all-read/         mark all read
  GET    /v1/notifications/unread-count/          badge count
"""

from rest_framework.routers import DefaultRouter

from .views import (
    BuyerViewSet,
    BuyerNotificationViewSet,
    CartViewSet,
    CouponViewSet,
    OrderViewSet,
    PaymentViewSet,
    WishlistViewSet,
)

router = DefaultRouter()
router.register(r"",        BuyerViewSet,             basename="buyer")
router.register(r"wishlists",     WishlistViewSet,          basename="wishlist")
router.register(r"cart",          CartViewSet,              basename="cart")
router.register(r"orders",        OrderViewSet,             basename="order")
router.register(r"payments",      PaymentViewSet,           basename="payment")
router.register(r"coupons",       CouponViewSet,            basename="coupon")
router.register(r"notifications", BuyerNotificationViewSet, basename="notification")

urlpatterns = router.urls