from rest_framework import serializers
from .models import Team, Series, Venue, Match, Innings, BattingStat, BowlingStat, MatchSnapshot


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ('icc_id', 'name', 'short_name', 'url')


class SeriesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Series
        fields = ('icc_id', 'name', 'short_name', 'tour_name')


class VenueSerializer(serializers.ModelSerializer):
    class Meta:
        model = Venue
        fields = ('icc_id', 'name', 'city', 'country')


class BattingSerializer(serializers.ModelSerializer):
    player_id = serializers.CharField(source='player.icc_id')
    player_name = serializers.CharField(source='player.name')

    class Meta:
        model = BattingStat
        fields = ('player_id', 'player_name', 'position', 'runs', 'balls', 'fours', 'sixes',
                  'strike_rate', 'dismissal', 'is_batting')


class BowlingSerializer(serializers.ModelSerializer):
    player_id = serializers.CharField(source='player.icc_id')
    player_name = serializers.CharField(source='player.name')

    class Meta:
        model = BowlingStat
        fields = ('player_id', 'player_name', 'overs', 'maidens', 'runs', 'wickets',
                  'economy', 'is_bowling_now')


class InningsSerializer(serializers.ModelSerializer):
    batting_team = TeamSerializer(read_only=True)
    bowling_team = TeamSerializer(read_only=True)
    batting = BattingSerializer(many=True, read_only=True)
    bowling = BowlingSerializer(many=True, read_only=True)

    class Meta:
        model = Innings
        fields = ('number', 'batting_team', 'bowling_team', 'runs', 'wickets', 'overs',
                  'target', 'run_rate', 'extras', 'fall_of_wickets', 'partnerships',
                  'powerplays', 'notes', 'batting', 'bowling')


class MatchListSerializer(serializers.ModelSerializer):
    team_a = TeamSerializer(read_only=True)
    team_b = TeamSerializer(read_only=True)
    series = SeriesSerializer(read_only=True)
    venue = VenueSerializer(read_only=True)
    winner = TeamSerializer(read_only=True)

    class Meta:
        model = Match
        fields = ('icc_id', 'format', 'status', 'status_message', 'match_number', 'start_at',
                  'team_a', 'team_b', 'series', 'venue', 'winner', 'result', 'match_url',
                  'last_schedule_sync', 'last_scorecard_sync')


class MatchDetailSerializer(MatchListSerializer):
    innings = InningsSerializer(many=True, read_only=True)

    class Meta(MatchListSerializer.Meta):
        fields = MatchListSerializer.Meta.fields + ('toss_decision', 'equation', 'innings')


class SnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchSnapshot
        fields = ('recorded_at', 'status', 'score', 'equation')
