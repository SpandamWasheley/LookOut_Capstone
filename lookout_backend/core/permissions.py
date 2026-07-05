from rest_framework import permissions

from .models import User


class IsAdmin(permissions.BasePermission):
    """Allows access only to authenticated users with the admin role."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == User.Role.ADMIN
        )


class IsAdminOrReadOnly(permissions.BasePermission):
    """Any authenticated user can read; only admins can write."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.role == User.Role.ADMIN
