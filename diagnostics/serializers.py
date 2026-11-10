from rest_framework import serializers
from .models import Category, DiagnosticTest, HealthPackage


class CategorySerializer(serializers.ModelSerializer):
    tests_count = serializers.IntegerField(source='tests.count', read_only=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'icon', 'is_active', 'tests_count']


class DiagnosticTestSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

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
            'is_available',
            'created_at',
            'updated_at',
        ]


class DiagnosticTestSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticTest
        fields = ['id', 'name', 'code', 'sample_type', 'fasting_required', 'base_price']


class HealthPackageSerializer(serializers.ModelSerializer):
    tests = DiagnosticTestSimpleSerializer(many=True, read_only=True)
    tests_count = serializers.IntegerField(source='total_tests_count', read_only=True)
    discount_percentage = serializers.IntegerField(source='calculate_discount_percentage', read_only=True)

    class Meta:
        model = HealthPackage
        fields = [
            'id',
            'name',
            'code',
            'description',
            'price',
            'market_price',
            'discount_percentage',
            'tests_count',
            'tests',
            'preparation_instructions',
            'terms_and_conditions',
            'fasting_required',
            'is_available',
            'created_at',
            'updated_at',
        ]

