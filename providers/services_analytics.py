from __future__ import annotations

import csv
import io
from statistics import pstdev

from providers.models import Provider
from providers.services_marketplace import marketplace_ranked_queryset


def _round(value, digits=2):
    if value is None:
        return None
    return round(float(value), digits)


def _percentage(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def _load_marketplace_rows():
    offer_rows = list(
        marketplace_ranked_queryset().values(
            "id",
            "provider_id",
            "category_id",
            "service_category_name",
            "price_cents",
            "hybrid_score",
            "provider__province",
            "provider__city",
            "provider__zone_id",
            "zone_name",
        )
    )

    provider_ids = sorted({row["provider_id"] for row in offer_rows})
    provider_rows = list(
        Provider.objects.filter(provider_id__in=provider_ids).values(
            "provider_id",
            "province",
            "city",
            "zone_id",
            "zone__name",
            "avg_rating",
            "is_verified",
        )
    )

    return offer_rows, provider_rows


def _mean(values, digits=2):
    if not values:
        return None
    return _round(sum(values) / len(values), digits)


def compute_competitiveness_index(spread, std_dev, max_spread, max_std):
    if spread is None or std_dev is None:
        return None

    normalized_components = []

    if max_spread and max_spread > 0:
        normalized_components.append(max(0.0, 1 - (spread / max_spread)))
    else:
        normalized_components.append(1.0)

    if max_std and max_std > 0:
        normalized_components.append(max(0.0, 1 - (std_dev / max_std)))
    else:
        normalized_components.append(1.0)

    return _round(sum(normalized_components) / len(normalized_components), 4)


def _grouped_slice_metrics(
    *,
    provider_rows,
    offer_rows,
    field_map,
    limit: int | None = None,
    provider_filter=None,
    offer_filter=None,
):
    provider_stats = {}
    for row in provider_rows:
        if provider_filter and not provider_filter(row):
            continue
        key = tuple(row[provider_field] for _, provider_field, _ in field_map)
        entry = provider_stats.setdefault(
            key,
            {
                "providers": 0,
                "verified_providers": 0,
                "rating_values": [],
            },
        )
        entry["providers"] += 1
        if row["is_verified"]:
            entry["verified_providers"] += 1
        if row["avg_rating"] is not None:
            entry["rating_values"].append(float(row["avg_rating"]))

    offer_stats = {}
    for row in offer_rows:
        if offer_filter and not offer_filter(row):
            continue
        key = tuple(row[offer_field] for _, _, offer_field in field_map)
        entry = offer_stats.setdefault(
            key,
            {
                "offers": 0,
                "price_values": [],
                "score_values": [],
            },
        )
        entry["offers"] += 1
        if row["price_cents"] is not None:
            entry["price_values"].append(int(row["price_cents"]))
        if row["hybrid_score"] is not None:
            entry["score_values"].append(float(row["hybrid_score"]))

    merged_rows = []
    for key, stats in provider_stats.items():
        offer_entry = offer_stats.get(
            key,
            {"offers": 0, "price_values": [], "score_values": []},
        )
        output_row = {
            output_name: key[index]
            for index, (output_name, _, _) in enumerate(field_map)
        }
        output_row.update(
            {
                "providers": stats["providers"],
                "verified_providers": stats["verified_providers"],
                "verified_pct": _percentage(
                    stats["verified_providers"],
                    stats["providers"],
                ),
                "avg_rating": _mean(stats["rating_values"], digits=2),
                "offers": offer_entry["offers"],
                "avg_price_cents": int(round(sum(offer_entry["price_values"]) / len(offer_entry["price_values"])))
                if offer_entry["price_values"]
                else None,
                "avg_price": _mean(
                    [value / 100 for value in offer_entry["price_values"]],
                    digits=2,
                ),
                "avg_hybrid_score": _mean(offer_entry["score_values"], digits=4),
                "score_std_dev": _round(pstdev(offer_entry["score_values"]), 4)
                if len(offer_entry["score_values"]) > 1
                else 0.0,
                "score_spread": _round(
                    max(offer_entry["score_values"]) - min(offer_entry["score_values"]),
                    4,
                )
                if offer_entry["score_values"]
                else None,
            }
        )
        merged_rows.append(output_row)

    merged_rows.sort(
        key=lambda row: (
            -row["providers"],
            *(str(row.get(output_name) or "") for output_name, _, _ in field_map),
        )
    )

    if limit is not None:
        return merged_rows[:limit]
    return merged_rows


def marketplace_global_kpis(*, offer_rows=None, provider_rows=None):
    if offer_rows is None or provider_rows is None:
        offer_rows, provider_rows = _load_marketplace_rows()

    score_values = [float(row["hybrid_score"]) for row in offer_rows if row["hybrid_score"] is not None]
    price_values = [int(row["price_cents"]) for row in offer_rows if row["price_cents"] is not None]
    rating_values = [float(row["avg_rating"]) for row in provider_rows if row["avg_rating"] is not None]
    verified_total = sum(1 for row in provider_rows if row["is_verified"])

    return {
        "total_providers": len(provider_rows),
        "verified_providers": verified_total,
        "verified_pct": _percentage(verified_total, len(provider_rows)),
        "avg_rating": _mean(rating_values, digits=2),
        "avg_price_cents": int(round(sum(price_values) / len(price_values))) if price_values else None,
        "avg_price": _mean([value / 100 for value in price_values], digits=2),
        "avg_hybrid_score": _mean(score_values, digits=4),
        "score_std_dev": _round(pstdev(score_values), 4) if len(score_values) > 1 else 0.0,
        "total_offers": len(offer_rows),
        "score_spread": _round(max(score_values) - min(score_values), 4) if score_values else None,
    }


def marketplace_kpis_by_slice(level: str, limit: int | None = None, *, offer_rows=None, provider_rows=None):
    if offer_rows is None or provider_rows is None:
        offer_rows, provider_rows = _load_marketplace_rows()

    if level == "province":
        return _grouped_slice_metrics(
            provider_rows=provider_rows,
            offer_rows=offer_rows,
            field_map=(("province", "province", "provider__province"),),
            limit=limit,
        )

    if level == "city":
        return _grouped_slice_metrics(
            provider_rows=provider_rows,
            offer_rows=offer_rows,
            field_map=(
                ("province", "province", "provider__province"),
                ("city", "city", "provider__city"),
            ),
            limit=limit,
        )

    raise ValueError(f"Unsupported slice level: {level}")


def provider_distribution_by_zone(limit: int | None = None, *, offer_rows=None, provider_rows=None):
    if offer_rows is None or provider_rows is None:
        offer_rows, provider_rows = _load_marketplace_rows()

    return _grouped_slice_metrics(
        provider_rows=provider_rows,
        offer_rows=offer_rows,
        field_map=(
            ("province", "province", "provider__province"),
            ("city", "city", "provider__city"),
            ("zone_id", "zone_id", "provider__zone_id"),
            ("zone_name", "zone__name", "zone_name"),
        ),
        provider_filter=lambda row: row["zone_id"] is not None,
        offer_filter=lambda row: row["provider__zone_id"] is not None,
        limit=limit,
    )


def hybrid_score_spread(
    *,
    province: str | None = None,
    city: str | None = None,
    category_id: int | None = None,
    limit: int | None = None,
    offer_rows=None,
):
    if offer_rows is None:
        offer_rows, _ = _load_marketplace_rows()

    filtered_rows = []
    for row in offer_rows:
        if province and row["provider__province"] != province:
            continue
        if city and row["provider__city"] != city:
            continue
        if category_id is not None and row["category_id"] != category_id:
            continue
        filtered_rows.append(row)

    global_scores = [float(row["hybrid_score"]) for row in filtered_rows if row["hybrid_score"] is not None]
    global_summary = {
        "offers": len(filtered_rows),
        "avg_hybrid_score": _mean(global_scores, digits=4),
        "score_std_dev": _round(pstdev(global_scores), 4) if len(global_scores) > 1 else 0.0,
        "min_hybrid_score": _round(min(global_scores), 4) if global_scores else None,
        "max_hybrid_score": _round(max(global_scores), 4) if global_scores else None,
        "score_spread": _round(max(global_scores) - min(global_scores), 4) if global_scores else None,
    }

    slice_stats = {}
    for row in filtered_rows:
        key = (
            row["provider__province"],
            row["provider__city"],
            row["category_id"],
            row["service_category_name"],
        )
        entry = slice_stats.setdefault(
            key,
            {
                "provider_ids": set(),
                "offers": 0,
                "score_values": [],
            },
        )
        entry["provider_ids"].add(row["provider_id"])
        entry["offers"] += 1
        if row["hybrid_score"] is not None:
            entry["score_values"].append(float(row["hybrid_score"]))

    by_slice = []
    for key, entry in slice_stats.items():
        score_values = entry["score_values"]
        by_slice.append(
            {
                "provider__province": key[0],
                "provider__city": key[1],
                "category_id": key[2],
                "service_category_name": key[3],
                "providers": len(entry["provider_ids"]),
                "offers": entry["offers"],
                "avg_hybrid_score": _mean(score_values, digits=4),
                "score_std_dev": _round(pstdev(score_values), 4) if len(score_values) > 1 else 0.0,
                "min_hybrid_score": _round(min(score_values), 4) if score_values else None,
                "max_hybrid_score": _round(max(score_values), 4) if score_values else None,
                "score_spread": _round(max(score_values) - min(score_values), 4) if score_values else None,
            }
        )

    max_spread = max(
        (row["score_spread"] for row in by_slice if row["score_spread"] is not None),
        default=0.0,
    )
    max_std_dev = max(
        (row["score_std_dev"] for row in by_slice if row["score_std_dev"] is not None),
        default=0.0,
    )
    for row in by_slice:
        row["competitiveness_index"] = compute_competitiveness_index(
            row["score_spread"],
            row["score_std_dev"],
            max_spread,
            max_std_dev,
        )

    by_slice.sort(
        key=lambda row: (
            row["score_spread"] if row["score_spread"] is not None else 999999,
            -row["providers"],
            row["provider__province"],
            row["provider__city"],
            row["category_id"],
        )
    )

    if limit is not None:
        by_slice = by_slice[:limit]

    return {
        "global": global_summary,
        "by_slice": by_slice,
    }


def marketplace_analytics_snapshot(limit: int | None = None):
    offer_rows, provider_rows = _load_marketplace_rows()
    return {
        "global": marketplace_global_kpis(offer_rows=offer_rows, provider_rows=provider_rows),
        "by_province": marketplace_kpis_by_slice(
            "province",
            limit=limit,
            offer_rows=offer_rows,
            provider_rows=provider_rows,
        ),
        "by_city": marketplace_kpis_by_slice(
            "city",
            limit=limit,
            offer_rows=offer_rows,
            provider_rows=provider_rows,
        ),
        "by_zone": provider_distribution_by_zone(
            limit=limit,
            offer_rows=offer_rows,
            provider_rows=provider_rows,
        ),
        "score_spread": hybrid_score_spread(
            limit=limit,
            offer_rows=offer_rows,
        ),
    }


def marketplace_analytics_to_csv(snapshot: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    global_metrics = snapshot.get("global", {})
    writer.writerow(["Global"])
    writer.writerow(["metric", "value"])
    for metric, value in global_metrics.items():
        writer.writerow([metric, value])
    writer.writerow([])

    writer.writerow(["By Province"])
    writer.writerow(
        [
            "province",
            "providers",
            "verified_pct",
            "avg_rating",
            "avg_price",
            "avg_score",
            "std_dev",
            "spread",
        ]
    )
    for row in snapshot.get("by_province", []):
        writer.writerow(
            [
                row.get("province"),
                row.get("providers"),
                row.get("verified_pct"),
                row.get("avg_rating"),
                row.get("avg_price"),
                row.get("avg_hybrid_score"),
                row.get("score_std_dev"),
                row.get("score_spread"),
            ]
        )
    writer.writerow([])

    writer.writerow(["By City"])
    writer.writerow(
        [
            "province",
            "city",
            "providers",
            "verified_pct",
            "avg_rating",
            "avg_price",
            "avg_score",
            "std_dev",
            "spread",
        ]
    )
    for row in snapshot.get("by_city", []):
        writer.writerow(
            [
                row.get("province"),
                row.get("city"),
                row.get("providers"),
                row.get("verified_pct"),
                row.get("avg_rating"),
                row.get("avg_price"),
                row.get("avg_hybrid_score"),
                row.get("score_std_dev"),
                row.get("score_spread"),
            ]
        )
    writer.writerow([])

    writer.writerow(["By Zone"])
    writer.writerow(
        [
            "province",
            "city",
            "zone",
            "providers",
            "verified_pct",
            "avg_rating",
            "avg_price",
            "avg_score",
            "std_dev",
        ]
    )
    for row in snapshot.get("by_zone", []):
        writer.writerow(
            [
                row.get("province"),
                row.get("city"),
                row.get("zone_name"),
                row.get("providers"),
                row.get("verified_pct"),
                row.get("avg_rating"),
                row.get("avg_price"),
                row.get("avg_hybrid_score"),
                row.get("score_std_dev"),
            ]
        )
    writer.writerow([])

    writer.writerow(["Score Spread"])
    writer.writerow(["slice", "max_score", "min_score", "std_dev", "spread", "competitiveness_index"])
    for row in snapshot.get("score_spread", {}).get("by_slice", []):
        slice_label = (
            f'{row.get("provider__province")}'
            f'-{row.get("provider__city")}'
            f'-cat{row.get("category_id")}'
        )
        writer.writerow(
            [
                slice_label,
                row.get("max_hybrid_score"),
                row.get("min_hybrid_score"),
                row.get("score_std_dev"),
                row.get("score_spread"),
                row.get("competitiveness_index"),
            ]
        )

    return buffer.getvalue()
