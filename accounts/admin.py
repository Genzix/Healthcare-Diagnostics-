from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, PhoneOTP, FamilyMember


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
    list_display = ('name', 'relationship', 'user', 'gender', 'date_of_birth', 'contact_number', 'created_at')
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
