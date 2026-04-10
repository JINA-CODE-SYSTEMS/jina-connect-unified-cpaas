import glob
import os

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Remove migration files for all apps (keeps __init__.py files)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--exclude',
            nargs='*',
            default=[],
            help='List of apps to exclude from migration removal'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force deletion without confirmation prompt'
        )

    def handle(self, *args, **options):
        excluded_apps = options['exclude']
        dry_run = options['dry_run']
        force = options['force']
        
        self.stdout.write(self.style.WARNING('🗑️  Migration Removal Tool'))
        self.stdout.write('=' * 50)
        
        # Get only Django project apps (not third-party packages)
        project_apps = []
        base_dir = str(settings.BASE_DIR)
        
        for app_config in apps.get_app_configs():
            # Check if app is in the project directory (not in site-packages)
            app_path = str(app_config.path)
            if app_path.startswith(base_dir) and 'site-packages' not in app_path:
                project_apps.append(app_config.label)
        
        # Filter out excluded apps
        target_apps = [app for app in project_apps if app not in excluded_apps]
        
        if excluded_apps:
            self.stdout.write(
                self.style.WARNING(f'📋 Excluding apps: {", ".join(excluded_apps)}')
            )
        
        self.stdout.write(
            self.style.SUCCESS(f'🎯 Django project apps: {", ".join(project_apps)}')
        )
        self.stdout.write(
            self.style.SUCCESS(f'📋 Target apps (after exclusions): {", ".join(target_apps)}')
        )
        
        migration_files_found = []
        
        # Find all migration files
        for app_name in target_apps:
            try:
                app_config = apps.get_app_config(app_name)
                migrations_dir = os.path.join(app_config.path, 'migrations')
                
                if os.path.exists(migrations_dir):
                    # Find all .py files except __init__.py
                    pattern = os.path.join(migrations_dir, '*.py')
                    files = glob.glob(pattern)
                    
                    # Filter out __init__.py and __pycache__
                    migration_files = [
                        f for f in files 
                        if not f.endswith('__init__.py')
                    ]
                    
                    if migration_files:
                        migration_files_found.extend(migration_files)
                        self.stdout.write(
                            f'📁 {app_name}: {len(migration_files)} migration files found'
                        )
                    
                    # Also check for __pycache__ directory
                    pycache_dir = os.path.join(migrations_dir, '__pycache__')
                    if os.path.exists(pycache_dir):
                        migration_files_found.append(pycache_dir)
                        
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'❌ Error processing app {app_name}: {e}')
                )
        
        if not migration_files_found:
            self.stdout.write(self.style.SUCCESS('✅ No migration files found to remove.'))
            return
        
        # Show what will be deleted
        self.stdout.write('\n' + '=' * 50)
        self.stdout.write(self.style.WARNING('📋 Files/Directories to be removed:'))
        self.stdout.write('=' * 50)
        
        for file_path in migration_files_found:
            if os.path.isdir(file_path):
                self.stdout.write(f'📂 {file_path}/ (directory)')
            else:
                self.stdout.write(f'📄 {file_path}')
        
        self.stdout.write(f'\n🔢 Total: {len(migration_files_found)} items')
        
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS('\n✅ Dry run completed. No files were actually deleted.')
            )
            return
        
        # Confirmation prompt
        if not force:
            self.stdout.write('\n' + '⚠️ ' * 20)
            self.stdout.write(
                self.style.ERROR('WARNING: This action cannot be undone!')
            )
            self.stdout.write(
                self.style.ERROR('Make sure you have backups of your data.')
            )
            self.stdout.write('⚠️ ' * 20 + '\n')
            
            confirm = input('Are you sure you want to delete these migration files? (yes/no): ')
            
            if confirm.lower() not in ['yes', 'y']:
                self.stdout.write(self.style.SUCCESS('❌ Operation cancelled.'))
                return
        
        # Delete the files
        deleted_count = 0
        error_count = 0
        
        self.stdout.write('\n' + '🗑️ ' * 20)
        self.stdout.write('Starting deletion...')
        self.stdout.write('🗑️ ' * 20 + '\n')
        
        for file_path in migration_files_found:
            try:
                if os.path.isdir(file_path):
                    # Remove directory and all contents
                    import shutil
                    shutil.rmtree(file_path)
                    self.stdout.write(f'🗂️  Deleted directory: {file_path}')
                else:
                    # Remove file
                    os.remove(file_path)
                    self.stdout.write(f'🗄️  Deleted file: {file_path}')
                
                deleted_count += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'❌ Error deleting {file_path}: {e}')
                )
                error_count += 1
        
        # Summary
        self.stdout.write('\n' + '=' * 50)
        self.stdout.write(self.style.SUCCESS('🎉 SUMMARY'))
        self.stdout.write('=' * 50)
        self.stdout.write(f'✅ Successfully deleted: {deleted_count} items')
        
        if error_count > 0:
            self.stdout.write(f'❌ Errors encountered: {error_count} items')
        
        self.stdout.write('\n' + '💡 ' * 20)
        self.stdout.write(self.style.SUCCESS('Next steps:'))
        self.stdout.write('1. Run: python manage.py makemigrations')
        self.stdout.write('2. Run: python manage.py migrate')
        self.stdout.write('💡 ' * 20)
        
        if deleted_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'\n🎊 Migration removal completed successfully!')
            )
