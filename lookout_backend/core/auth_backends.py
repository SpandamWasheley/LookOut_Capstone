import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

logger = logging.getLogger(__name__)


class CaseInsensitiveUsernameBackend(ModelBackend):
    """Like ModelBackend, but matches the username case-insensitively so a
    login doesn't fail just because a phone keyboard auto-capitalized the
    first letter. If two accounts collide case-insensitively (e.g. "jdoe"
    and "JDoe"), refuses to guess which one was meant rather than
    authenticating an arbitrary match."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        users = list(UserModel._default_manager.filter(username__iexact=username))
        if len(users) != 1:
            if len(users) > 1:
                logger.warning(
                    "Case-insensitive username lookup for %r matched %d accounts; refusing to authenticate.",
                    username, len(users),
                )
            # Hash the password anyway so a nonexistent or ambiguous username
            # takes the same time as a real check (mirrors ModelBackend's own
            # mitigation for timing attacks on unknown usernames).
            UserModel().set_password(password)
            return None

        user = users[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
