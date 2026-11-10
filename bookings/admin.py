from django.contrib import admin
from .models import Booking, BookingItem, Payment, Notification


class BookingItemInline(admin.TabularInline):
    model = BookingItem
    extra = 0
    readonly_fields = ('item_type', 'item_name', 'price', 'test', 'package')


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = (
        'payment_id',
        'payment_type',
        'amount',
        'payment_mode',
        'status',
        'gateway_transaction_id',
        'receipt_number',
        'offline_payment_reference',
        'recorded_by_partner',
        'created_at',
    )


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        'booking_id',
        'patient_name',
        'partner',
        'service_mode',
        'booking_date',
        'total_amount',
        'advance_amount_paid',
        'balance_amount_due',
        'booking_status',
        'payment_status',
        'created_at',
    )
    list_filter = (
        'booking_status',
        'payment_status',
        'service_mode',
        'partner',
        'booking_date',
    )
    search_fields = (
        'booking_id',
        'patient_name',
        'patient_phone',
        'user__phone_number',
        'partner__centre_name',
    )
    inlines = [BookingItemInline, PaymentInline]
    ordering = ('-created_at',)
    readonly_fields = (
        'booking_id',
        'advance_amount_due',
        'balance_amount_due',
        'created_at',
        'updated_at',
        'confirmed_at',
        'completed_at',
    )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        'payment_id',
        'booking',
        'payment_type',
        'amount',
        'payment_mode',
        'status',
        'receipt_number',
        'recorded_by_partner',
        'created_at',
    )
    list_filter = ('payment_type', 'payment_mode', 'status', 'recorded_by_partner', 'created_at')
    search_fields = ('payment_id', 'booking__booking_id', 'receipt_number', 'gateway_transaction_id')
    ordering = ('-created_at',)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'notification_type', 'title', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read', 'created_at')
    search_fields = ('user__phone_number', 'title', 'message')
    ordering = ('-created_at',)

