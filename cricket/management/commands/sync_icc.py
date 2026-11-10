import time
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from cricket.client import ICCClient
from cricket.importers import import_schedule, import_scorecard


class Command(BaseCommand):
    help = 'Sync ICC matches from schedule and scorecard JSON into PostgreSQL'

    def add_arguments(self, parser):
        parser.add_argument('--mode', choices=['all', 'live', 'completed', 'upcoming'], default='all')
        parser.add_argument('--days-back', type=int, default=7)
        parser.add_argument('--days-ahead', type=int, default=30)
        parser.add_argument('--client-id', help='Overrides settings.ICC_CLIENT_ID')
        parser.add_argument('--page-size', type=int, default=50)
        parser.add_argument('--max-pages', type=int, default=30)
        parser.add_argument('--no-details', action='store_true', help='Only fetch schedule')
        parser.add_argument('--watch', action='store_true', help='Run repeatedly (use for live mode)')
        parser.add_argument('--interval', type=int, default=60)
        parser.add_argument('--snapshots', action='store_true', help='Save changes to live scores over time')
        parser.add_argument('--delay', type=float, default=0.5, help='Seconds between scorecard requests')

    def handle(self, *args, **options):
        if options['days_back'] < 0 or options['days_ahead'] < 0:
            raise CommandError('Date windows must be non-negative')
        if options['watch'] and options['interval'] < 15:
            raise CommandError('Use --interval >= 15 for watch mode')
        if not 1 <= options['page_size'] <= 100:
            raise CommandError('Page size must be between 1 and 100')
        if options['max_pages'] < 1:
            raise CommandError('Use --max-pages >= 1')
        if options['delay'] < 0:
            raise CommandError('Use --delay >= 0')
        client = ICCClient(client_id=options['client_id'])
        try:
            while True:
                try:
                    self.sync_once(client, options)
                except Exception as exc:
                    if not options['watch']:
                        raise CommandError(f'ICC schedule sync failed: {exc}') from exc
                    # In watch mode a transient outage must not end live polling.
                    self.stderr.write(f'ICC schedule sync failed, retrying in '
                                      f'{options["interval"]}s: {exc}')
                if not options['watch']:
                    break
                time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('Sync interrupted')
        finally:
            client.close()

    def sync_once(self, client, options):
        today = timezone.localdate()
        if options['mode'] == 'live':
            start, end = today - timedelta(days=2), today + timedelta(days=2)
        else:
            start = today - timedelta(days=options['days_back'])
            end = today + timedelta(days=options['days_ahead'])
        rows = client.schedule(start, end, options['mode'],
                               page_size=options['page_size'],
                               max_pages=options['max_pages'])
        self.stdout.write(f'ICC schedule: received {len(rows)} distinct match records')
        saved = details = failed = 0
        for row in rows:
            try:
                match = import_schedule(row)
                saved += 1
                if not options['no_details'] and match.status in ('live', 'completed'):
                    payload = client.scorecard(match.icc_id)
                    import_scorecard(payload, match.icc_id,
                                     keep_snapshot=options['snapshots'])
                    details += 1
                    if options['delay']:
                        time.sleep(options['delay'])
            except Exception as exc:
                failed += 1
                match_id = row.get('match_id', '?') if isinstance(row, dict) else '?'
                self.stderr.write(f'Match {match_id}: {exc}')
        self.stdout.write(self.style.SUCCESS(
            f'Saved {saved} schedules, {details} scorecards; {failed} errors'))
