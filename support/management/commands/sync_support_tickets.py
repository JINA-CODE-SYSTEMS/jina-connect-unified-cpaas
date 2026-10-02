from django.core.management.base import BaseCommand, CommandError

from support import conf, services


class Command(BaseCommand):
    help = "Import this deployment's BugDrop issues and their /reply comments from GitHub. Safe to rerun."

    def handle(self, *args, **options):
        if not conf.is_configured():
            raise CommandError(
                "Support is not configured: set SUPPORT_GITHUB_REPO, SUPPORT_GITHUB_TOKEN and "
                "SUPPORT_GITHUB_WEBHOOK_SECRET."
            )
        hosts = ", ".join(sorted(conf.report_hosts())) or "(none)"
        self.stdout.write(f"Repository {conf.repo()}, reports from {hosts}")
        counts = services.sync_from_github()
        self.stdout.write(
            self.style.SUCCESS(
                f"{counts['seen']} ticket(s) for this deployment, {counts['created']} new, "
                f"{counts['replies']} reply(ies) imported."
            )
        )
