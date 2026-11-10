from django.db import models
from django.db.models import Q


class Team(models.Model):
    icc_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=150)
    short_name = models.CharField(max_length=30, blank=True)
    url = models.URLField(blank=True)

    def __str__(self):
        return self.name


class Series(models.Model):
    icc_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=250)
    short_name = models.CharField(max_length=200, blank=True)
    tour_name = models.CharField(max_length=250, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class Venue(models.Model):
    icc_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=250)
    city = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class Match(models.Model):
    class Status(models.TextChoices):
        UPCOMING = 'upcoming', 'Upcoming'
        LIVE = 'live', 'Live'
        COMPLETED = 'completed', 'Completed'
        OTHER = 'other', 'Other'

    icc_id = models.CharField(max_length=32, unique=True, db_index=True)
    match_file = models.CharField(max_length=100, blank=True)
    match_number = models.CharField(max_length=80, blank=True)
    format = models.CharField(max_length=30, blank=True, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OTHER, db_index=True)
    status_message = models.TextField(blank=True)
    start_at = models.DateTimeField(null=True, blank=True, db_index=True)
    series = models.ForeignKey(Series, null=True, blank=True, on_delete=models.SET_NULL, related_name='matches')
    venue = models.ForeignKey(Venue, null=True, blank=True, on_delete=models.SET_NULL, related_name='matches')
    team_a = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='home_matches')
    team_b = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='away_matches')
    winner = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='won_matches')
    result = models.TextField(blank=True)
    toss_won_by = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='toss_wins')
    toss_decision = models.CharField(max_length=40, blank=True)
    equation = models.TextField(blank=True)
    match_url = models.URLField(blank=True)
    raw_schedule = models.JSONField(default=dict, blank=True)
    raw_scorecard = models.JSONField(default=dict, blank=True)
    last_schedule_sync = models.DateTimeField(null=True, blank=True)
    last_scorecard_sync = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-start_at', '-id')
        indexes = [models.Index(fields=['status', 'start_at'])]

    def __str__(self):
        return f'{self.icc_id}: {self.team_a} v {self.team_b}'


class Player(models.Model):
    icc_id = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=80, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class MatchPlayer(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='squad')
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='appearances')
    team = models.ForeignKey(Team, null=True, on_delete=models.SET_NULL)
    confirmed_xi = models.BooleanField(default=False)
    captain = models.BooleanField(default=False)
    keeper = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['match', 'player'], name='unique_match_player')]


class Innings(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='innings')
    number = models.PositiveSmallIntegerField()
    batting_team = models.ForeignKey(Team, null=True, on_delete=models.SET_NULL, related_name='+')
    bowling_team = models.ForeignKey(Team, null=True, on_delete=models.SET_NULL, related_name='+')
    runs = models.PositiveIntegerField(null=True, blank=True)
    wickets = models.PositiveSmallIntegerField(null=True, blank=True)
    overs = models.CharField(max_length=12, blank=True)  # Cricket overs are NOT base-10 decimals.
    target = models.PositiveIntegerField(null=True, blank=True)
    run_rate = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    extras = models.JSONField(default=dict, blank=True)
    fall_of_wickets = models.JSONField(default=list, blank=True)
    partnerships = models.JSONField(default=list, blank=True)
    powerplays = models.JSONField(default=list, blank=True)
    notes = models.JSONField(default=list, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['match', 'number'], name='unique_match_innings')]
        ordering = ('number',)


class BattingStat(models.Model):
    innings = models.ForeignKey(Innings, on_delete=models.CASCADE, related_name='batting')
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField(default=0)
    runs = models.PositiveIntegerField(null=True, blank=True)
    balls = models.PositiveIntegerField(null=True, blank=True)
    fours = models.PositiveIntegerField(null=True, blank=True)
    sixes = models.PositiveIntegerField(null=True, blank=True)
    strike_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    dismissal = models.CharField(max_length=250, blank=True)
    is_batting = models.BooleanField(default=False)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['innings', 'player'], name='unique_batting_innings_player')]


class BowlingStat(models.Model):
    innings = models.ForeignKey(Innings, on_delete=models.CASCADE, related_name='bowling')
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    overs = models.CharField(max_length=12, blank=True)
    maidens = models.PositiveIntegerField(null=True, blank=True)
    runs = models.PositiveIntegerField(null=True, blank=True)
    wickets = models.PositiveSmallIntegerField(null=True, blank=True)
    economy = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    is_bowling_now = models.BooleanField(default=False)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['innings', 'player'], name='unique_bowling_innings_player')]


class MatchSnapshot(models.Model):
    """Optional lightweight historical score snapshots; not every poll is stored by default."""
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='snapshots')
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=12)
    score = models.JSONField(default=list)
    equation = models.TextField(blank=True)
    fingerprint = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['match', 'fingerprint'], name='unique_match_score_fingerprint')]
        ordering = ('-recorded_at',)
