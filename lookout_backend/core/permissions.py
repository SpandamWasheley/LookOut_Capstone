from rest_framework import permissions

from .models import Alert, User

# An alert somebody has already closed: attended and dealt with, or judged a
# false alarm. Mirrors AlertViewSet.REVIEWED_STATUSES.
CLOSED_ALERT_STATUSES = (Alert.Status.RESOLVED, Alert.Status.ACKNOWLEDGED)


class IsAdmin(permissions.BasePermission):
    """Allows access only to authenticated users with the admin role."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == User.Role.ADMIN
        )


class CanEditOwnOpenCitation(permissions.BasePermission):
    """A filed citation may be corrected by the officer who filed it, and only
    while its alert is still open.

    Two limits, for two different reasons.

    OWNERSHIP — a citation is the officer's own account of what they saw and
    who they spoke to. Someone who was not at that scene correcting a name is
    not fixing a typo, they are rewriting somebody else's statement.

    OPEN ALERT — once the alert is resolved the incident is closed and the
    citation is the record of it. Editing is for the mistake you notice while
    still standing there, not for revising a closed case later. A genuine
    error found after the fact is a different, deliberate act and should not
    share a path with "fix the spelling".

    Reads are unrestricted (the dashboard lists citations); this only gates
    writes. Deletes are not covered because the viewset does not expose one.
    """

    message = ("A citation can only be corrected by the officer who filed it, "
               "and only while the alert is still open.")

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        officer = getattr(request.user, "officer_profile", None)
        if officer is None or obj.officer_id != officer.id:
            return False
        # No alert at all (a standalone citation) has nothing to close, so
        # ownership is the only test that applies.
        if obj.alert is None:
            return True
        return obj.alert.status not in CLOSED_ALERT_STATUSES


class IsAdminOrReadOnly(permissions.BasePermission):
    """Any authenticated user can read; only admins can write."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.role == User.Role.ADMIN
