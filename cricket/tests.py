from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from django.urls import reverse
from django.utils import timezone
from .importers import import_schedule, import_scorecard
from .models import Match, Innings, BattingStat, BowlingStat


SCHEDULE = {
    'match_id': '271651', 'teama_id': '5', 'teama': 'New Zealand',
    'teamb_id': '4', 'teamb': 'India', 'series_id': '15633', 'series_name': 'India in New Zealand',
    'venue_id': '49', 'venue': 'Eden Park, Auckland', 'match_type': 'ODI',
    'start_date': '11/4/2026T07:00:00+05:00', 'upcoming': True,
    'other_info': {'match_center': 'https://www.icc-cricket.com/matches/271651/new-zealand-vs-india'},
}
SCORECARD = {'data': {
    'Matchdetail': {'Team_Home': '5', 'Team_Away': '4',
         'Match': {'Id': '271651', 'Type': 'ODI', 'Live': True, 'StartDate': '11/4/2026T02:00+00:00'},
         'Series': {'Id': '15633', 'Name': 'India in New Zealand'},
         'Venue': {'Id': '49', 'Name': 'Eden Park, Auckland'}},
    'Teams': {'5': {'Name_Full': 'New Zealand', 'Players': {'1': {'Name_Full': 'Batter One'}}},
              '4': {'Name_Full': 'India', 'Players': {'2': {'Name_Full': 'Bowler Two'}}}},
    'Innings': [{'Number': 'First', 'Battingteam': '5', 'Bowlingteam': '4',
                 'Total': '185', 'Wickets': '10', 'Overs': '40.2',
                 'Batsmen': [{'Batsman': '1', 'Runs': '38', 'Balls': '43', 'Number': 1}],
                 'Bowlers': [{'Bowler': '2', 'Overs': '6.4', 'Runs': '47', 'Wickets': '3'}]}],
    'Notes': {'1': ['Power play']},
}}


class ImportTests(TestCase):
    def test_repeat_sync_updates_in_place(self):
        import_schedule(SCHEDULE)
        import_schedule(SCHEDULE)
        self.assertEqual(Match.objects.count(), 1)
        import_scorecard(SCORECARD)
        import_scorecard(SCORECARD)
        self.assertEqual(Innings.objects.count(), 1)
        self.assertEqual(BattingStat.objects.count(), 1)
        self.assertEqual(BowlingStat.objects.count(), 1)
        self.assertEqual(Match.objects.get().status, 'live')
        self.assertEqual(Innings.objects.get().overs, '40.2')

    def test_read_only_api(self):
        import_schedule(SCHEDULE)
        self.assertEqual(self.client.get('/api/matches/').status_code, 401)
        user = get_user_model().objects.create_user('reader')
        auth = {'HTTP_AUTHORIZATION': f'Token {Token.objects.create(user=user).key}'}
        r = self.client.get('/api/matches/?status=upcoming', **auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['count'], 1)
        self.assertEqual(self.client.get('/api/matches/271651/', **auth).status_code, 200)
        self.assertEqual(self.client.post('/api/matches/', {}, **auth).status_code, 405)
