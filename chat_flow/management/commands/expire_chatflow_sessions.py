"""End chat-flow sessions that stopped advancing.

Without this, a session ends only at an end node, on an explicit reset, or when
its flow is deactivated — so a contact who starts a flow and never replies holds
one open forever, and editing that flow is refused for as long as it is open.

HOW TO RUN:
    python manage.py expire_chatflow_sessions
    python manage.py expire_chatflow_sessions --dry-run
    python manage.py expire_chatflow_sessions --hours 24 --flow 1
"""

from django.core.management.base import BaseCommand, CommandError

from chat_flow.services.session_expiry import expire_idle_sessions, idle_cutoff, stale_sessions


class Command(BaseCommand):
    help = "End chat-flow sessions idle beyond CHATFLOW_SESSION_IDLE_TIMEOUT_HOURS."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, help="Override the configured idle timeout.")
        parser.add_argument("--flow", type=int, help="Limit to one flow id.")
        parser.add_argument("--dry-run", action="store_true", help="Report what would be ended, without ending it.")

    def handle(self, *args, **options):
        hours = options.get("hours")
        if hours is not None and hours < 1:
            raise CommandError("--hours must be at least 1.")

        flow = None
        if options.get("flow"):
            from chat_flow.models import ChatFlow

            try:
                flow = ChatFlow.objects.get(pk=options["flow"])
            except ChatFlow.DoesNotExist:
                raise CommandError(f"No flow with id {options['flow']}.")

        self.stdout.write(f"Sessions that have not advanced since {idle_cutoff(hours).isoformat()}:")

        if options["dry_run"]:
            # Listed rather than counted, because the operator running this
            # after being blocked from editing wants to know *whose*
            # conversation is about to be ended.
            stale = stale_sessions(flow=flow, hours=hours).select_related("contact", "flow")
            for session in stale:
                self.stdout.write(
                    f"  flow {session.flow_id} · contact {session.contact_id} · "
                    f"at node {session.current_node_id} · last moved {session.updated_at.isoformat()}"
                )
            self.stdout.write(self.style.WARNING(f"{len(stale)} session(s) would be ended. Nothing was changed."))
            return

        ended = expire_idle_sessions(flow=flow, hours=hours)
        self.stdout.write(self.style.SUCCESS(f"{ended} session(s) ended."))
