import logging
from functools import cached_property
from typing import List

from django.db import transaction
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class BroadcastService(BaseModel):
    model_config = ConfigDict(ignored_types=(cached_property,))

    broadcast_id: int

    @cached_property
    def broadcast(self):
        from broadcast.models import Broadcast

        return Broadcast.objects.get(id=self.broadcast_id)

    def create_message_records_only(self, recipients):
        """
        UPDATED FOR CHUNKED PROCESSING: Only create BroadcastMessage records

        Individual task queueing is now handled by the chunked batch processor
        This method focuses on bulk creating all message records for tracking
        """
        from broadcast.models import BroadcastMessage, MessageStatusChoices

        # Prepare records for bulk creation
        message_records = []
        for recipient in recipients:
            message_records.append(
                BroadcastMessage(
                    broadcast=self.broadcast,
                    contact=recipient,
                    status=MessageStatusChoices.PENDING,  # Changed from QUEUED to PENDING
                    created_by=self.broadcast.created_by,
                    updated_by=self.broadcast.updated_by,
                )
            )

        # Bulk insert with transaction
        with transaction.atomic():
            created_messages = BroadcastMessage.objects.bulk_create(
                message_records,
                update_conflicts=True,
                update_fields=["status", "updated_at", "updated_by"],
                unique_fields=["broadcast", "contact"],
            )

        logger.info(f"Bulk created {len(created_messages)} message records for broadcast {self.broadcast_id}")

        return created_messages

    def create_batches(self, batch_size: int = 100) -> List[List[int]]:
        """
        Create batches of BroadcastMessage IDs for chunked processing.
        """
        from broadcast.models import BroadcastMessage

        message_ids = list(BroadcastMessage.objects.filter(broadcast_id=self.broadcast_id).values_list("id", flat=True))

        # check if batch_size is more than total messages
        if batch_size >= len(message_ids):
            logger.info(
                f"Batch size {batch_size} is greater than or equal to total messages {len(message_ids)}. Creating single batch."
            )
            return [message_ids]

        # Split message IDs into batches
        batches = [message_ids[i : i + batch_size] for i in range(0, len(message_ids), batch_size)]

        logger.info(f"Created {len(batches)} batches for broadcast {self.broadcast_id} with batch size {batch_size}")
        return batches

    def __call__(self, *args, **kwargs):
        """
        UPDATED FOR CHUNKED PROCESSING: Main execution method for broadcast processing

        This method now only creates individual message records.
        The chunked batch processing is handled by process_broadcast_task.
        """

        # Get broadcast (don't update status here - will be updated by main task)
        broadcast = self.broadcast

        # Get recipients
        recipients = list(broadcast.recipients.all())

        if not recipients:
            logger.warning(f"No recipients found for broadcast {self.broadcast_id}")
            return {"status": "failed", "message": "No recipients found", "total_recipients": 0, "created_messages": 0}

        # Create message records only (no individual task queueing)
        try:
            created_messages = self.create_message_records_only(recipients)

            logger.info(
                f"Successfully created {len(created_messages)} message records for broadcast {self.broadcast_id}"
            )
            batches = self.create_batches(batch_size=1000)  # Example batch size

            # Queue Celery tasks for each batch
            from broadcast.tasks import process_broadcast_messages_batch

            batch_task_ids = []
            for batch_index, batch in enumerate(batches):
                logger.info(f"Queueing batch {batch_index + 1}/{len(batches)} with {len(batch)} messages")
                task = process_broadcast_messages_batch.delay(batch)
                batch_task_ids.append(task.id)
                # Assign task_id to each message in the batch
                from broadcast.models import BroadcastMessage

                BroadcastMessage.objects.filter(id__in=batch).update(task_id=task.id)

            logger.info(f"Queued {len(batch_task_ids)} batch processing tasks for broadcast {self.broadcast_id}")

            return {
                "status": "success",
                "message": "Message records created and batch processing queued",
                "total_recipients": len(recipients),
                "created_messages": len(created_messages),
                "total_batches": len(batches),
                "batch_task_ids": batch_task_ids,
                "broadcast_id": self.broadcast_id,
            }

        except Exception as e:
            logger.error(f"Error creating message records for broadcast {self.broadcast_id}: {str(e)}")

            return {
                "status": "error",
                "message": str(e),
                "total_recipients": len(recipients),
                "created_messages": 0,
                "broadcast_id": self.broadcast_id,
            }
