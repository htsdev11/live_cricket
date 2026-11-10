from django.urls import path
from .views import (
    MatchListAPIView,
    MatchLiveAPIView,
    MatchUpcomingAPIView,
    MatchCompletedAPIView,
    MatchDetailAPIView,
    MatchSnapshotsAPIView,
)

urlpatterns = [
    path(
        "matches/",
        MatchListAPIView.as_view(),
        name="match-list",
    ),
    path(
        "matches/live/",
        MatchLiveAPIView.as_view(),
        name="match-live",
    ),
    path(
        "matches/upcoming/",
        MatchUpcomingAPIView.as_view(),
        name="match-upcoming",
    ),
    path(
        "matches/completed/",
        MatchCompletedAPIView.as_view(),
        name="match-completed",
    ),
    path(
        "matches/<str:icc_id>/snapshots/",
        MatchSnapshotsAPIView.as_view(),
        name="match-snapshots",
    ),
    path(
        "matches/<str:icc_id>/",
        MatchDetailAPIView.as_view(),
        name="match-detail",
    ),
]
