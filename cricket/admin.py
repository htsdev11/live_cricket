
from django.contrib import admin
from django.db.models import Q
from django.utils.html import format_html

from .models import (
    Team, Series, Venue, Match, Player,
    MatchPlayer, Innings, BattingStat,
    BowlingStat, MatchSnapshot,
)


# =========================
# TEAMS
# =========================

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("icc_id", "name", "short_name")
    search_fields = ("icc_id", "name", "short_name")
    ordering = ("name",)
    list_per_page = 50


# =========================
# SERIES
# =========================

@admin.register(Series)
class SeriesAdmin(admin.ModelAdmin):
    list_display = (
        "icc_id", "name", "short_name", "tour_name"
    )
    search_fields = (
        "icc_id", "name", "short_name", "tour_name"
    )
    ordering = ("name",)
    list_per_page = 30

    fieldsets = (
        ("Series Information", {
            "fields": (
                "icc_id", "name", "short_name", "tour_name"
            )
        }),
        ("ICC Original Data", {
            "classes": ("collapse",),
            "fields": ("raw",)
        }),
    )


# =========================
# VENUES
# =========================

@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = (
        "icc_id", "name", "city", "country"
    )
    search_fields = (
        "icc_id", "name", "city", "country"
    )
    list_filter = ("country",)
    ordering = ("name",)
    list_per_page = 50


# =========================
# MATCH INNINGS INLINE
# =========================

class InningsInline(admin.TabularInline):
    model = Innings
    extra = 0
    show_change_link = True

    fields = (
        "number",
        "batting_team",
        "bowling_team",
        "runs",
        "wickets",
        "overs",
        "target",
        "run_rate",
    )

    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


# =========================
# MATCHES
# =========================

@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):

    list_display = (
        "icc_id",
        "match_teams",
        "format",
        "status",
        "match_score",
        "series",
        "start_at",
        "last_scorecard_sync",
    )

    list_display_links = (
        "icc_id",
        "match_teams",
    )

    list_filter = (
        "status",
        "format",
        "start_at",
    )

    search_fields = (
        "=icc_id",
        "team_a__name",
        "team_b__name",
        "series__name",
        "venue__name",
    )

    autocomplete_fields = (
        "team_a",
        "team_b",
        "winner",
        "venue",
        "series",
        "toss_won_by",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "last_schedule_sync",
        "last_scorecard_sync",
        "match_link",
    )

    list_select_related = (
        "team_a",
        "team_b",
        "series",
        "venue",
    )

    inlines = (InningsInline,)

    ordering = ("-start_at",)
    date_hierarchy = "start_at"
    list_per_page = 30
    show_full_result_count = False
    save_on_top = True

    fieldsets = (
        ("Match Information", {
            "fields": (
                "icc_id",
                "match_file",
                "match_number",
                "format",
                "status",
                "status_message",
                "start_at",
                "match_link",
            )
        }),

        ("Teams & Tournament", {
            "fields": (
                ("team_a", "team_b"),
                "series",
                "venue",
            )
        }),

        ("Result & Toss", {
            "fields": (
                "winner",
                "result",
                "toss_won_by",
                "toss_decision",
                "equation",
            )
        }),

        ("Synchronization", {
            "fields": (
                "last_schedule_sync",
                "last_scorecard_sync",
                "created_at",
                "updated_at",
            ),
        }),

        ("Original ICC Data", {
            "classes": ("collapse",),
            "fields": (
                "raw_schedule",
                "raw_scorecard",
            ),
        }),
    )

    @admin.display(description="Match")
    def match_teams(self, obj):
        team_a = obj.team_a.short_name if obj.team_a else "TBA"
        team_b = obj.team_b.short_name if obj.team_b else "TBA"

        return f"{team_a} vs {team_b}"

    @admin.display(description="Score")
    def match_score(self, obj):
        innings = list(obj.innings.all())

        if not innings:
            return "Not available"

        scores = []

        for inning in innings:
            team = (
                inning.batting_team.short_name
                if inning.batting_team
                else "TBA"
            )

            runs = inning.runs if inning.runs is not None else "-"
            wickets = inning.wickets if inning.wickets is not None else "-"

            scores.append(
                f"{team}: {runs}/{wickets} ({inning.overs})"
            )

        return " | ".join(scores)

    @admin.display(description="ICC Match Link")
    def match_link(self, obj):
        if not obj.match_url:
            return "-"

        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">'
            'Open ICC Match</a>',
            obj.match_url,
        )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related(
                "innings__batting_team"
            )
        )


# =========================
# PLAYERS
# =========================

@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = (
        "icc_id",
        "name",
        "role",
    )

    search_fields = (
        "icc_id",
        "name",
        "role",
    )

    list_filter = ("role",)
    ordering = ("name",)
    list_per_page = 50


# =========================
# MATCH PLAYERS
# =========================

@admin.register(MatchPlayer)
class MatchPlayerAdmin(admin.ModelAdmin):
    list_display = (
        "match",
        "player",
        "team",
        "confirmed_xi",
        "captain",
        "keeper",
    )

    list_select_related = (
        "match",
        "player",
        "team",
    )

    list_filter = (
        "confirmed_xi",
        "captain",
        "keeper",
    )

    autocomplete_fields = (
        "match",
        "player",
        "team",
    )

    search_fields = (
        "match__icc_id",
        "player__name",
        "team__name",
    )

    list_per_page = 50


# =========================
# INNINGS
# =========================

@admin.register(Innings)
class InningsAdmin(admin.ModelAdmin):
    list_display = (
        "match",
        "number",
        "batting_team",
        "bowling_team",
        "runs",
        "wickets",
        "overs",
        "target",
        "run_rate",
    )

    list_select_related = (
        "match",
        "batting_team",
        "bowling_team",
    )

    autocomplete_fields = (
        "match",
        "batting_team",
        "bowling_team",
    )

    search_fields = (
        "match__icc_id",
        "batting_team__name",
    )

    ordering = ("-match__start_at", "number")
    list_per_page = 50


# =========================
# BATTING STATISTICS
# =========================

@admin.register(BattingStat)
class BattingStatAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "innings",
        "runs",
        "balls",
        "fours",
        "sixes",
        "strike_rate",
        "dismissal",
    )

    list_select_related = (
        "player",
        "innings",
        "innings__match",
    )

    autocomplete_fields = (
        "player",
        "innings",
    )

    search_fields = (
        "player__name",
        "innings__match__icc_id",
    )

    ordering = ("-innings__match__start_at",)
    list_per_page = 50


# =========================
# BOWLING STATISTICS
# =========================

@admin.register(BowlingStat)
class BowlingStatAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "innings",
        "overs",
        "maidens",
        "runs",
        "wickets",
        "economy",
        "is_bowling_now",
    )

    list_select_related = (
        "player",
        "innings",
        "innings__match",
    )

    autocomplete_fields = (
        "player",
        "innings",
    )

    search_fields = (
        "player__name",
        "innings__match__icc_id",
    )

    list_filter = ("is_bowling_now",)
    ordering = ("-innings__match__start_at",)
    list_per_page = 50


# =========================
# MATCH SNAPSHOTS
# =========================

@admin.register(MatchSnapshot)
class MatchSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "match",
        "status",
        "recorded_at",
        "fingerprint",
    )

    list_select_related = ("match",)

    search_fields = (
        "match__icc_id",
        "fingerprint",
    )

    list_filter = (
        "status",
        "recorded_at",
    )

    readonly_fields = (
        "match",
        "status",
        "recorded_at",
        "fingerprint",
        "score",
        "equation",
    )

    ordering = ("-recorded_at",)
    list_per_page = 50
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# =========================
# ADMIN BRANDING
# =========================

admin.site.site_header = "ICC Cricket Administration"
admin.site.site_title = "ICC Cricket Admin"
admin.site.index_title = "Cricket Data Management"
