from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework import status


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 10


class CustomPagination(PageNumberPagination):
    page_size = DEFAULT_PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        # Use the values the paginator actually applied, not the raw query
        # string, which may be non-numeric, out of range or "last".
        paginator = self.page.paginator
        current_page = self.page.number
        current_page_size = paginator.per_page
        total_items = paginator.count
        total_pages = paginator.num_pages if total_items else 0

        return Response({
            "success": True,
            "pagination": {
                "count": total_items,
                "page": current_page,
                "pages": total_pages,
                "page_size": current_page_size,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
            },
            "results": data
        }, status=status.HTTP_200_OK)

    def get_failure_response(self, message, response_status):
        return Response({
            "success": False,
            "message": message,
            "pagination": None,
            "results": []
        }, status=response_status)