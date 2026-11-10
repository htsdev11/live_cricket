
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .models import Match
from .serializers import (
    MatchListSerializer,
    MatchDetailSerializer,
    SnapshotSerializer,
)


class MatchPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class BaseMatchAPIView(APIView):
    """
    Shared authentication, queryset, and filters.

    Authentication uses REST_FRAMEWORK's defaults (token or logged-in session).
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Match.objects.select_related(
            "team_a",
            "team_b",
            "winner",
            "series",
            "venue",
            "toss_won_by",
        ).order_by("-start_at", "-pk")

    def filter_matches(self, request, queryset):
        params = request.query_params

        # Note: ?format= only reaches this filter because settings set
        # REST_FRAMEWORK["URL_FORMAT_OVERRIDE"] = None.
        status = params.get("status", "").strip().lower()
        if status:
            if status not in Match.Status.values:
                raise ValidationError({
                    "status": "Use upcoming, live, completed, or other."
                })
            queryset = queryset.filter(status=status)

        fmt = params.get("format")
        if fmt:
            queryset = queryset.filter(format__iexact=fmt)

        team = params.get("team")
        if team:
            queryset = queryset.filter(
                Q(team_a__name__icontains=team)
                | Q(team_b__name__icontains=team)
                | Q(team_a__short_name__iexact=team)
                | Q(team_b__short_name__iexact=team)
            )

        series = params.get("series")
        if series:
            queryset = queryset.filter(
                series__name__icontains=series
            )

        for field, lookup in (
            ("from_date", "start_at__date__gte"),
            ("to_date", "start_at__date__lte"),
        ):
            value = params.get(field)

            if value:
                try:
                    parsed = parse_date(value)
                except ValueError:  # well formatted but impossible, e.g. 2026-02-30
                    parsed = None

                if not parsed:
                    raise ValidationError({
                        field: "Use YYYY-MM-DD."
                    })

                queryset = queryset.filter(**{
                    lookup: parsed
                })

        query = params.get("q")
        if query:
            queryset = queryset.filter(
                Q(team_a__name__icontains=query)
                | Q(team_b__name__icontains=query)
                | Q(series__name__icontains=query)
                | Q(venue__name__icontains=query)
            )

        return queryset

    def paginate(self, request, queryset, serializer_class):
        paginator = MatchPagination()
        page = paginator.paginate_queryset(
            queryset,
            request,
            view=self,
        )

        if page is not None:
            serializer = serializer_class(
                page,
                many=True,
            )
            return paginator.get_paginated_response(
                serializer.data
            )

        return Response(
            serializer_class(queryset, many=True).data
        )


class MatchListAPIView(BaseMatchAPIView):
    """
    GET /api/matches/
    """

    def get(self, request):
        queryset = self.filter_matches(
            request,
            self.get_queryset(),
        )

        queryset = queryset.order_by("-start_at", "-pk")

        return self.paginate(
            request,
            queryset,
            MatchListSerializer,
        )


class MatchLiveAPIView(BaseMatchAPIView):
    """
    GET /api/matches/live/
    """

    def get(self, request):
        queryset = self.filter_matches(
            request,
            self.get_queryset().filter(
                status="live"
            ),
        ).filter(status="live")

        return self.paginate(
            request,
            queryset,
            MatchListSerializer,
        )


class MatchUpcomingAPIView(BaseMatchAPIView):
    """
    GET /api/matches/upcoming/
    """

    def get(self, request):
        queryset = self.filter_matches(
            request,
            self.get_queryset().filter(
                status="upcoming"
            ),
        ).filter(status="upcoming")

        queryset = queryset.order_by("start_at", "pk")

        return self.paginate(
            request,
            queryset,
            MatchListSerializer,
        )


class MatchCompletedAPIView(BaseMatchAPIView):
    """
    GET /api/matches/completed/
    """

    def get(self, request):
        queryset = self.filter_matches(
            request,
            self.get_queryset().filter(
                status="completed"
            ),
        ).filter(status="completed")

        return self.paginate(
            request,
            queryset,
            MatchListSerializer,
        )


class MatchDetailAPIView(BaseMatchAPIView):
    """
    GET /api/matches/<icc_id>/
    """

    def get(self, request, icc_id):
        queryset = self.get_queryset().prefetch_related(
            "innings__batting__player",
            "innings__bowling__player",
            "innings__batting_team",
            "innings__bowling_team",
        )

        match = get_object_or_404(
            queryset,
            icc_id=icc_id,
        )

        serializer = MatchDetailSerializer(match)

        return Response(serializer.data)


class MatchSnapshotsAPIView(BaseMatchAPIView):
    """
    GET /api/matches/<icc_id>/snapshots/
    """

    def get(self, request, icc_id):
        match = get_object_or_404(
            self.get_queryset(),
            icc_id=icc_id,
        )

        snapshots = match.snapshots.order_by(
            "-recorded_at"
        )[:100]

        serializer = SnapshotSerializer(
            snapshots,
            many=True,
        )

        return Response(serializer.data)
