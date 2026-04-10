"""
Management command to configure CORS on the GCS media bucket.

Browsers enforce CORS when JavaScript fetches resources cross-origin
(e.g. ``fetch(signedUrl)``).  Without a CORS policy on the bucket,
GCS responses won't include ``Access-Control-Allow-Origin`` and the
browser will block them — often surfacing as an ORB error.

Usage:
    python manage.py setup_gcs_cors
    python manage.py setup_gcs_cors --origins https://app.jinaconnect.com http://localhost:8000
    python manage.py setup_gcs_cors --dry-run
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Set CORS configuration on the GCS media bucket to allow cross-origin media access."

    def add_arguments(self, parser):
        parser.add_argument(
            "--origins",
            nargs="*",
            default=None,
            help=(
                "Allowed origins (e.g. https://app.jinaconnect.com). "
                "Defaults to ['*'] (all origins)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the CORS policy without applying it.",
        )

    def handle(self, *args, **options):
        backend = getattr(settings, "STORAGE_BACKEND", "local")
        if backend != "gcs":
            raise CommandError(
                f"STORAGE_BACKEND is '{backend}', not 'gcs'. "
                "This command only applies to Google Cloud Storage."
            )

        bucket_name = getattr(settings, "GS_BUCKET_NAME", None)
        if not bucket_name:
            raise CommandError("GS_BUCKET_NAME is not set in settings.")

        origins = options["origins"] or ["*"]

        cors_policy = [
            {
                "origin": origins,
                "method": ["GET", "HEAD", "OPTIONS"],
                "responseHeader": [
                    "Content-Type",
                    "Content-Disposition",
                    "Content-Length",
                    "Accept-Ranges",
                    "Content-Range",
                    "Cache-Control",
                ],
                "maxAgeSeconds": 3600,
            }
        ]

        if options["dry_run"]:
            import json

            self.stdout.write(self.style.WARNING("DRY RUN — would apply:"))
            self.stdout.write(json.dumps(cors_policy, indent=2))
            self.stdout.write(f"\nBucket: {bucket_name}")
            return

        try:
            from google.cloud import storage as gcs_storage

            # Re-use credentials from settings if available
            credentials = getattr(settings, "GS_CREDENTIALS", None)
            project_id = getattr(settings, "GS_PROJECT_ID", None)

            client = gcs_storage.Client(
                project=project_id,
                credentials=credentials,
            )
            bucket = client.bucket(bucket_name)
            bucket.cors = cors_policy
            bucket.patch()

            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ CORS policy applied to bucket '{bucket_name}':\n"
                    f"   Origins: {origins}\n"
                    f"   Methods: GET, HEAD, OPTIONS\n"
                    f"   MaxAge:  3600s"
                )
            )

            # Verify by reading back
            bucket.reload()
            self.stdout.write(f"   Verified: {bucket.cors}")

        except Exception as exc:
            raise CommandError(f"Failed to set CORS on bucket '{bucket_name}': {exc}")
