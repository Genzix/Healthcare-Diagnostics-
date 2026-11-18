from django import forms
from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, PhoneOTP, FamilyMember, UserAddress


class AdminEmailAuthenticationForm(AdminAuthenticationForm):
    """
    Custom Admin Login Form that displays 'Email Address (or Phone Number)'
    and supports authentication via either Email address or Phone number.
    """
    username = forms.CharField(
        label="Email Address",
        widget=forms.TextInput(attrs={
            'autofocus': True,
            'placeholder': 'admin@example.com (or phone number)',
            'class': 'vTextField'
        })
    )


# Configure Django Admin Portal Headers and Login Form
admin.site.login_form = AdminEmailAuthenticationForm
admin.site.site_header = "1MinDoc Healthcare Administration"
admin.site.site_title = "1MinDoc Admin Portal"
admin.site.index_title = "1MinDoc Operations Dashboard"


class FamilyMemberInline(admin.TabularInline):
    model = FamilyMember
    extra = 0
    fields = ('name', 'relationship', 'gender', 'date_of_birth', 'contact_number')


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Admin interface configuration for Custom User model.
    """
    inlines = [FamilyMemberInline]
    list_display = (
        'phone_number',
        'first_name',
        'last_name',
        'email',
        'role',
        'city',
        'is_phone_verified',
        'is_staff',
        'is_active',
        'date_joined',
    )
    list_filter = (
        'role',
        'gender',
        'city',
        'is_phone_verified',
        'is_staff',
        'is_active',
    )
    search_fields = (
        'phone_number',
        'first_name',
        'last_name',
        'email',
        'city',
    )
    ordering = ('-date_joined',)

    fieldsets = (
        (None, {'fields': ('phone_number', 'password')}),
        ('Personal & Profile Info', {
            'fields': (
                'first_name',
                'last_name',
                'email',
                'role',
                'date_of_birth',
                'gender',
                'city',
                'address',
                'pincode',
            )
        }),
        ('Verification & Permissions', {
            'fields': (
                'is_phone_verified',
                'is_active',
                'is_staff',
                'is_superuser',
                'groups',
                'user_permissions',
            )
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': ('phone_number', 'role', 'is_phone_verified', 'password1', 'password2'),
            },
        ),
    )


@admin.register(FamilyMember)
class FamilyMemberAdmin(admin.ModelAdmin):
    list_display = ('name', 'relationship', 'user', 'gender', 'date_of_birth', 'contact_number', 'is_phone_verified', 'created_at')
    list_filter = ('relationship', 'gender', 'created_at')
    search_fields = ('name', 'user__phone_number', 'contact_number')



@admin.register(PhoneOTP)
class PhoneOTPAdmin(admin.ModelAdmin):
    """
    Admin interface configuration for PhoneOTP model.
    """
    list_display = (
        'phone_number',
        'otp_code',
        'purpose',
        'is_verified',
        'attempts',
        'max_attempts',
        'created_at',
        'expires_at',
    )
    list_filter = (
        'purpose',
        'is_verified',
        'created_at',
    )
    search_fields = (
        'phone_number',
        'otp_code',
    )
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)


@admin.register(UserAddress)
class UserAddressAdmin(admin.ModelAdmin):
    list_display = ('user', 'label', 'custom_label', 'house', 'city', 'pincode', 'is_default', 'location_source', 'has_location', 'last_used_at')
    list_filter = ('label', 'is_default', 'location_source', 'city')
    search_fields = ('user__phone_number', 'house', 'area', 'city', 'pincode', 'landmark')
    raw_id_fields = ('user',)
    readonly_fields = ('created_at', 'updated_at', 'last_used_at')

    @admin.display(boolean=True, description='Pinned')
    def has_location(self, obj):
        return obj.has_location
