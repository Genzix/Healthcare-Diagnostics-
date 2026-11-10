from django.contrib import admin
from .models import Category, DiagnosticTest, HealthPackage


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(DiagnosticTest)
class DiagnosticTestAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'code',
        'category',
        'sample_type',
        'fasting_required',
        'turnaround_time_hours',
        'base_price',
        'is_available',
    )
    list_filter = (
        'is_available',
        'category',
        'sample_type',
        'fasting_required',
    )
    search_fields = ('name', 'code', 'description')
    ordering = ('name',)


@admin.register(HealthPackage)
class HealthPackageAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'code',
        'price',
        'market_price',
        'total_tests_count',
        'fasting_required',
        'is_available',
    )
    list_filter = ('is_available', 'fasting_required')
    search_fields = ('name', 'code', 'description')
    filter_horizontal = ('tests',)
    ordering = ('price',)

