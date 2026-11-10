"""Edge-case tests for importers, API, pagination, ICC client and sync command.

Each test asserts the *desired* behaviour. A failing test marks a real defect.
"""
import copy
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from . import client as client_mod
from .client import ICCClient, get_client_id, KNOWN_CLIENT_ID
from .importers import (
    string, number, decimal, boolean, datetime_value, status_from, innings_index,
    team, import_schedule, import_scorecard,
)
from .models import (
    Team, Match, Player, MatchPlayer, Innings, BattingStat, BowlingStat, MatchSnapshot,
)
from .pagination import CustomPagination


SCHEDULE = {
    'match_id': '271651', 'teama_id': '5', 'teama': 'New Zealand', 'teama_short': 'NZ',
    'teamb_id': '4', 'teamb': 'India', 'teamb_short': 'IND',
    'series_id': '15633', 'series_name': 'India in New Zealand',
    'venue_id': '49', 'venue': 'Eden Park, Auckland', 'match_type': 'ODI',
    'start_date': '11/4/2026T07:00:00+05:00', 'upcoming': True,
    'other_info': {'match_center': 'https://www.icc-cricket.com/matches/271651'},
}

SCORECARD = {'data': {
    'Matchdetail': {
        'Team_Home': '5', 'Team_Away': '4', 'Status': 'NZ won by 5 wickets',
        'Match': {'Id': '271651', 'Type': 'ODI', 'Live': True, 'StartDate': '11/4/2026T02:00+00:00'},
        'Series': {'Id': '15633', 'Name': 'India in New Zealand'},
        'Venue': {'Id': '49', 'Name': 'Eden Park, Auckland'},
    },
    'Teams': {
        '5': {'Name_Full': 'New Zealand', 'Players': {'1': {'Name_Full': 'Batter One'}}},
        '4': {'Name_Full': 'India', 'Players': {'2': {'Name_Full': 'Bowler Two'}}},
    },
    'Innings': [{
        'Number': 'First', 'Battingteam': '5', 'Bowlingteam': '4',
        'Total': '185', 'Wickets': '10', 'Overs': '40.2', 'Runrate': '4.58',
        'Batsmen': [{'Batsman': '1', 'Runs': '38', 'Balls': '43', 'Number': 1, 'Strikerate': '88.37'}],
        'Bowlers': [{'Bowler': '2', 'Overs': '6.4', 'Runs': '47', 'Wickets': '3', 'Economyrate': '7.05'}],
    }],
    'Notes': {'1': ['Power play']},
}}


def sched(**overrides):
    row = copy.deepcopy(SCHEDULE)
    row.update(overrides)
    return row


def card(mutator=None):
    payload = copy.deepcopy(SCORECARD)
    if mutator:
        mutator(payload['data'])
    return payload


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------
class HelperTests(SimpleTestCase):
    def test_string(self):
        self.assertEqual(string(None), '')
        self.assertEqual(string(0), '0')
        self.assertEqual(string(False), 'False')

    def test_number_valid_and_invalid(self):
        self.assertEqual(number('12'), 12)
        self.assertEqual(number(' 12 '), 12)
        self.assertIsNone(number(None))
        self.assertIsNone(number(''))
        self.assertIsNone(number('abc'))
        self.assertIsNone(number([]))
        self.assertEqual(number(0), 0)

    def test_number_float_string(self):
        # ICC sometimes sends "12.0"; it should not silently become NULL.
        self.assertEqual(number('12.0'), 12)

    def test_decimal(self):
        self.assertEqual(decimal('4.58'), Decimal('4.58'))
        self.assertIsNone(decimal('-'))
        self.assertIsNone(decimal(''))
        self.assertIsNone(decimal(None))

    def test_decimal_rejects_nan_and_infinity(self):
        # Strike rate for 0 balls is often reported as NaN / Infinity.
        self.assertIsNone(decimal('NaN'))
        self.assertIsNone(decimal('Infinity'))

    def test_boolean(self):
        for v in (True, 'true', 'TRUE', ' 1 ', 'yes', 1):
            self.assertTrue(boolean(v), v)
        for v in (False, None, '', 'false', '0', 'no', 0):
            self.assertFalse(boolean(v), v)

    def test_datetime_formats(self):
        self.assertIsNotNone(datetime_value('2026-11-04T07:00:00+05:00'))
        self.assertIsNotNone(datetime_value('11/4/2026T07:00:00+05:00'))
        self.assertIsNotNone(datetime_value('11/4/2026T02:00+00:00'))
        self.assertIsNone(datetime_value(''))
        self.assertIsNone(datetime_value(None))
        self.assertIsNone(datetime_value('garbage'))
        self.assertIsNone(datetime_value(1730000000))

    def test_datetime_naive_becomes_aware(self):
        dt = datetime_value('2026-11-04T07:00:00')
        self.assertTrue(timezone.is_aware(dt))

    def test_datetime_invalid_calendar_value(self):
        # Well-formatted but impossible date must not crash the import.
        self.assertIsNone(datetime_value('2026-02-30T10:00:00'))

    def test_status_from(self):
        self.assertEqual(status_from({'live': True, 'upcoming': True}), 'live')
        self.assertEqual(status_from({'upcoming': 'true'}), 'upcoming')
        self.assertEqual(status_from({'is_match_ended': '1'}), 'completed')
        self.assertEqual(status_from({'match_status': 'Result'}), 'completed')
        self.assertEqual(status_from({'match_display_status': 'LIVE'}), 'live')
        self.assertEqual(status_from({}), 'other')
        self.assertEqual(status_from({'live': 'false', 'match_status': 'abandoned'}), 'other')

    def test_innings_index(self):
        self.assertEqual(innings_index('First', 9), 1)
        self.assertEqual(innings_index('FOURTH', 9), 4)
        self.assertEqual(innings_index('3', 9), 3)
        self.assertEqual(innings_index(None, 2), 2)
        self.assertEqual(innings_index('Fifth', 5), 5)  # falls back to index
        self.assertEqual(innings_index('0', 7), 7)

    def test_innings_index_negative(self):
        # Negative number would violate PositiveSmallIntegerField; should fall back.
        self.assertEqual(innings_index('-1', 1), 1)


# ---------------------------------------------------------------------------
# Schedule import
# ---------------------------------------------------------------------------
class ScheduleImportTests(TestCase):
    def test_missing_match_id_raises(self):
        for value in (None, ''):
            with self.assertRaises(ValueError):
                import_schedule(sched(match_id=value))
        self.assertEqual(Match.objects.count(), 0)

    def test_integer_match_id_matches_string_id(self):
        import_schedule(sched(match_id=271651))
        import_schedule(sched(match_id='271651'))
        self.assertEqual(Match.objects.count(), 1)

    def test_minimal_row(self):
        m = import_schedule({'match_id': '1'})
        self.assertEqual(m.status, 'other')
        self.assertIsNone(m.team_a)
        self.assertIsNone(m.start_at)

    def test_null_other_info(self):
        m = import_schedule(sched(other_info=None))
        self.assertEqual(m.match_url, '')

    def test_start_at_preserved_when_missing_in_refresh(self):
        import_schedule(sched())
        m = import_schedule(sched(start_date=''))
        self.assertIsNotNone(m.start_at)

    def test_team_name_fallback_and_later_update(self):
        import_schedule(sched(teama=None, teama_display_name=None))
        self.assertEqual(Team.objects.get(icc_id='5').name, '5')
        import_schedule(sched())
        self.assertEqual(Team.objects.get(icc_id='5').name, 'New Zealand')

    def test_team_name_not_overwritten_by_blank(self):
        import_schedule(sched())
        import_schedule(sched(teama='', teama_short=''))
        t = Team.objects.get(icc_id='5')
        self.assertEqual((t.name, t.short_name), ('New Zealand', 'NZ'))

    def test_display_name_preferred(self):
        import_schedule(sched(teama_display_name='Black Caps'))
        self.assertEqual(Team.objects.get(icc_id='5').name, 'Black Caps')

    def test_zero_winner_id_does_not_create_phantom_team(self):
        # ICC uses "0" for "no winner / no toss yet".
        m = import_schedule(sched(winning_team_id='0', toss_won_by='0'))
        self.assertIsNone(m.winner)
        self.assertIsNone(m.toss_won_by)
        self.assertFalse(Team.objects.filter(icc_id='0').exists())

    def test_tba_team_id_zero(self):
        m = import_schedule(sched(teama_id='0', teama='TBA'))
        self.assertIsNone(m.team_a)

    def test_unicode_names(self):
        import_schedule(sched(venue='Estadio Ñandú — ক্রিকেট'))
        self.assertEqual(Match.objects.get().venue.name, 'Estadio Ñandú — ক্রিকেট')

    def test_overlong_field_does_not_crash(self):
        # format is max_length=30; ICC strings should be truncated / tolerated.
        m = import_schedule(sched(match_type='X' * 50))
        self.assertLessEqual(len(m.format), 30)

    def test_overlong_short_name_does_not_crash(self):
        import_schedule(sched(teama_short='S' * 40))
        self.assertEqual(Match.objects.count(), 1)

    def test_nul_byte_in_text(self):
        # PostgreSQL rejects NUL bytes in text/jsonb.
        m = import_schedule(sched(teama='New\x00Zealand'))
        self.assertNotIn('\x00', m.team_a.name)

    def test_schedule_refresh_keeps_completed_status_when_flags_missing(self):
        import_schedule(sched(upcoming=False, recent=True))
        m = import_schedule(sched(upcoming=None))
        self.assertEqual(m.status, 'completed')

    def test_schedule_refresh_keeps_scorecard_result(self):
        import_schedule(sched(upcoming=False, recent=True))
        import_scorecard(card(lambda d: d['Matchdetail']['Match'].update(Live=False, Recent=True)))
        self.assertEqual(Match.objects.get().result, 'NZ won by 5 wickets')
        m = import_schedule(sched(upcoming=False, recent=True))  # no match_result in row
        self.assertEqual(m.result, 'NZ won by 5 wickets')

    def test_schedule_refresh_keeps_raw_scorecard(self):
        import_schedule(sched())
        import_scorecard(card())
        m = import_schedule(sched())
        self.assertTrue(m.raw_scorecard)


# ---------------------------------------------------------------------------
# Scorecard import
# ---------------------------------------------------------------------------
class ScorecardImportTests(TestCase):
    def test_match_id_from_argument(self):
        payload = card(lambda d: d['Matchdetail']['Match'].pop('Id'))
        m = import_scorecard(payload, match_id='777')
        self.assertEqual(m.icc_id, '777')

    def test_missing_match_id_raises(self):
        payload = card(lambda d: d['Matchdetail']['Match'].pop('Id'))
        with self.assertRaises(ValueError):
            import_scorecard(payload)

    def test_empty_payload(self):
        with self.assertRaises(ValueError):
            import_scorecard({})
        with self.assertRaises(ValueError):
            import_scorecard({'data': None})

    def test_scorecard_before_schedule_creates_match(self):
        m = import_scorecard(card())
        self.assertEqual(m.team_a.name, 'New Zealand')
        self.assertEqual(Innings.objects.count(), 1)

    def test_numeric_fields_parsed(self):
        import_scorecard(card())
        inn = Innings.objects.get()
        self.assertEqual((inn.runs, inn.wickets, inn.run_rate), (185, 10, Decimal('4.58')))
        self.assertEqual(inn.notes, ['Power play'])
        self.assertEqual(BattingStat.objects.get().strike_rate, Decimal('88.37'))

    def test_non_numeric_values_become_null(self):
        def mut(d):
            d['Innings'][0].update(Total='-', Wickets='', Runrate='-')
            d['Innings'][0]['Batsmen'][0].update(Runs='DNB', Strikerate='-')
        import_scorecard(card(mut))
        inn = Innings.objects.get()
        self.assertIsNone(inn.runs)
        self.assertIsNone(inn.run_rate)
        self.assertIsNone(BattingStat.objects.get().runs)

    def test_negative_numbers_do_not_crash(self):
        def mut(d):
            d['Innings'][0]['Bowlers'][0]['Runs'] = '-1'
        import_scorecard(card(mut))
        self.assertEqual(BowlingStat.objects.count(), 1)

    def test_nan_strike_rate_does_not_crash(self):
        def mut(d):
            d['Innings'][0]['Batsmen'][0]['Strikerate'] = 'NaN'
        import_scorecard(card(mut))
        self.assertIsNone(BattingStat.objects.get().strike_rate)

    def test_huge_run_rate_does_not_crash(self):
        # run_rate is max_digits=7 -> 99999.99
        def mut(d):
            d['Innings'][0]['Runrate'] = '123456.78'
        import_scorecard(card(mut))
        self.assertEqual(Innings.objects.count(), 1)

    def test_overlong_overs(self):
        def mut(d):
            d['Innings'][0]['Overs'] = '40.2 (50 ov, target 300)'
        import_scorecard(card(mut))
        self.assertEqual(Innings.objects.count(), 1)

    def test_long_player_name(self):
        def mut(d):
            d['Teams']['5']['Players']['1']['Name_Full'] = 'N' * 200
        import_scorecard(card(mut))
        self.assertEqual(Player.objects.filter(icc_id='1').count(), 1)

    def test_batsman_without_id_skipped(self):
        def mut(d):
            d['Innings'][0]['Batsmen'].append({'Batsman': '', 'Runs': '5'})
            d['Innings'][0]['Bowlers'].append({'Runs': '5'})
        import_scorecard(card(mut))
        self.assertEqual(BattingStat.objects.count(), 1)
        self.assertEqual(BowlingStat.objects.count(), 1)

    def test_batsman_unknown_player_created_with_id_name(self):
        def mut(d):
            d['Innings'][0]['Batsmen'].append({'Batsman': '99', 'Runs': '5'})
        import_scorecard(card(mut))
        self.assertEqual(Player.objects.get(icc_id='99').name, '99')

    def test_missing_position_defaults_zero(self):
        def mut(d):
            d['Innings'][0]['Batsmen'][0].pop('Number')
        import_scorecard(card(mut))
        self.assertEqual(BattingStat.objects.get().position, 0)

    def test_null_players_entry(self):
        def mut(d):
            d['Teams']['5']['Players'] = {'1': None}
        import_scorecard(card(mut))
        self.assertEqual(Match.objects.count(), 1)

    def test_null_innings_lists(self):
        def mut(d):
            d['Innings'][0].update(Batsmen=None, Bowlers=None, FallofWickets=None, Partnerships=None)
        import_scorecard(card(mut))
        inn = Innings.objects.get()
        self.assertEqual((inn.fall_of_wickets, inn.partnerships), ([], []))

    def test_innings_unknown_team(self):
        def mut(d):
            d['Innings'][0]['Battingteam'] = '12345'
        import_scorecard(card(mut))
        self.assertIsNone(Innings.objects.get().batting_team)

    def test_innings_number_fallback_to_index(self):
        def mut(d):
            second = copy.deepcopy(d['Innings'][0])
            d['Innings'][0]['Number'] = None
            second['Number'] = None
            d['Innings'].append(second)
        import_scorecard(card(mut))
        self.assertEqual(list(Innings.objects.values_list('number', flat=True)), [1, 2])

    def test_duplicate_innings_labels_do_not_merge(self):
        def mut(d):
            d['Innings'].append(dict(copy.deepcopy(d['Innings'][0]), Total='200'))
        import_scorecard(card(mut))
        self.assertEqual(Innings.objects.count(), 2)

    def test_players_dropped_from_xi_are_removed(self):
        import_scorecard(card())
        def mut(d):
            d['Teams']['5']['Players'] = {}
        import_scorecard(card(mut))
        self.assertFalse(MatchPlayer.objects.filter(player__icc_id='1').exists())

    def test_innings_removed_by_icc_is_removed(self):
        def two(d):
            d['Innings'].append(dict(copy.deepcopy(d['Innings'][0]), Number='Second'))
        import_scorecard(card(two))
        import_scorecard(card())
        self.assertEqual(Innings.objects.count(), 1)

    def test_status_other_preserves_previous(self):
        import_schedule(sched(upcoming=False, live=True))
        def mut(d):
            d['Matchdetail']['Match']['Live'] = False
        m = import_scorecard(card(mut))
        self.assertEqual(m.status, 'live')

    def test_result_only_set_when_completed(self):
        m = import_scorecard(card())  # live
        self.assertEqual(m.result, '')
        def done(d):
            d['Matchdetail']['Match'].update(Live=False, Recent=True)
        m = import_scorecard(card(done))
        self.assertEqual(m.result, 'NZ won by 5 wickets')

    def test_boolean_flags(self):
        def mut(d):
            d['Teams']['5']['Players']['1'].update(Confirm_XI='true', Iscaptain=True, Iskeeper='0')
        import_scorecard(card(mut))
        mp = MatchPlayer.objects.get(player__icc_id='1')
        self.assertEqual((mp.confirmed_xi, mp.captain, mp.keeper), (True, True, False))

    def test_snapshot_dedup_and_change(self):
        import_scorecard(card(), keep_snapshot=True)
        import_scorecard(card(), keep_snapshot=True)
        self.assertEqual(MatchSnapshot.objects.count(), 1)
        def mut(d):
            d['Innings'][0]['Total'] = '190'
        import_scorecard(card(mut), keep_snapshot=True)
        self.assertEqual(MatchSnapshot.objects.count(), 2)

    def test_no_snapshot_by_default(self):
        import_scorecard(card())
        self.assertEqual(MatchSnapshot.objects.count(), 0)

    def test_nul_in_raw_json(self):
        def mut(d):
            d['Innings'][0]['Batsmen'][0]['Howout'] = 'c X b Y\x00'
        import_scorecard(card(mut))
        self.assertEqual(BattingStat.objects.count(), 1)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
class APITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user('api', password='x')
        cls.token = Token.objects.create(user=user)
        now = timezone.now()
        for i, (status, fmt, a, b) in enumerate([
            ('upcoming', 'ODI', 'Pakistan', 'India'),
            ('live', 'T20I', 'England', 'Australia'),
            ('completed', 'Test', 'South Africa', 'Pakistan'),
            ('other', 'ODI', 'Ireland', 'Scotland'),
        ], 1):
            import_schedule({
                'match_id': str(1000 + i), 'teama_id': f'a{i}', 'teama': a, 'teama_short': a[:3].upper(),
                'teamb_id': f'b{i}', 'teamb': b, 'match_type': fmt,
                'series_id': f's{i}', 'series_name': f'{a} tour {i}',
                'start_date': (now + timedelta(days=i - 2)).isoformat(),
                'live': status == 'live', 'upcoming': status == 'upcoming',
                'recent': status == 'completed',
            })
        for n in range(30):
            import_schedule({'match_id': f'bulk{n}', 'upcoming': True,
                             'start_date': (now + timedelta(days=40 + n)).isoformat()})

    def setUp(self):
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def get(self, url):
        return self.api.get(url)

    # auth ---------------------------------------------------------------
    def test_anonymous_rejected(self):
        self.assertEqual(APIClient().get('/api/matches/').status_code, 401)

    def test_bad_token_rejected(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION='Token nope')
        self.assertEqual(c.get('/api/matches/').status_code, 401)

    def test_session_login_accepted(self):
        # Settings enable session auth (browsable API / admin users); views honour it.
        c = APIClient()
        c.force_login(self.token.user)
        self.assertEqual(c.get('/api/matches/').status_code, 200)

    # read-only ----------------------------------------------------------
    def test_write_methods_not_allowed(self):
        for method in ('post', 'put', 'patch', 'delete'):
            r = getattr(self.api, method)('/api/matches/1001/')
            self.assertEqual(r.status_code, 405, method)

    # filters ------------------------------------------------------------
    def test_list_count(self):
        self.assertEqual(self.get('/api/matches/').json()['count'], 34)

    def test_status_filter_valid_and_invalid(self):
        self.assertEqual(self.get('/api/matches/?status=live').json()['count'], 1)
        self.assertEqual(self.get('/api/matches/?status=bogus').status_code, 400)

    def test_status_filter_case_insensitive(self):
        self.assertEqual(self.get('/api/matches/?status=LIVE').status_code, 200)

    def test_empty_params_ignored(self):
        r = self.get('/api/matches/?status=&format=&team=&q=&from_date=')
        self.assertEqual(r.json()['count'], 34)

    def test_format_case_insensitive(self):
        self.assertEqual(self.get('/api/matches/?format=odi').json()['count'], 2)

    def test_team_filter(self):
        self.assertEqual(self.get('/api/matches/?team=pakistan').json()['count'], 2)
        self.assertEqual(self.get('/api/matches/?team=pak').json()['count'], 2)
        self.assertEqual(self.get('/api/matches/?team=ENG').json()['count'], 1)

    def test_q_filter_no_duplicates(self):
        r = self.get('/api/matches/?q=Pakistan').json()
        ids = [m['icc_id'] for m in r['results']]
        self.assertEqual(len(ids), len(set(ids)))

    def test_q_special_characters(self):
        for q in ('%', '_', "'; DROP TABLE x;--", '\\'):
            self.assertEqual(self.api.get('/api/matches/', {'q': q}).status_code, 200, q)

    def test_date_filters(self):
        today = timezone.localdate().isoformat()
        r = self.get(f'/api/matches/?from_date={today}&to_date={today}')
        self.assertEqual(r.status_code, 200)

    def test_date_bad_format(self):
        self.assertEqual(self.get('/api/matches/?from_date=11-04-2026').status_code, 400)

    def test_date_impossible_value_returns_400(self):
        self.assertEqual(self.get('/api/matches/?from_date=2026-02-30').status_code, 400)

    def test_inverted_date_range(self):
        r = self.get('/api/matches/?from_date=2030-01-01&to_date=2020-01-01')
        self.assertEqual(r.json()['count'], 0)

    def test_combined_filters(self):
        r = self.get('/api/matches/?status=completed&format=Test&team=Pakistan')
        self.assertEqual(r.json()['count'], 1)

    # pagination ---------------------------------------------------------
    def test_default_page_size(self):
        self.assertEqual(len(self.get('/api/matches/').json()['results']), 25)

    def test_page_size_capped(self):
        self.assertEqual(len(self.get('/api/matches/?page_size=1000').json()['results']), 34)

    def test_page_size_invalid_falls_back(self):
        for v in ('0', '-5', 'abc'):
            self.assertEqual(len(self.get(f'/api/matches/?page_size={v}').json()['results']), 25, v)

    def test_page_out_of_range(self):
        self.assertEqual(self.get('/api/matches/?page=999').status_code, 404)
        self.assertEqual(self.get('/api/matches/?page=abc').status_code, 404)
        self.assertEqual(self.get('/api/matches/?page=last').status_code, 200)

    def test_null_start_at_ordering(self):
        import_schedule({'match_id': 'nodate'})
        self.assertEqual(self.get('/api/matches/?page_size=100').status_code, 200)

    # dedicated lists ----------------------------------------------------
    def test_live_upcoming_completed(self):
        self.assertEqual(self.get('/api/matches/live/').json()['count'], 1)
        self.assertEqual(self.get('/api/matches/completed/').json()['count'], 1)
        self.assertEqual(self.get('/api/matches/upcoming/').json()['count'], 31)

    def test_upcoming_sorted_ascending(self):
        starts = [m['start_at'] for m in self.get('/api/matches/upcoming/?page_size=100').json()['results']]
        self.assertEqual(starts, sorted(starts))

    def test_live_with_conflicting_status_param(self):
        self.assertEqual(self.get('/api/matches/live/?status=completed').json()['count'], 0)

    # detail / snapshots -------------------------------------------------
    def test_detail_404(self):
        self.assertEqual(self.get('/api/matches/does-not-exist/').status_code, 404)
        self.assertEqual(self.get('/api/matches/' + 'x' * 500 + '/').status_code, 404)

    def test_detail_hides_raw(self):
        import_scorecard(card())
        body = self.get('/api/matches/271651/').json()
        self.assertNotIn('raw_scorecard', body)
        self.assertNotIn('raw_schedule', body)
        self.assertEqual(body['innings'][0]['batting'][0]['player_name'], 'Batter One')

    def test_detail_query_count_bounded(self):
        import_scorecard(card())
        with self.assertNumQueries(9):  # token + match + 7 prefetches
            self.get('/api/matches/271651/')

    def test_snapshots(self):
        import_scorecard(card(), keep_snapshot=True)
        r = self.get('/api/matches/271651/snapshots/')
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(self.get('/api/matches/nope/snapshots/').status_code, 404)

    def test_match_with_id_live_is_unreachable(self):
        import_schedule({'match_id': 'live'})
        r = self.get('/api/matches/live/')
        self.assertIn('count', r.json())  # route collision: list wins, detail unreachable


# ---------------------------------------------------------------------------
# Unused CustomPagination class
# ---------------------------------------------------------------------------
class CustomPaginationTests(TestCase):
    def test_non_integer_page_size_in_response(self):
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory
        for i in range(3):
            Match.objects.create(icc_id=str(i))
        req = Request(APIRequestFactory().get('/', {'page_size': 'abc'}))
        p = CustomPagination()
        p.paginate_queryset(Match.objects.order_by('pk'), req)
        p.get_paginated_response([])  # should not raise ValueError


# ---------------------------------------------------------------------------
# ICC client (network mocked)
# ---------------------------------------------------------------------------
def fake_response(json_data=None, status=200, text=''):
    r = mock.Mock()
    r.status_code = status
    r.ok = status < 400
    r.text = text
    r.json.return_value = json_data
    r.raise_for_status.side_effect = None if status < 400 else requests.HTTPError(str(status))
    return r


class ClientTests(SimpleTestCase):
    def make(self):
        with override_settings(ICC_CLIENT_ID='abc'):
            return ICCClient()

    def test_client_id_precedence(self):
        s = mock.Mock()
        self.assertEqual(get_client_id(s, 'override'), 'override')
        with override_settings(ICC_CLIENT_ID=' configured '):
            self.assertEqual(get_client_id(s), 'configured')
        s.get.assert_not_called()

    def test_client_id_blank_setting_falls_back(self):
        s = mock.Mock()
        s.get.side_effect = requests.ConnectionError
        with override_settings(ICC_CLIENT_ID='   '):
            self.assertEqual(get_client_id(s), KNOWN_CLIENT_ID)

    def test_client_id_scraped(self):
        s = mock.Mock()
        s.get.return_value = fake_response(text='<script>var x="?client_id=scraped%3D%3D&a=1"</script>')
        with override_settings(ICC_CLIENT_ID=''):
            self.assertEqual(get_client_id(s), 'scraped==')

    def test_client_id_network_down_falls_back(self):
        s = mock.Mock()
        s.get.side_effect = requests.ConnectionError
        with override_settings(ICC_CLIENT_ID=''):
            self.assertEqual(get_client_id(s), KNOWN_CLIENT_ID)

    def test_json_non_object(self):
        c = self.make()
        c.session.get = mock.Mock(return_value=fake_response([1, 2]))
        with self.assertRaises(ValueError):
            c._json('u', {})

    def test_json_rejected_status(self):
        c = self.make()
        c.session.get = mock.Mock(return_value=fake_response({'meta': {'app_status_code': 0, 'message': 'bad'}}))
        with self.assertRaisesRegex(ValueError, 'bad'):
            c._json('u', {})

    def test_json_http_error(self):
        c = self.make()
        c.session.get = mock.Mock(return_value=fake_response({}, status=403))
        with self.assertRaises(requests.HTTPError):
            c._json('u', {})

    def test_schedule_pagination_and_dedup(self):
        c = self.make()
        pages = [
            {'meta': {'pagination': True}, 'data': {'matches': [{'match_id': 1}, {'match_id': 2}]}},
            {'meta': {'pagination': True}, 'data': {'matches': [{'match_id': 2}]}},
        ]
        c._json = mock.Mock(side_effect=pages)
        today = timezone.localdate()
        rows = c.schedule(today, today, page_size=2)
        self.assertEqual(sorted(str(r['match_id']) for r in rows), ['1', '2'])
        self.assertEqual(c._json.call_count, 2)

    def test_schedule_respects_max_pages(self):
        c = self.make()
        c._json = mock.Mock(return_value={'meta': {'pagination': True},
                                          'data': {'matches': [{'match_id': 1}]}})
        today = timezone.localdate()
        c.schedule(today, today, page_size=1, max_pages=3)
        self.assertEqual(c._json.call_count, 3)

    def test_schedule_null_data(self):
        c = self.make()
        c._json = mock.Mock(return_value={'data': None})
        today = timezone.localdate()
        self.assertEqual(c.schedule(today, today), [])

    def test_schedule_bad_matches_type(self):
        c = self.make()
        c._json = mock.Mock(return_value={'data': {'matches': {'a': 1}}})
        today = timezone.localdate()
        with self.assertRaises(ValueError):
            c.schedule(today, today)

    def test_schedule_unknown_kind(self):
        c = self.make()
        today = timezone.localdate()
        with self.assertRaises(KeyError):
            c.schedule(today, today, kind='nope')


# ---------------------------------------------------------------------------
# sync_icc management command
# ---------------------------------------------------------------------------
class SyncCommandTests(TestCase):
    def run_cmd(self, fake, *args):
        out, err = StringIO(), StringIO()
        with mock.patch('cricket.management.commands.sync_icc.ICCClient', return_value=fake), \
             mock.patch('cricket.management.commands.sync_icc.time.sleep'):
            call_command('sync_icc', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def fake(self, rows, scorecard=None):
        f = mock.Mock()
        f.schedule.return_value = rows
        f.scorecard.return_value = scorecard or card()
        return f

    def test_argument_validation(self):
        for args in (['--days-back', '-1'], ['--days-ahead', '-1'], ['--page-size', '0'],
                     ['--page-size', '101'], ['--watch', '--interval', '5']):
            with self.assertRaises(CommandError, msg=args):
                self.run_cmd(self.fake([]), *args)

    def test_max_pages_validation(self):
        with self.assertRaises(CommandError):
            self.run_cmd(self.fake([]), '--max-pages', '0')

    def test_negative_delay_validation(self):
        with self.assertRaises(CommandError):
            self.run_cmd(self.fake([]), '--delay', '-1')

    def test_upcoming_skips_scorecard(self):
        f = self.fake([sched()])
        out, _ = self.run_cmd(f)
        f.scorecard.assert_not_called()
        self.assertIn('Saved 1 schedules, 0 scorecards; 0 errors', out)

    def test_live_fetches_scorecard(self):
        f = self.fake([sched(upcoming=False, live=True)])
        out, _ = self.run_cmd(f)
        self.assertIn('1 scorecards; 0 errors', out)
        self.assertEqual(Innings.objects.count(), 1)

    def test_no_details(self):
        f = self.fake([sched(upcoming=False, live=True)])
        self.run_cmd(f, '--no-details')
        f.scorecard.assert_not_called()

    def test_bad_row_does_not_stop_others(self):
        f = self.fake([{'match_id': ''}, sched()])
        out, err = self.run_cmd(f)
        self.assertIn('Saved 1 schedules, 0 scorecards; 1 errors', out)
        self.assertIn('missing match_id', err)

    def test_scorecard_failure_still_saves_schedule(self):
        f = self.fake([sched(upcoming=False, live=True)])
        f.scorecard.side_effect = requests.Timeout('slow')
        out, _ = self.run_cmd(f)
        self.assertIn('1 errors', out)
        self.assertEqual(Match.objects.count(), 1)

    def test_schedule_failure_raises_command_error_and_closes(self):
        f = self.fake([])
        f.schedule.side_effect = requests.ConnectionError('down')
        with self.assertRaises(CommandError):
            self.run_cmd(f)
        f.close.assert_called_once()

    def test_watch_survives_transient_schedule_failure(self):
        f = self.fake([])
        f.schedule.side_effect = [requests.ConnectionError('blip'), KeyboardInterrupt()]
        out, _ = self.run_cmd(f, '--mode', 'live', '--watch', '--interval', '15')
        self.assertIn('Sync interrupted', out)

    def test_live_mode_window(self):
        f = self.fake([])
        self.run_cmd(f, '--mode', 'live', '--days-back', '30')
        start, end, kind = f.schedule.call_args.args
        self.assertEqual((end - start).days, 4)
        self.assertEqual(kind, 'live')
