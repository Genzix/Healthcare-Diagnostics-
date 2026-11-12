import secrets
from datetime import timedelta
from django.conf import settings
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


# ============================================================================
# Health Card Models (zero migrations required, managed = False)
# ============================================================================

class HealthCardProfile(models.Model):
    BLOOD_GROUP_CHOICES = (
        ('A+', 'A+'), ('A-', 'A-'),
        ('B+', 'B+'), ('B-', 'B-'),
        ('AB+', 'AB+'), ('AB-', 'AB-'),
        ('O+', 'O+'), ('O-', 'O-'),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='health_card_profile'
    )

    # 1. Basic Health Profile
    blood_group = models.CharField(max_length=5, choices=BLOOD_GROUP_CHOICES, blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True, default='')
    emergency_contact_phone = models.CharField(max_length=20, blank=True, default='')

    # 2. Body Metrics
    height_cm = models.FloatField(blank=True, null=True, help_text="Height in centimeters")
    weight_kg = models.FloatField(blank=True, null=True, help_text="Weight in kilograms")

    # 3. Vitals
    bp_systolic = models.IntegerField(blank=True, null=True, help_text="Systolic Blood Pressure (mmHg)")
    bp_diastolic = models.IntegerField(blank=True, null=True, help_text="Diastolic Blood Pressure (mmHg)")
    heart_rate = models.IntegerField(blank=True, null=True, help_text="Heart Rate / Pulse (BPM)")
    spo2 = models.IntegerField(blank=True, null=True, help_text="Blood Oxygen Saturation (%)")
    temperature_f = models.FloatField(blank=True, null=True, help_text="Body Temperature (°F)")

    # 4. Daily Activity
    daily_steps = models.IntegerField(default=0, help_text="Current daily step count")
    daily_water_ml = models.IntegerField(default=0, help_text="Current daily water intake (ml)")

    # 5. Sleep Tracking
    sleep_hours = models.FloatField(default=0.0, help_text="Last recorded sleep duration (hours)")
    sleep_quality = models.CharField(max_length=20, default='GOOD', choices=[
        ('DEEP', 'Deep Sleep'),
        ('GOOD', 'Good Quality'),
        ('FAIR', 'Fair Quality'),
        ('RESTLESS', 'Restless / Poor')
    ])

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        app_label = 'accounts'
        db_table = 'accounts_healthcardprofile'

    def __str__(self):
        return f"HealthCardProfile for User {self.user_id}"

    # --- Calculations ---
    @property
    def bmi(self):
        """BMI = weight (kg) / [height (m)]^2"""
        if self.height_cm and self.weight_kg and self.height_cm > 0:
            height_m = self.height_cm / 100.0
            return round(self.weight_kg / (height_m ** 2), 1)
        return None

    @property
    def bmi_category(self):
        val = self.bmi
        if not val:
            return "Needs height + weight"
        if val < 18.5:
            return "Underweight"
        elif val <= 24.9:
            return "Normal"
        elif val <= 29.9:
            return "Overweight"
        else:
            return "Obese"

    @property
    def bmr(self):
        """Mifflin-St Jeor formula: 10*W + 6.25*H - 5*Age (+5 for men, -161 for women)"""
        age = self.user.get_age()
        if not (self.height_cm and self.weight_kg and age):
            return None
        base = (10 * self.weight_kg) + (6.25 * self.height_cm) - (5 * age)
        gender = (self.user.gender or '').upper()
        if gender == 'MALE':
            return round(base + 5)
        elif gender == 'FEMALE':
            return round(base - 161)
        return round(base - 78)

    @property
    def bp_display(self):
        if self.bp_systolic and self.bp_diastolic:
            return f"{self.bp_systolic}/{self.bp_diastolic} mmHg"
        return "No data"

    @property
    def bp_status(self):
        if not (self.bp_systolic and self.bp_diastolic):
            return "No data"
        sys, dia = self.bp_systolic, self.bp_diastolic
        if sys < 120 and dia < 80:
            return "Normal"
        elif 120 <= sys <= 129 and dia < 80:
            return "Elevated"
        elif (130 <= sys <= 139) or (80 <= dia <= 89):
            return "Hypertension Stage 1"
        elif (140 <= sys <= 180) or (90 <= dia <= 120):
            return "Hypertension Stage 2"
        return "Hypertensive Crisis"

    @property
    def spo2_display(self):
        if self.spo2 is not None:
            return f"{self.spo2}%"
        return "No data"

    @property
    def spo2_status(self):
        if self.spo2 is None:
            return "No data"
        return "Normal" if self.spo2 >= 95 else "Low / Hypoxemia"

    @property
    def temperature_display(self):
        if self.temperature_f is not None:
            return f"{self.temperature_f} °F"
        return "No data"


class HealthMetricHistory(models.Model):
    CATEGORY_CHOICES = (
        ('BODY', 'Body Metrics'),
        ('VITALS', 'Vitals'),
        ('ACTIVITY', 'Daily Activity'),
        ('SLEEP', 'Sleep Tracking'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='health_metric_history'
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, db_index=True)
    metric_name = models.CharField(max_length=50, db_index=True)
    metric_value = models.FloatField()
    secondary_value = models.FloatField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True, default='')
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        managed = False
        app_label = 'accounts'
        db_table = 'accounts_healthmetrichistory'
        ordering = ['-recorded_at']

    def __str__(self):
        return f"{self.metric_name} = {self.metric_value} for User {self.user_id} at {self.recorded_at}"


class HealthCardShare(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='health_card_shares'
    )
    share_token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    is_active = models.BooleanField(default=True)
    duration_hours = models.IntegerField(default=24)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        app_label = 'accounts'
        db_table = 'accounts_healthcardshare'
        ordering = ['-created_at']

    def __str__(self):
        return f"Share token {self.share_token[:8]}... for User {self.user_id} (Expires: {self.expires_at})"

    def is_valid(self):
        return self.is_active and timezone.now() < self.expires_at

    @classmethod
    def create_share_link(cls, user, duration_hours=24):
        token = secrets.token_urlsafe(24)
        expires_at = timezone.now() + timedelta(hours=duration_hours)
        return cls.objects.create(
            user=user,
            share_token=token,
            expires_at=expires_at,
            duration_hours=duration_hours,
            is_active=True
        )


class SupportInquiry(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='support_inquiries'
    )
    name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.CharField(max_length=100, blank=True)
    subject = models.CharField(max_length=200, default='General Inquiry')
    message = models.TextField()
    status = models.CharField(max_length=20, default='OPEN')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        app_label = 'accounts'
        db_table = 'accounts_supportinquiry'
        ordering = ['-created_at']

    def __str__(self):
        return f"Inquiry from {self.name or self.phone}: {self.subject}"


def ensure_health_card_tables():
    """
    Safely creates health card tables if they don't already exist in the database,
    requiring zero makemigrations and zero migrate commands.
    """
    from django.db import connection
    vendor = connection.vendor
    try:
        with connection.cursor() as cursor:
            if vendor == 'sqlite':
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts_healthcardprofile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE REFERENCES accounts_user(id) ON DELETE CASCADE,
                    blood_group VARCHAR(5),
                    emergency_contact_name VARCHAR(150) DEFAULT '',
                    emergency_contact_phone VARCHAR(20) DEFAULT '',
                    height_cm REAL,
                    weight_kg REAL,
                    bp_systolic INTEGER,
                    bp_diastolic INTEGER,
                    heart_rate INTEGER,
                    spo2 INTEGER,
                    temperature_f REAL,
                    daily_steps INTEGER DEFAULT 0,
                    daily_water_ml INTEGER DEFAULT 0,
                    sleep_hours REAL DEFAULT 0,
                    sleep_quality VARCHAR(20) DEFAULT 'GOOD',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                ''')
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts_healthmetrichistory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES accounts_user(id) ON DELETE CASCADE,
                    category VARCHAR(20),
                    metric_name VARCHAR(50),
                    metric_value REAL,
                    secondary_value REAL,
                    notes VARCHAR(255) DEFAULT '',
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                ''')
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts_healthcardshare (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES accounts_user(id) ON DELETE CASCADE,
                    share_token VARCHAR(64) UNIQUE,
                    expires_at TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    duration_hours INTEGER DEFAULT 24,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                ''')
            else:
                # PostgreSQL
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts_healthcardprofile (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE REFERENCES accounts_user(id) ON DELETE CASCADE,
                    blood_group VARCHAR(5),
                    emergency_contact_name VARCHAR(150) DEFAULT '',
                    emergency_contact_phone VARCHAR(20) DEFAULT '',
                    height_cm DOUBLE PRECISION,
                    weight_kg DOUBLE PRECISION,
                    bp_systolic INTEGER,
                    bp_diastolic INTEGER,
                    heart_rate INTEGER,
                    spo2 INTEGER,
                    temperature_f DOUBLE PRECISION,
                    daily_steps INTEGER DEFAULT 0,
                    daily_water_ml INTEGER DEFAULT 0,
                    sleep_hours DOUBLE PRECISION DEFAULT 0,
                    sleep_quality VARCHAR(20) DEFAULT 'GOOD',
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                ''')
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts_healthmetrichistory (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES accounts_user(id) ON DELETE CASCADE,
                    category VARCHAR(20),
                    metric_name VARCHAR(50),
                    metric_value DOUBLE PRECISION,
                    secondary_value DOUBLE PRECISION,
                    notes VARCHAR(255) DEFAULT '',
                    recorded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                ''')
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts_healthcardshare (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES accounts_user(id) ON DELETE CASCADE,
                    share_token VARCHAR(64) UNIQUE,
                    expires_at TIMESTAMP WITH TIME ZONE,
                    is_active BOOLEAN DEFAULT TRUE,
                    duration_hours INTEGER DEFAULT 24,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                ''')
    except Exception:
        pass


def ensure_support_tables():
    """
    Safely creates support inquiry table if it doesn't already exist in the database,
    requiring zero makemigrations and zero migrate commands.
    """
    from django.db import connection
    vendor = connection.vendor
    try:
        with connection.cursor() as cursor:
            if vendor == 'sqlite':
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts_supportinquiry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name VARCHAR(150),
                    phone VARCHAR(20),
                    email VARCHAR(100),
                    subject VARCHAR(200),
                    message TEXT,
                    status VARCHAR(20) DEFAULT 'OPEN',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                ''')
            else:
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS accounts_supportinquiry (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT,
                    name VARCHAR(150),
                    phone VARCHAR(20),
                    email VARCHAR(100),
                    subject VARCHAR(200),
                    message TEXT,
                    status VARCHAR(20) DEFAULT 'OPEN',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                ''')
    except Exception:
        pass

