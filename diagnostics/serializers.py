import re
from decimal import Decimal
from rest_framework import serializers
from .models import Category, DiagnosticTest, HealthPackage


class CategorySerializer(serializers.ModelSerializer):
    tests_count = serializers.IntegerField(source='tests.count', read_only=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'description', 'icon', 'is_active', 'tests_count']


class DiagnosticTestSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    price = serializers.DecimalField(source='base_price', max_digits=10, decimal_places=2, required=False)
    selling_price = serializers.DecimalField(source='base_price', max_digits=10, decimal_places=2, required=False, write_only=True)
    test_name = serializers.CharField(source='name', required=False, write_only=True)
    test_code = serializers.CharField(source='code', required=False, write_only=True)
    mrp = serializers.SerializerMethodField()
    discount = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    report_turnaround = serializers.CharField(required=False, write_only=True)
    test_preparation_instructions = serializers.CharField(source='preparation_instructions', required=False, write_only=True)
    active = serializers.BooleanField(source='is_available', required=False, write_only=True)
    is_active = serializers.BooleanField(source='is_available', required=False, write_only=True)
    featured = serializers.BooleanField(required=False, default=False, write_only=True)
    show_on_homepage = serializers.BooleanField(required=False, default=False, write_only=True)

    class Meta:
        model = DiagnosticTest
        fields = [
            'id',
            'name',
            'test_name',
            'code',
            'test_code',
            'category',
            'category_name',
            'description',
            'sample_type',
            'fasting_required',
            'fasting_duration_hours',
            'turnaround_time_hours',
            'report_turnaround',
            'preparation_instructions',
            'test_preparation_instructions',
            'base_price',
            'price',
            'selling_price',
            'mrp',
            'discount',
            'status',
            'is_available',
            'active',
            'is_active',
            'featured',
            'show_on_homepage',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {
            'base_price': {'required': False},
            'name': {'required': False},
            'code': {'required': False},
            'description': {'required': False, 'allow_blank': True},
        }

    def to_internal_value(self, data):
        data = data.copy() if hasattr(data, 'copy') else dict(data)

        # 1. Alias: name / test_name
        if not data.get('name') and data.get('test_name'):
            data['name'] = data['test_name']

        # 2. Alias: code / test_code / auto-generate
        if not data.get('code'):
            if data.get('test_code'):
                data['code'] = data['test_code']
            elif data.get('name'):
                words = [w[:3].upper() for w in data['name'].split() if w]
                prefix = '-'.join(words[:2]) if words else 'TST'
                import random
                data['code'] = f"{prefix}-{random.randint(100, 999)}"

        # 3. Alias: price / selling_price / base_price
        if not data.get('base_price'):
            if data.get('selling_price') is not None:
                data['base_price'] = data['selling_price']
            elif data.get('price') is not None:
                data['base_price'] = data['price']
            elif data.get('mrp') is not None:
                data['base_price'] = data['mrp']

        # 4. Alias: active / is_active / is_available
        if 'is_available' not in data:
            if 'active' in data:
                data['is_available'] = data['active']
            elif 'is_active' in data:
                data['is_available'] = data['is_active']

        # 5. Alias: sample_type normalization
        st = str(data.get('sample_type', 'BLOOD')).strip().upper()
        if 'BLOOD' in st:
            data['sample_type'] = 'BLOOD'
        elif 'URINE' in st:
            data['sample_type'] = 'URINE'
        elif 'STOOL' in st:
            data['sample_type'] = 'STOOL'
        elif 'SWAB' in st:
            data['sample_type'] = 'SWAB'
        elif 'SPUTUM' in st:
            data['sample_type'] = 'SPUTUM'
        elif st:
            data['sample_type'] = 'OTHER'

        # 6. Alias: report_turnaround -> turnaround_time_hours
        if 'turnaround_time_hours' not in data and 'report_turnaround' in data:
            rt = str(data['report_turnaround']).lower()
            m = re.search(r'(\d+)', rt)
            if m:
                data['turnaround_time_hours'] = int(m.group(1))
            elif 'same day' in rt:
                data['turnaround_time_hours'] = 8
            else:
                data['turnaround_time_hours'] = 24

        # 7. Category lookup / auto-resolution
        cat = data.get('category')
        if isinstance(cat, str) and not cat.isdigit():
            c_obj = Category.objects.filter(name__iexact=cat).first()
            if not c_obj:
                c_obj = Category.objects.filter(slug__iexact=cat).first()
            if not c_obj:
                c_obj = Category.objects.create(name=cat)
            data['category'] = c_obj.id

        # 8. Store custom MRP in description metadata if provided
        mrp_val = data.get('mrp')
        if mrp_val is not None:
            desc = data.get('description', '')
            if '[MRP:' not in desc:
                data['description'] = f"{desc} [MRP: {mrp_val}]".strip()

        return super().to_internal_value(data)

    def create(self, validated_data):
        validated_data.pop('featured', None)
        validated_data.pop('show_on_homepage', None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('featured', None)
        validated_data.pop('show_on_homepage', None)
        return super().update(instance, validated_data)

    def get_mrp(self, obj):
        if obj.description:
            m = re.search(r'\[MRP:\s*([0-9.]+)\]', obj.description)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
        base = float(obj.base_price) if obj.base_price else 0.0
        return round(base * 1.5, 2)

    def get_discount(self, obj):
        base = float(obj.base_price) if obj.base_price else 0.0
        mrp = self.get_mrp(obj)
        if mrp and mrp > base:
            pct = round(((mrp - base) / mrp) * 100, 2)
            return f"{pct:g}%"
        return "0%"

    def get_status(self, obj):
        return "Active" if obj.is_available else "Inactive"


class DiagnosticTestSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticTest
        fields = ['id', 'name', 'code', 'sample_type', 'fasting_required', 'base_price']


class HealthPackageSerializer(serializers.ModelSerializer):
    tests = DiagnosticTestSimpleSerializer(many=True, read_only=True)
    tests_count = serializers.IntegerField(source='tests.count', read_only=True)
    discount_percentage = serializers.SerializerMethodField()

    class Meta:
        model = HealthPackage
        fields = [
            'id',
            'name',
            'code',
            'description',
            'tests',
            'tests_count',
            'price',
            'market_price',
            'discount_percentage',
            'preparation_instructions',
            'is_available',
            'created_at',
            'updated_at',
        ]

    def get_discount_percentage(self, obj):
        if obj.market_price and obj.price and obj.market_price > obj.price:
            discount = ((obj.market_price - obj.price) / obj.market_price) * 100
            return round(discount, 1)
        return 0.0


class HealthPackageCreateUpdateSerializer(serializers.ModelSerializer):
    test_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        help_text="List of DiagnosticTest IDs to include in this package"
    )

    class Meta:
        model = HealthPackage
        fields = [
            'id',
            'name',
            'code',
            'description',
            'test_ids',
            'price',
            'market_price',
            'preparation_instructions',
            'is_available',
        ]

    def create(self, validated_data):
        test_ids = validated_data.pop('test_ids', [])
        package = HealthPackage.objects.create(**validated_data)
        if test_ids:
            tests = DiagnosticTest.objects.filter(id__in=test_ids)
            package.tests.set(tests)
        return package

    def update(self, instance, validated_data):
        test_ids = validated_data.pop('test_ids', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if test_ids is not None:
            tests = DiagnosticTest.objects.filter(id__in=test_ids)
            instance.tests.set(tests)
        return instance
