"""
Management command to fix missing currency values in broadcast cost fields.
Adds default currency (INR) to initial_cost and refund_amount fields.
"""
from broadcast.models import Broadcast
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Fix missing currency values in broadcast initial_cost and refund_amount fields'

    def add_arguments(self, parser):
        parser.add_argument(
            '--currency',
            type=str,
            default='USD',
            help='Currency code to use as default (default: INR)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        currency = options['currency']
        dry_run = options['dry_run']
        
        self.stdout.write(self.style.WARNING(f'Fixing broadcast currency fields...'))
        self.stdout.write(f'Default currency: {currency}')
        self.stdout.write(f'Dry run: {dry_run}')
        self.stdout.write('')
        
        # Use raw SQL to update currency fields directly
        with connection.cursor() as cursor:
            # Count records that need updating
            cursor.execute("""
                SELECT COUNT(*) FROM broadcast_broadcast 
                WHERE (initial_cost IS NOT NULL AND initial_cost_currency IS NULL)
                   OR (refund_amount IS NOT NULL AND refund_amount_currency IS NULL)
            """)
            total_count = cursor.fetchone()[0]
            
            if total_count == 0:
                self.stdout.write(self.style.SUCCESS('No records need updating!'))
                return
            
            self.stdout.write(f'Found {total_count} broadcasts with missing currency values')
            
            # Count initial_cost updates needed
            cursor.execute("""
                SELECT COUNT(*) FROM broadcast_broadcast 
                WHERE initial_cost IS NOT NULL AND initial_cost_currency IS NULL
            """)
            initial_cost_count = cursor.fetchone()[0]
            
            # Count refund_amount updates needed
            cursor.execute("""
                SELECT COUNT(*) FROM broadcast_broadcast 
                WHERE refund_amount IS NOT NULL AND refund_amount_currency IS NULL
            """)
            refund_amount_count = cursor.fetchone()[0]
            
            self.stdout.write(f'  - initial_cost_currency: {initial_cost_count} records')
            self.stdout.write(f'  - refund_amount_currency: {refund_amount_count} records')
            self.stdout.write('')
            
            if dry_run:
                self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made'))
                
                # Show sample records
                cursor.execute("""
                    SELECT id, name, initial_cost, initial_cost_currency, 
                           refund_amount, refund_amount_currency
                    FROM broadcast_broadcast 
                    WHERE (initial_cost IS NOT NULL AND initial_cost_currency IS NULL)
                       OR (refund_amount IS NOT NULL AND refund_amount_currency IS NULL)
                    LIMIT 5
                """)
                
                self.stdout.write('\nSample records that would be updated:')
                for row in cursor.fetchall():
                    self.stdout.write(f'  ID: {row[0]}, Name: {row[1]}')
                    if row[2] is not None and row[3] is None:
                        self.stdout.write(f'    - initial_cost: {row[2]} (no currency) -> {row[2]} {currency}')
                    if row[4] is not None and row[5] is None:
                        self.stdout.write(f'    - refund_amount: {row[4]} (no currency) -> {row[4]} {currency}')
                
                return
            
            # Update initial_cost_currency
            if initial_cost_count > 0:
                cursor.execute("""
                    UPDATE broadcast_broadcast 
                    SET initial_cost_currency = %s
                    WHERE initial_cost IS NOT NULL AND initial_cost_currency IS NULL
                """, [currency])
                self.stdout.write(self.style.SUCCESS(f'✓ Updated initial_cost_currency for {initial_cost_count} records'))
            
            # Update refund_amount_currency
            if refund_amount_count > 0:
                cursor.execute("""
                    UPDATE broadcast_broadcast 
                    SET refund_amount_currency = %s
                    WHERE refund_amount IS NOT NULL AND refund_amount_currency IS NULL
                """, [currency])
                self.stdout.write(self.style.SUCCESS(f'✓ Updated refund_amount_currency for {refund_amount_count} records'))
            
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(f'Successfully updated {total_count} broadcast records!'))
