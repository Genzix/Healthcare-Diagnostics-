from rest_framework import serializers
from .models import Category, DiagnosticTest, HealthPackage


class CategorySerializer(serializers.ModelSerializer):
    tests_count = serializers.IntegerField(source='tests.count', read_only=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'icon', 'is_active', 'tests_count']


class DiagnosticTestSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    price = serializers.DecimalField(source='base_price', max_digits=10, decimal_places=2, read_only=True)
    mrp = serializers.SerializerMethodField()
    discount = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = DiagnosticTest
        fields = [
            'id',
            'name',
            'code',
            'category',
            'category_name',
            'description',
            'sample_type',
            'fasting_required',
            'fasting_duration_hours',
            'turnaround_time_hours',
            'preparation_instructions',
            'base_price',
            'price',
            'mrp',
            'discount',
            'status',
            'is_available',
            'created_at',
            'updated_at',
        ]

    def get_mrp(self, obj):
        base = float(obj.base_price) if obj.base_price else 0.0
        return round(base * 1.5, 2)

    def get_discount(self, obj):
        return "33.33%"

    def get_status(self, obj):
        return "Active" if obj.is_available else "Inactive"


class DiagnosticTestSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticTest
        fields = ['id', 'name', 'code', 'sample_type', 'fasting_required', 'base_price']


class HealthPackageSerializer(serializers.ModelSerializer):
    tests = DiagnosticTestSimpleSerializer(many=True, read_only=True)
    tests_count = serializers.IntegerField(source='total_tests_count', read_only=True)
    discount_percentage = serializers.IntegerField(source='calculate_discount_percentage', read_only=True)
    mrp = serializers.DecimalField(source='market_price', max_digits=10, decimal_places=2, read_only=True)
    discount = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = HealthPackage
        fields = [
            'id',
            'name',
            'code',
            'description',
            'price',
            'market_price',
            'mrp',
            'discount',
            'discount_percentage',
            'status',
            'tests_count',
            'tests',
            'preparation_instructions',
            'terms_and_conditions',
            'fasting_required',
            'is_available',
            'created_at',
            'updated_at',
        ]

    def get_discount(self, obj):
        pct = obj.calculate_discount_percentage()
        return f"{pct}%" if pct else "0%"

    def get_status(self, obj):
        return "Active" if obj.is_available else "Inactive"

