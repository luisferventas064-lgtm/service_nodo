import csv

from django.http import HttpResponse

from core.legal_disclaimers import (
    FINANCIAL_DISCLAIMER_SHORT,
    build_financial_disclaimer_context,
)

from .activity_financial_adapter import build_activity_financial_data_map
from .activity_query import PAGE_SIZE, ActivityQuery
from .dto.activity_row_dto import ActivityRowDTO


def build_activity_view_context(actor_type, actor, *, params=None, selected_status=None, limit=PAGE_SIZE):
    query_params = params
    if query_params is None:
        query_params = {"status": selected_status}
    query = ActivityQuery(
        actor_type,
        actor,
        params=query_params,
        limit=limit,
    )
    return {
        **query.build_context(),
        **build_financial_disclaimer_context(),
    }


def export_activity_csv(actor_type, actor, params):
    query = ActivityQuery(
        actor_type,
        actor,
        params=params,
    )
    jobs = list(query.get_filtered_queryset())
    financials_by_job = build_activity_financial_data_map(jobs, actor_type)
    rows = [
        ActivityRowDTO.from_job(
            job,
            actor_type=actor_type,
            financial=financials_by_job.get(job.job_id),
        )
        for job in jobs
    ]

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="activity_export.csv"'

    writer = csv.writer(response)
    writer.writerow(ActivityRowDTO.get_csv_headers(actor_type))
    for row in rows:
        writer.writerow(row.to_csv_row(actor_type))
    writer.writerow([])
    writer.writerow(["Disclaimer", FINANCIAL_DISCLAIMER_SHORT])

    return response
