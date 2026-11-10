from django.db import models
from django.conf import settings


class DiagnosticPartner(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending Approval'),
        ('ACTIVE', 'Active'),
        ('SUSPENDED', 'Suspended'),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='partner_profile',
        null=True,
        blank=True,
        help_text="Associated partner user account for dashboard login"
    )
    centre_name = models.CharField(max_length=200, db_index=True)
    license_number = models.CharField(max_length=100, unique=True)
    contact_person = models.CharField(max_length=150)
    contact_phone = models.CharField(max_length=17)
    contact_email = models.EmailField()

    address_line = models.TextField()
    city = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=10, db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    offers_home_collection = models.BooleanField(
        default=True,
        help_text="Designates if this centre offers doorstep sample collection"
    )
    home_collection_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0.00,
        help_text="Flat fee charged for home collection"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Diagnostic Partner'
        verbose_name_plural = 'Diagnostic Partners'
        ordering = ['centre_name']

    def __str__(self):
        return f"{self.centre_name} ({self.city}) - {self.status}"

    def is_serviceable(self, pincode):
        """Checks whether the partner services the provided pincode for home collection."""
        if not self.offers_home_collection:
            return False
        return self.service_areas.filter(pincode=str(pincode).strip(), is_active=True).exists()


class PartnerServiceArea(models.Model):
    """
    Pincodes serviced by a diagnostic partner for home sample collection.
    """
    partner = models.ForeignKey(
        DiagnosticPartner,
        on_delete=models.CASCADE,
        related_name='service_areas'
    )
    city = models.CharField(max_length=100)
    pincode = models.CharField(max_length=10, db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Partner Service Area'
        verbose_name_plural = 'Partner Service Areas'
        unique_together = ('partner', 'pincode')

    def __str__(self):
        return f"{self.partner.centre_name} -> {self.pincode} ({self.city})"


class PartnerSlot(models.Model):
    SERVICE_MODE_CHOICES = (
        ('ALL', 'All Modes'),
        ('HOME_COLLECTION', 'Home Sample Collection'),
        ('CENTRE_VISIT', 'Diagnostic Centre Visit'),
    )

    partner = models.ForeignKey(
        DiagnosticPartner,
        on_delete=models.CASCADE,
        related_name='slots'
    )
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    service_mode = models.CharField(
        max_length=20,
        choices=SERVICE_MODE_CHOICES,
        default='ALL'
    )
    max_capacity = models.PositiveIntegerField(
        default=5,
        help_text="Maximum bookings allowed in this time slot"
    )
    booked_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Partner Time Slot'
        verbose_name_plural = 'Partner Time Slots'
        ordering = ['date', 'start_time']
        indexes = [
            models.Index(fields=['partner', 'date', 'is_active']),
        ]

    def __str__(self):
        return f"{self.partner.centre_name} | {self.date} {self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')} ({self.booked_count}/{self.max_capacity})"

    @property
    def is_available(self):
        return self.is_active and (self.booked_count < self.max_capacity)


class PartnerTestPricing(models.Model):
    """
    Partner-specific test price and availability overrides.
    """
    partner = models.ForeignKey(
        DiagnosticPartner,
        on_delete=models.CASCADE,
        related_name='test_pricings'
    )
    test = models.ForeignKey(
        'diagnostics.DiagnosticTest',
        on_delete=models.CASCADE,
        related_name='partner_pricings'
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_available = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Partner Test Pricing'
        verbose_name_plural = 'Partner Test Pricings'
        unique_together = ('partner', 'test')

    def __str__(self):
        return f"{self.partner.centre_name} - {self.test.name}: ₹{self.price}"


class PartnerPackagePricing(models.Model):
    """
    Partner-specific package price and availability overrides.
    """
    partner = models.ForeignKey(
        DiagnosticPartner,
        on_delete=models.CASCADE,
        related_name='package_pricings'
    )
    package = models.ForeignKey(
        'diagnostics.HealthPackage',
        on_delete=models.CASCADE,
        related_name='partner_pricings'
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_available = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Partner Package Pricing'
        verbose_name_plural = 'Partner Package Pricings'
        unique_together = ('partner', 'package')

    def __str__(self):
        return f"{self.partner.centre_name} - {self.package.name}: ₹{self.price}"

