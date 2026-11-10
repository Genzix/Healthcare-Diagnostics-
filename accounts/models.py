import secrets
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone
from django.core.validators import RegexValidator


phone_validator = RegexValidator(
    regex=r'^\+?[0-9]{7,15}$',
    message="Phone number must be entered in format: '+999999999' or standard digits. Between 7 and 15 digits allowed."
)


class CustomUserManager(BaseUserManager):
    """
    Custom manager for User model where phone_number is the unique identifier.
    """
    def create_user(self, phone_number, password=None, **extra_fields):
        if not phone_number:
            raise ValueError("The phone number must be provided.")
        
        # Normalize phone number
        phone_number = str(phone_number).strip().replace(" ", "").replace("-", "")
        extra_fields.setdefault('is_active', True)

        user = self.model(phone_number=phone_number, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_phone_verified', True)
        extra_fields.setdefault('role', 'ADMIN')

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(phone_number, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = (
        ('PATIENT', 'Patient'),
        ('DOCTOR', 'Doctor'),
        ('LAB_STAFF', 'Lab Staff'),
        ('ADMIN', 'Admin'),
    )

    phone_number = models.CharField(
        max_length=17,
        unique=True,
        validators=[phone_validator],
        help_text="Primary phone number used for login and registration."
    )
    email = models.EmailField(blank=True, null=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='PATIENT')

    # Basic Profile Details
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(
        max_length=20,
        choices=[('MALE', 'Male'), ('FEMALE', 'Female'), ('OTHER', 'Other')],
        blank=True
    )
    city = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    pincode = models.CharField(max_length=10, blank=True)

    is_phone_verified = models.BooleanField(
        default=False,
        help_text="Designates whether this user's phone number has been verified via OTP."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Designates whether this user account should be treated as active."
    )
    is_staff = models.BooleanField(
        default=False,
        help_text="Designates whether the user can log into this admin site."
    )
    date_joined = models.DateTimeField(default=timezone.now)

    objects = CustomUserManager()

    USERNAME_FIELD = 'phone_number'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-date_joined']

    def __str__(self):
        full_name = self.get_full_name()
        if full_name:
            return f"{self.phone_number} ({full_name} - {self.role})"
        return f"{self.phone_number} ({self.role})"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name or self.phone_number

    def get_age(self):
        if not self.date_of_birth:
            return None
        dob = self.date_of_birth
        if isinstance(dob, str):
            from datetime import date
            dob = date.fromisoformat(dob)
        today = timezone.now().date()
        return today.year - dob.year - (
            (today.month, today.day) < (dob.month, dob.day)
        )


class FamilyMember(models.Model):
    RELATIONSHIP_CHOICES = (
        ('SPOUSE', 'Spouse'),
        ('CHILD', 'Child'),
        ('PARENT', 'Parent'),
        ('SIBLING', 'Sibling'),
        ('OTHER', 'Other'),
    )
    GENDER_CHOICES = (
        ('MALE', 'Male'),
        ('FEMALE', 'Female'),
        ('OTHER', 'Other'),
    )

    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='family_members'
    )
    name = models.CharField(max_length=150)
    relationship = models.CharField(max_length=50, choices=RELATIONSHIP_CHOICES)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES)
    contact_number = models.CharField(max_length=17, blank=True)
    email = models.EmailField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Family Member'
        verbose_name_plural = 'Family Members'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.relationship}) - User: {self.user.phone_number}"

    def get_age(self):
        if not self.date_of_birth:
            return None
        dob = self.date_of_birth
        if isinstance(dob, str):
            from datetime import date
            dob = date.fromisoformat(dob)
        today = timezone.now().date()
        return today.year - dob.year - (
            (today.month, today.day) < (dob.month, dob.day)
        )



class PhoneOTP(models.Model):
    PURPOSE_CHOICES = (
        ('REGISTRATION', 'Registration'),
        ('LOGIN', 'Login'),
        ('PASSWORD_RESET', 'Password Reset'),
    )

    phone_number = models.CharField(max_length=17, db_index=True)
    otp_code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default='REGISTRATION')
    is_verified = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'Phone OTP'
        verbose_name_plural = 'Phone OTPs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['phone_number', 'purpose', 'is_verified']),
        ]

    def __str__(self):
        status = "Verified" if self.is_verified else ("Expired" if self.is_expired() else "Active")
        return f"OTP for {self.phone_number} [{self.purpose}] - {status}"

    def is_expired(self):
        return timezone.now() > self.expires_at

    def can_attempt(self):
        return self.attempts < self.max_attempts and not self.is_verified and not self.is_expired()

    @staticmethod
    def generate_otp_code(length=6):
        """Generates a cryptographically secure numeric OTP."""
        return ''.join(str(secrets.randbelow(10)) for _ in range(length))
