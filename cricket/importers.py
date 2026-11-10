"""Persist ICC schedule + full scorecards without creating duplicate matches/stats."""
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from django.db import connection, models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import (Team, Series, Venue, Match, Player, MatchPlayer, Innings,
                     BattingStat, BowlingStat, MatchSnapshot)

# IDs ICC uses to mean "no team / not decided yet".
EMPTY_IDS = ('', '0')


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------
def string(value):
    return '' if value is None else str(value)


def has_id(value):
    return string(value).strip() not in EMPTY_IDS


def decimal(value):
    if value is None or value == '':
        return None
    try:
        result = Decimal(str(value).strip())
    except (ValueError, TypeError, InvalidOperation):
        return None
    return result if result.is_finite() else None


def number(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        pass
    result = decimal(value)  # accept "12.0"
    if result is not None and result == result.to_integral_value():
        return int(result)
    return None


def boolean(value):
    return str(value).strip().lower() in ('true', '1', 'yes')


def datetime_value(value):
    if not value:
        return None
    text = str(value)
    try:
        dt = parse_datetime(text)
    except ValueError:  # well formatted but impossible, e.g. Feb 30
        return None
    if dt is None:
        for fmt in ('%m/%d/%YT%H:%M:%S%z', '%m/%d/%YT%H:%M%z'):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                pass
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


# ---------------------------------------------------------------------------
# Sanitising values so one odd field never aborts a whole match import
# ---------------------------------------------------------------------------
def strip_nul(value):
    """PostgreSQL text and jsonb columns reject NUL (0x00) characters."""
    if isinstance(value, str):
        return value.replace('\x00', '')
    if isinstance(value, dict):
        return {strip_nul(k): strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    return value


def _fit_decimal(value, field):
    try:
        value = Decimal(value).quantize(Decimal(1).scaleb(-field.decimal_places))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not value.is_finite() or abs(value) >= Decimal(10) ** (field.max_digits - field.decimal_places):
        return None
    return value


def clean(model, data):
    """Fit values to the model's column limits (length, integer range, decimal size)."""
    out = {}
    for key, value in data.items():
        field = model._meta.get_field(key)
        if isinstance(value, str):
            value = value.replace('\x00', '')
            limit = getattr(field, 'max_length', None)
            if limit and len(value) > limit:
                # A truncated URL is a broken link, so drop it instead.
                value = '' if isinstance(field, models.URLField) else value[:limit]
        elif isinstance(field, models.DecimalField) and value is not None:
            value = _fit_decimal(value, field)
        elif isinstance(field, models.IntegerField) and isinstance(value, int):
            low, high = connection.ops.integer_field_range(field.get_internal_type())
            if (low is not None and value < low) or (high is not None and value > high):
                value = None
        if value is None and not field.null and field.has_default():
            value = field.get_default()
        out[key] = value
    return out


def update(instance, fields):
    for key, value in clean(type(instance), fields).items():
        setattr(instance, key, value)
    instance.save()
    return instance


def upsert(model, defaults, **lookup):
    obj, _ = model.objects.update_or_create(**lookup, defaults=clean(model, defaults))
    return obj


# ---------------------------------------------------------------------------
# Reference objects
# ---------------------------------------------------------------------------
def team(team_id, name='', short='', url=''):
    if not has_id(team_id):
        return None
    key = string(team_id).strip()
    obj, created = Team.objects.get_or_create(
        icc_id=key, defaults=clean(Team, {'name': name or key, 'short_name': short, 'url': url}))
    if not created:
        data = {}
        if name and name != key:
            data['name'] = name
        if short:
            data['short_name'] = short
        if url:
            data['url'] = url
        if data:
            update(obj, data)
    return obj


def series_obj(series_id, name='', **kwargs):
    if not has_id(series_id):
        return None
    key = string(series_id).strip()
    return upsert(Series, {'name': name or key, **kwargs}, icc_id=key)


def venue_obj(venue_id, name='', **kwargs):
    if not has_id(venue_id):
        return None
    key = string(venue_id).strip()
    return upsert(Venue, {'name': name or key, **kwargs}, icc_id=key)


def player_obj(player_id, payload=None):
    if not has_id(player_id):
        return None
    payload = payload if isinstance(payload, dict) else {}
    pid = string(player_id).strip()
    obj, _ = Player.objects.get_or_create(
        icc_id=pid, defaults=clean(Player, {'name': payload.get('Name_Full') or pid}))
    if payload:
        update(obj, {'name': payload.get('Name_Full') or obj.name,
                     'role': string(payload.get('Role') or payload.get('Skill_Name')),
                     'raw': payload})
    return obj


def status_from(row):
    if boolean(row.get('live')):
        return Match.Status.LIVE
    if boolean(row.get('upcoming')):
        return Match.Status.UPCOMING
    if boolean(row.get('recent')) or boolean(row.get('is_match_ended')):
        return Match.Status.COMPLETED
    label = string(row.get('match_display_status') or row.get('match_status')).lower()
    if label in ('live', 'upcoming'):
        return label
    if label in ('result', 'completed', 'finished', 'recent'):
        return Match.Status.COMPLETED
    return Match.Status.OTHER


def innings_index(label, index):
    labels = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4}
    key = string(label).strip().lower()
    if key in labels:
        return labels[key]
    value = number(label)
    return value if value and value > 0 else index


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
@transaction.atomic
def import_schedule(row):
    """Upsert schedule. Blank or unknown values never overwrite data already stored
    (e.g. a result or status written by an earlier scorecard import)."""
    if not isinstance(row, dict):
        raise ValueError('Schedule record must be an object')
    row = strip_nul(row)
    match_id = string(row.get('match_id')).strip()
    if not match_id:
        raise ValueError('Schedule record missing match_id')
    a = team(row.get('teama_id'), row.get('teama_display_name') or row.get('teama') or '',
             row.get('teama_short') or '', row.get('teama_url') or '')
    b = team(row.get('teamb_id'), row.get('teamb_display_name') or row.get('teamb') or '',
             row.get('teamb_short') or '', row.get('teamb_url') or '')
    series = series_obj(row.get('series_id'), row.get('series_name') or '',
                        short_name=row.get('series_short_display_name') or '',
                        tour_name=row.get('tour_name') or '')
    venue = venue_obj(row.get('venue_id'), row.get('venue') or '')
    other = row.get('other_info') if isinstance(row.get('other_info'), dict) else {}
    match, _ = Match.objects.get_or_create(icc_id=match_id)
    status = status_from(row)
    update(match, {
        'match_file': string(row.get('match_file')) or match.match_file,
        'match_number': string(row.get('match_number')) or match.match_number,
        'format': string(row.get('match_type')) or match.format,
        'status': match.status if status == Match.Status.OTHER else status,
        'status_message': string(row.get('current_status_message') or row.get('match_status'))
                          or match.status_message,
        'start_at': datetime_value(row.get('start_date')) or match.start_at,
        'series': series or match.series,
        'venue': venue or match.venue,
        'team_a': a or match.team_a,
        'team_b': b or match.team_b,
        'winner': team(row.get('winning_team_id')) or match.winner,
        'result': string(row.get('match_result')) or match.result,
        'toss_won_by': team(row.get('toss_won_by')) or match.toss_won_by,
        'toss_decision': string(row.get('toss_elected_to')) or match.toss_decision,
        'match_url': string(other.get('match_center')) or match.match_url,
        'raw_schedule': row,
        'last_schedule_sync': timezone.now(),
    })
    return match


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------
def _dicts(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


@transaction.atomic
def import_scorecard(payload, match_id=None, keep_snapshot=False):
    if not isinstance(payload, dict):
        raise ValueError('Scorecard payload must be an object')
    payload = strip_nul(payload)
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    detail = data.get('Matchdetail') or {}
    info = detail.get('Match') or {}
    match_id = string(info.get('Id') or match_id).strip()
    if not match_id:
        raise ValueError('Scorecard missing match ID')
    match, _ = Match.objects.get_or_create(icc_id=match_id)

    teams = {}
    team_entries = data.get('Teams') if isinstance(data.get('Teams'), dict) else {}
    seen_players = set()
    for tid, entry in team_entries.items():
        entry = entry if isinstance(entry, dict) else {}
        teams[string(tid)] = team(tid, entry.get('Name_Full') or '', entry.get('Name_Short') or '',
                                  entry.get('Team_Url') or '')
        players = entry.get('Players') if isinstance(entry.get('Players'), dict) else {}
        for pid, pdata in players.items():
            pdata = pdata if isinstance(pdata, dict) else {}
            player = player_obj(pid, pdata)
            if not player:
                continue
            seen_players.add(player.pk)
            upsert(MatchPlayer, {'team': teams[string(tid)],
                                 'confirmed_xi': boolean(pdata.get('Confirm_XI')),
                                 'captain': boolean(pdata.get('Iscaptain')),
                                 'keeper': boolean(pdata.get('Iskeeper'))},
                   match=match, player=player)
    if team_entries:
        # Players ICC no longer lists for this match (e.g. dropped from the XI).
        match.squad.exclude(player_id__in=seen_players).delete()

    ser = detail.get('Series') or {}
    ven = detail.get('Venue') or {}
    original_status = match.status
    status = status_from({
        'live': info.get('Live'), 'recent': info.get('Recent'),
        'upcoming': info.get('Upcoming'),
        'is_match_ended': info.get('is_match_ended'),
        'match_display_status': detail.get('Match_display_status'),
    })
    update(match, {
        'match_file': string(info.get('Code') or match.match_file),
        'match_number': string(info.get('Number') or match.match_number),
        'format': string(info.get('Type') or match.format),
        'status': status if status != Match.Status.OTHER else original_status,
        'status_message': string(detail.get('Current_status_message') or detail.get('Status')),
        'start_at': datetime_value(info.get('StartDate')) or match.start_at,
        'series': series_obj(ser.get('Id'), ser.get('Name') or '',
                             short_name=ser.get('Series_short_display_name') or '',
                             tour_name=ser.get('Tour_Name') or '', raw=ser) or match.series,
        'venue': venue_obj(ven.get('Id'), ven.get('Name') or '',
                           city=ven.get('City') or '', country=ven.get('Country') or '', raw=ven) or match.venue,
        'team_a': teams.get(string(detail.get('Team_Home'))) or match.team_a,
        'team_b': teams.get(string(detail.get('Team_Away'))) or match.team_b,
        'result': string(detail.get('Status') if status == Match.Status.COMPLETED else match.result),
        'toss_won_by': teams.get(string(detail.get('Tosswonby'))) or match.toss_won_by,
        'toss_decision': string(detail.get('Toss_elected_to') or match.toss_decision),
        'equation': string(detail.get('Equation') or (data.get('Matchequation') or {}).get('Equation')),
        'match_url': string((data.get('other_info') or {}).get('match_center') or match.match_url),
        'raw_scorecard': payload,
        'last_scorecard_sync': timezone.now(),
    })

    notes = data.get('Notes') if isinstance(data.get('Notes'), dict) else {}
    innings_rows = _dicts(data.get('Innings'))
    used_numbers = set()
    for index, row in enumerate(innings_rows, 1):
        no = innings_index(row.get('Number'), index)
        while no in used_numbers:  # two innings with the same label must not merge
            no += 1
        used_numbers.add(no)
        inn = upsert(Innings, {
            'batting_team': teams.get(string(row.get('Battingteam'))),
            'bowling_team': teams.get(string(row.get('Bowlingteam'))),
            'runs': number(row.get('Total')), 'wickets': number(row.get('Wickets')),
            'overs': string(row.get('Overs')), 'target': number(row.get('Target')),
            'run_rate': decimal(row.get('Runrate')),
            'extras': {key: row.get(key) for key in ('Byes', 'Legbyes', 'Wides', 'Noballs', 'Penalty')},
            'fall_of_wickets': row.get('FallofWickets') or [],
            'partnerships': row.get('Partnerships') or [],
            'powerplays': row.get('PowerPlayDetails') or [],
            'notes': notes.get(str(no), []),
            'raw': row,
        }, match=match, number=no)

        if isinstance(row.get('Batsmen'), list):
            seen = set()
            for b in _dicts(row['Batsmen']):
                p = player_obj(b.get('Batsman'))
                if p:
                    seen.add(p.pk)
                    upsert(BattingStat, {
                        'position': number(b.get('Number')) or 0,
                        'runs': number(b.get('Runs')), 'balls': number(b.get('Balls')),
                        'fours': number(b.get('Fours')), 'sixes': number(b.get('Sixes')),
                        'strike_rate': decimal(b.get('Strikerate')),
                        'dismissal': string(b.get('Howout')),
                        'is_batting': boolean(b.get('Isbatting')), 'raw': b,
                    }, innings=inn, player=p)
            inn.batting.exclude(player_id__in=seen).delete()

        if isinstance(row.get('Bowlers'), list):
            seen = set()
            for b in _dicts(row['Bowlers']):
                p = player_obj(b.get('Bowler'))
                if p:
                    seen.add(p.pk)
                    upsert(BowlingStat, {
                        'overs': string(b.get('Overs')), 'maidens': number(b.get('Maidens')),
                        'runs': number(b.get('Runs')), 'wickets': number(b.get('Wickets')),
                        'economy': decimal(b.get('Economyrate')),
                        'is_bowling_now': boolean(b.get('Isbowlingnow')), 'raw': b,
                    }, innings=inn, player=p)
            inn.bowling.exclude(player_id__in=seen).delete()

    if innings_rows:
        # Innings ICC no longer reports (e.g. renumbered or removed).
        match.innings.exclude(number__in=used_numbers).delete()

    if keep_snapshot:
        score = [{'number': i.number, 'runs': i.runs, 'wickets': i.wickets,
                  'overs': i.overs} for i in match.innings.all()]
        fingerprint = hashlib.sha256(json.dumps([match.status, score, match.equation],
                      sort_keys=True).encode()).hexdigest()
        MatchSnapshot.objects.get_or_create(
            match=match, fingerprint=fingerprint,
            defaults=clean(MatchSnapshot, {'status': match.status, 'score': score,
                                           'equation': match.equation}))
    return match
