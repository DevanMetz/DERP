from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = BaseUserAdmin.list_display + ("role",)
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Custom Permissions", {"fields": ("role",)}),
    )
