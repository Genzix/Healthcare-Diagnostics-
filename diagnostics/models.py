from django.db import models
from django.utils.text import slugify


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=100, blank=True, help_text="Icon identifier or CSS class")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class DiagnosticTest(models.Model):
    SAMPLE_TYPE_CHOICES = (
        ('BLOOD', 'Blood'),
        ('URINE', 'Urine'),
        ('STOOL', 'Stool'),
        ('SWAB', 'Swab'),
        ('SPUTUM', 'Sputum'),
        ('OTHER', 'Other'),
    )

    name = models.CharField(max_length=200, db_index=True)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tests'
    )
    description = models.TextField()
    sample_type = models.CharField(max_length=50, choices=SAMPLE_TYPE_CHOICES, default='BLOOD')
    fasting_required = models.BooleanField(default=False)
    fasting_duration_hours = models.PositiveIntegerField(
        default=0,
        help_text="Required fasting hours (e.g., 8-12 hours)"
    )
    turnaround_time_hours = models.PositiveIntegerField(
        default=24,
        help_text="Estimated report turnaround time in hours"
    )
    preparation_instructions = models.TextField(
        blank=True,
        help_text="Detailed preparation guidelines for the patient"
    )
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Diagnostic Test'
        verbose_name_plural = 'Diagnostic Tests'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code}) - ₹{self.base_price}"


class HealthPackage(models.Model):
    name = models.CharField(max_length=200, db_index=True)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    description = models.TextField()
    tests = models.ManyToManyField(
        DiagnosticTest,
        related_name='packages',
        blank=True,
        help_text="Tests included in this health package"
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Package selling price"
    )
    market_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Total MRP of tests for discount display"
    )
    preparation_instructions = models.TextField(
        blank=True,
        help_text="Combined preparation guidelines for all package tests"
    )
    terms_and_conditions = models.TextField(
        blank=True,
        help_text="Terms, eligibility, and cancellation notes"
    )
    fasting_required = models.BooleanField(default=False)
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Health Package'
        verbose_name_plural = 'Health Packages'
        ordering = ['price']

    def __str__(self):
        return f"{self.name} ({self.code}) - ₹{self.price}"

    def total_tests_count(self):
        return self.tests.count()

    def calculate_discount_percentage(self):
        if self.market_price and self.market_price > self.price:
            savings = self.market_price - self.price
            return round((savings / self.market_price) * 100)
        return 0

