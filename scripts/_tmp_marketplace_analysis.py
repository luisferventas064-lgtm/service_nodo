import json
import math
from providers.services_marketplace import search_provider_services

rows = list(
    search_provider_services(
        service_category_id=1,
        province="QC",
        city="Laval",
        limit=100,
        offset=0,
    )
)

# Top 10 JSON payload (same shape as API)
top10 = [
    {
        "provider_id": row.get("provider_id"),
        "price_cents": row.get("price_cents"),
        "safe_rating": row.get("safe_rating"),
        "hybrid_score": row.get("hybrid_score"),
    }
    for row in rows[:10]
]
print("TOP10_JSON=" + json.dumps(top10, ensure_ascii=True))

# Log lines (top 5 + bottom 5)

def log_line(row):
    return (
        "[marketplace_search] "
        f"provider_id= {row.get('provider_id')} "
        f"hybrid_score= {row.get('hybrid_score')} "
        f"cancellation_rate= {row.get('cancellation_rate')} "
        f"safe_completed= {row.get('safe_completed')} "
        f"safe_cancelled= {row.get('safe_cancelled')} "
        f"volume_score= {row.get('volume_score')} "
        f"verified_bonus= {row.get('verified_bonus')}"
    )

by_score_desc = sorted(rows, key=lambda r: r.get("hybrid_score") or 0, reverse=True)
by_score_asc = sorted(rows, key=lambda r: r.get("hybrid_score") or 0)

print("TOP5_LOGS_HIGH=")
for row in by_score_desc[:5]:
    print(log_line(row))

print("TOP5_LOGS_LOW=")
for row in by_score_asc[:5]:
    print(log_line(row))

# Analysis stats
verified_rows = [r for r in rows if (r.get("verified_bonus") or 0) >= 1.0]
non_verified_rows = [r for r in rows if (r.get("verified_bonus") or 0) < 1.0]


def avg(xs):
    return sum(xs) / len(xs) if xs else None


avg_verified = avg([r.get("hybrid_score") or 0 for r in verified_rows])
avg_non_verified = avg([r.get("hybrid_score") or 0 for r in non_verified_rows])

# Cancellation vs score (Pearson)
xs = [r.get("cancellation_rate") or 0 for r in rows]
ys = [r.get("hybrid_score") or 0 for r in rows]

mean_x = avg(xs)
mean_y = avg(ys)
num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
den = math.sqrt(
    sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
)
pearson = num / den if den else None

# Negative scores
negatives = [r for r in rows if (r.get("hybrid_score") or 0) < 0]
min_score = min((r.get("hybrid_score") for r in rows), default=None)

# Deterministic order (3 runs)
list1 = [r.get("provider_id") for r in rows]
list2 = [
    r.get("provider_id")
    for r in search_provider_services(
        service_category_id=1, province="QC", city="Laval", limit=100, offset=0
    )
]
list3 = [
    r.get("provider_id")
    for r in search_provider_services(
        service_category_id=1, province="QC", city="Laval", limit=100, offset=0
    )
]
deterministic = list1 == list2 == list3

# Pagination consistency check (first 2 pages)
page1 = list(
    search_provider_services(
        service_category_id=1, province="QC", city="Laval", limit=20, offset=0
    )
)
page2 = list(
    search_provider_services(
        service_category_id=1, province="QC", city="Laval", limit=20, offset=20
    )
)
combined = page1 + page2
combined_ids = [r.get("provider_id") for r in combined]
unique_ids = len(set(combined_ids))
pagination_consistent = (len(combined_ids) == unique_ids) and (
    combined_ids == [r.get("provider_id") for r in rows[:40]]
)

# Top 5 validations
Top5 = by_score_desc[:5]
any_zero_completed = any((r.get("safe_completed") or 0) == 0 for r in Top5)
any_high_cancel = any((r.get("cancellation_rate") or 0) > 0.5 for r in Top5)

# Non-verified outranking verified with similar metrics (simple heuristic)
similar_pair = None
for nv in Top5:
    if (nv.get("verified_bonus") or 0) >= 1.0:
        continue
    for v in Top5:
        if (v.get("verified_bonus") or 0) < 1.0:
            continue
        if (
            abs((nv.get("safe_rating") or 0) - (v.get("safe_rating") or 0)) <= 0.1
            and abs((nv.get("safe_completed") or 0) - (v.get("safe_completed") or 0))
            <= 10
        ):
            if (nv.get("hybrid_score") or 0) > (v.get("hybrid_score") or 0):
                similar_pair = (nv.get("provider_id"), v.get("provider_id"))
                break
    if similar_pair:
        break

print(
    "STATS="
    + json.dumps(
        {
            "count": len(rows),
            "avg_verified": avg_verified,
            "avg_non_verified": avg_non_verified,
            "pearson_cancel_vs_score": pearson,
            "negatives_count": len(negatives),
            "min_score": min_score,
            "deterministic": deterministic,
            "pagination_consistent": pagination_consistent,
            "top5_any_zero_completed": any_zero_completed,
            "top5_any_cancel_rate_gt_0_5": any_high_cancel,
            "top5_similar_nonverified_beats_verified": similar_pair,
        },
        ensure_ascii=True,
    )
)
