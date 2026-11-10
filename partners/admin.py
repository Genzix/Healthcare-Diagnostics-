from django.contrib import admin
from .models import (
    DiagnosticPartner,
    PartnerServiceArea,
    PartnerSlot,
    PartnerTestPricing,
    PartnerPackagePricing,
)


class PartnerServiceAreaInline(admin.TabularInline):
    model = PartnerServiceArea
    extra = 1


class PartnerTestPricingInline(admin.TabularInline):
    model = PartnerTestPricing
    extra = 1


class PartnerPackagePricingInline(admin.TabularInline):
    model = PartnerPackagePricing
    extra = 1


@admin.register(DiagnosticPartner)
class DiagnosticPartnerAdmin(admin.ModelAdmin):
    list_display = (
        'centre_name',
        'city',
        'contact_person',
        'contact_phone',
        'license_number',
        'offers_home_collection',
        'home_collection_fee',
        'status',
        'created_at',
    )
    list_filter = ('status', 'city', 'offers_home_collection', 'created_at')
    search_fields = ('centre_name', 'city', 'license_number', 'contact_person', 'contact_phone')
    inlines = [PartnerServiceAreaInline, PartnerTestPricingInline, PartnerPackagePricingInline]
    actions = ['activate_partners', 'suspend_partners']

    @admin.action(description="Activate selected diagnostic partners")
    def activate_partners(self, request, queryset):
        queryset.update(status='ACTIVE')

    @admin.action(description="Suspend selected diagnostic partners")
    def suspend_partners(self, request, queryset):
        queryset.update(status='SUSPENDED')


@admin.register(PartnerSlot)
class PartnerSlotAdmin(admin.ModelAdmin):
    list_display = (
        'partner',
        'date',
        'start_time',
        'end_time',
        'service_mode',
        'booked_count',
        'max_capacity',
        'is_active',
    )
    list_filter = ('partner', 'date', 'service_mode', 'is_active')
    search_fields = ('partner__centre_name',)
    ordering = ('date', 'start_time')

