"""The automation-effect report: every number derived from confirmed records."""
import statistics
from collections import Counter

import docstore
import profiles
import settings


def build_impact(
    rows: list[dict], manual_minutes: float = settings.MANUAL_BASELINE_MINUTES
) -> dict:
    """Aggregate confirmed records into an automation-impact report.

    Every number is derived from stored records; the only assumption is the
    manual baseline (`manual_minutes`), which is returned to the caller so the
    UI can show it instead of hiding it. Nothing here re-runs extraction, so
    the report never disagrees with the reviewed documents.
    """
    documents = len(rows)
    ids = {row.get('id') for row in rows if row.get('id')}
    duplicates = documents - len(ids)

    edited_rows = [row for row in rows if row.get('edited_fields')]
    edited_fields = sum(len(row.get('edited_fields') or []) for row in rows)
    # The accuracy denominator is the number of fields the document type offers
    # for review, so it comes from the profile and not from a constant here.
    fields_reviewed = sum(profiles.fields_per_document(row.get('doc_type')) for row in rows)

    corrected: Counter[str] = Counter()
    for row in rows:
        for field in row.get('edited_fields') or []:
            corrected[field] += 1
    top_corrections = [
        {'field': field, 'label': settings.FIELD_LABELS.get(field, field), 'count': count}
        for field, count in corrected.most_common()
    ]

    reviews = [
        seconds for row in rows if (seconds := docstore._review_seconds(row)) is not None
    ]
    average_review = sum(reviews) / len(reviews) if reviews else 0.0
    median_review = statistics.median(reviews) if reviews else 0.0
    saved_minutes = sum(max(0.0, manual_minutes - seconds / 60) for seconds in reviews)

    tripped: Counter[str] = Counter()
    documents_with_issues = 0
    unreviewed_risk = 0
    for row in rows:
        issues = docstore._row_issues(row)
        if not issues:
            continue
        documents_with_issues += 1
        for issue in issues:
            tripped[issue] += 1
        if not row.get('edited_fields'):
            unreviewed_risk += 1

    synthetic = sum(1 for row in rows if row.get('source') == 'synthetic')

    stamps = [stamp for row in rows if (stamp := docstore._parse_ts(row.get('confirmed_at')))]
    period = {
        'from': min(stamps).isoformat(timespec='seconds') if stamps else None,
        'to': max(stamps).isoformat(timespec='seconds') if stamps else None,
    }

    return {
        'documents': documents,
        'unique_documents': len(ids),
        'duplicate_confirmations': duplicates,
        'auto_pass_documents': documents - len(edited_rows),
        'auto_pass_rate': round((documents - len(edited_rows)) / documents * 100, 1)
        if documents
        else 0.0,
        'edited_documents': len(edited_rows),
        'edited_fields': edited_fields,
        'fields_reviewed': fields_reviewed,
        'field_accuracy': round(max(0.0, 1 - edited_fields / fields_reviewed) * 100, 1)
        if fields_reviewed
        else 0.0,
        'review': {
            'measured_documents': len(reviews),
            'average_seconds': round(average_review, 1),
            'median_seconds': round(median_review, 1),
        },
        'time_saved_minutes': round(saved_minutes, 1),
        'assumptions': {
            'manual_minutes_per_document': manual_minutes,
            'source': 'DOCFLOW_MANUAL_MINUTES',
        },
        'top_corrections': top_corrections,
        'validation': {
            'documents_with_issues': documents_with_issues,
            'unreviewed_risk_documents': unreviewed_risk,
            'top': [
                {'issue': issue, 'count': count} for issue, count in tripped.most_common()
            ],
        },
        'sources': {
            'synthetic': synthetic,
            'real': documents - synthetic,
            'mixed': 0 < synthetic < documents,
        },
        'period': period,
    }
