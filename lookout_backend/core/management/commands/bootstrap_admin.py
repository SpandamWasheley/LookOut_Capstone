"""Create the first admin account on a freshly migrated database.

A new cloud deployment has an empty database: `migrate` creates tables, not
rows, so there is nobody to log in as and no way in to make anybody. The usual
answer, `createsuperuser` over a shell, is not available on Render's free tier
(Shell is a paid feature), and it would not be enough anyway - it never prompts
for `role`, so the account it makes defaults to role="officer", which
lookout/src/api.js rejects at login. The superuser would be locked out of the
dashboard it was created for.

This command exists to be safe to leave in the build command permanently: it is
idempotent, it never touches an existing account, and it takes the password
from the environment rather than the repository.

    ADMIN_USERNAME=admin ADMIN_PASSWORD=<strong> python manage.py bootstrap_admin
"""

from decouple import config
from django.core.management.base import BaseCommand, CommandError

from core.models import User


class Command(BaseCommand):
    help = "Create the initial admin user from ADMIN_USERNAME/ADMIN_PASSWORD."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-force-change",
            action="store_true",
            help=(
                "Skip the forced password change on first login. Only for a "
                "throwaway demo instance - the default exists so the bootstrap "
                "password cannot survive as the real one."
            ),
        )

    def handle(self, *args, **options):
        username = config("ADMIN_USERNAME", default="").strip()
        password = config("ADMIN_PASSWORD", default="")
        email = config("ADMIN_EMAIL", default="").strip()

        # Unset means "nothing to do", not "fail". The command sits in the build
        # command of every deploy, and only the first one has work to do; a
        # later deploy with the variables cleared must not break the build.
        if not username or not password:
            self.stdout.write(
                "ADMIN_USERNAME/ADMIN_PASSWORD not set - skipping admin bootstrap."
            )
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"User {username!r} already exists - leaving it alone.")
            return

        # Deliberately refuses rather than silently creating a second admin under
        # a different name: if admins already exist, this command has already done
        # its job and a stale env var should not quietly add another way in.
        if User.objects.filter(role=User.Role.ADMIN).exists():
            raise CommandError(
                "An admin account already exists under a different username. "
                "Refusing to create another. Clear ADMIN_USERNAME/ADMIN_PASSWORD "
                "from the environment, or add the user from the dashboard."
            )

        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
            role=User.Role.ADMIN,
            display_name="Barangay Administrator",
            is_staff=True,
            is_superuser=True,
            must_change_password=not options["no_force_change"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created admin {user.username!r}. "
                + (
                    "Log in and set a real password immediately."
                    if user.must_change_password
                    else "Forced password change is OFF."
                )
            )
        )
