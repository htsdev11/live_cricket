"""Requests-only ICC Sportz API client, using observed ICC request parameters."""
import re
from datetime import timedelta
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = 'https://assets-icc.sportz.io/cricket/v1'
KNOWN_CLIENT_ID = 'tPZJbRgIub3Vua93/DWtyQ=='  # May be rotated by ICC.
PATTERNS = [
    re.compile(r'[?&]client_id=([^&"\'\s]+)', re.I),
    re.compile(r'client_id\s*[=:]\s*["\']([^"\']+)', re.I),
    re.compile(r'["\']clientId["\']\s*:\s*["\']([^"\']+)', re.I),
]


def session():
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; ICCMatchSync/1.0)',
                      'Accept': 'application/json,text/plain,*/*',
                      'Referer': 'https://www.icc-cricket.com/fixtures-results',
                      'Origin': 'https://www.icc-cricket.com'})
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=frozenset(['GET']), respect_retry_after_header=True)
    s.mount('https://', HTTPAdapter(max_retries=retry))
    return s


def get_client_id(s, override=None):
    if override and override.strip():
        return override.strip()
    configured = (getattr(settings, 'ICC_CLIENT_ID', '') or '').strip()
    if configured:  # a blank or whitespace-only value falls through to discovery
        return configured
    for page in ('https://www.icc-cricket.com/fixtures-results', 'https://www.icc-cricket.com/'):
        try:
            r = s.get(page, timeout=12)
            r.raise_for_status()
            urls = [urljoin(page, tag['src']) for tag in BeautifulSoup(r.text, 'html.parser').select('script[src]')[:20]]
            sources = [r.text]
            for u in urls:
                if urlparse(u).hostname not in ('www.icc-cricket.com', 'icc-cricket.com', 'assets-icc.sportz.io'):
                    continue
                try:
                    j = s.get(u, timeout=10)
                    if j.ok:
                        sources.append(j.text[:2_000_000])
                except requests.RequestException:
                    pass
            for source in sources:
                for pattern in PATTERNS:
                    match = pattern.search(source)
                    if match:
                        return unquote(match.group(1).replace('\\/', '/'))
        except requests.RequestException:
            pass
    return KNOWN_CLIENT_ID


class ICCClient:
    def __init__(self, client_id=None):
        self.session = session()
        self.client_id = get_client_id(self.session, client_id)

    def _json(self, url, params):
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError('ICC returned unexpected non-object JSON')
        meta = data.get('meta') or {}
        if str(meta.get('app_status_code', '1')) != '1':
            raise ValueError(f'ICC API rejected request: {meta.get("message", meta)}')
        return data

    def schedule(self, start, end, kind='all', page_size=50, max_pages=30):
        """Fetch a date-scoped ICC schedule, paginated. Returned list is not guaranteed exhaustive."""
        flags = {
            'all': ('true', 'true', 'true'),
            'live': ('true', 'false', 'false'),
            'completed': ('false', 'true', 'false'),
            'upcoming': ('false', 'false', 'true'),
        }
        live, recent, upcoming = flags[kind]
        params = {
            'client_id': self.client_id, 'feed_format': 'json',
            'from_date': start.strftime('%Y%m%d'), 'to_date': end.strftime('%Y%m%d'),
            'is_deleted': 'false', 'is_live': live, 'is_recent': recent,
            'is_upcoming': upcoming, 'lang': 'en', 'league_ids': '1,9',
            'page_size': page_size, 'pagination': 'true', 'timezone': '0500',
        }
        results = {}
        for page in range(1, max_pages + 1):
            params['page_number'] = page
            payload = self._json(f'{BASE}/schedule', params)
            rows = (payload.get('data') or {}).get('matches') or []
            if not isinstance(rows, list):
                raise ValueError('Expected ICC data.matches list')
            for item in rows:
                if isinstance(item, dict) and item.get('match_id'):
                    results[str(item['match_id'])] = item
            if not rows or len(rows) < page_size or not (payload.get('meta') or {}).get('pagination'):
                break
        return list(results.values())

    def scorecard(self, match_id):
        return self._json(f'{BASE}/game/scorecard', {'client_id': self.client_id,
                          'feed_format': 'json', 'game_id': str(match_id), 'lang': 'en'})

    def close(self):
        self.session.close()
