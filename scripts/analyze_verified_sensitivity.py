from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from statistics import mean

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.db.models import Count
from providers.models import ProviderService
from providers.services_marketplace import search_provider_services

RATING_WEIGHT = 0.5
VOLUME_WEIGHT = 0.3
VERIFIED_WEIGHT = 0.1
CANCELLATION_WEIGHT = 0.2


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare marketplace ranking with and without verified bonus.",
    )
    parser.add_argument(
        "--service-category-id",
        type=int,
        help="Service category to analyze.",
    )
    parser.add_argument(
        "--province",
        help="Province filter used by the marketplace search.",
    )
    parser.add_argument(
        "--city",
        help="Optional city filter used by the marketplace search.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of ranked rows to analyze (default: 100).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Offset passed to the marketplace search (default: 0).",
    )
    parser.add_argument(
        "--show-moves",
        type=int,
        default=15,
        help="How many rank changes to print (default: 15).",
    )
    parser.add_argument(
        "--auto-discover",
        action="store_true",
        help="Discover available slices and print an aggregated summary table.",
    )
    parser.add_argument(
        "--discovery-scope",
        choices=["province", "city", "both"],
        default="both",
        help="Slice families to auto-discover (default: both).",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=5,
        help="Minimum candidate volume for auto-discovered slices (default: 5).",
    )
    parser.add_argument(
        "--max-slices",
        type=int,
        default=20,
        help="Maximum slices to analyze in auto-discovery mode (default: 20).",
    )
    return parser.parse_args()


def _f(value, default=0.0):
    if value is None:
        return default
    return float(value)


def _i(value, default=0):
    if value is None:
        return default
    return int(value)


def compute_base_score(row):
    return (
        (_f(row.get("safe_rating")) * RATING_WEIGHT)
        + (_f(row.get("volume_score")) * VOLUME_WEIGHT)
        - (_f(row.get("cancellation_rate")) * CANCELLATION_WEIGHT)
    )


def compute_with_verified_scale(row, verified_scale):
    return compute_base_score(row) + (_f(row.get("verified_bonus")) * VERIFIED_WEIGHT * verified_scale)


def baseline_sort_key(row):
    return (
        -_f(row.get("hybrid_score")),
        -_f(row.get("safe_rating")),
        _i(row.get("price_cents")),
        _i(row.get("provider_id")),
    )


def scenario_sort_key(row, score_key):
    return (
        -_f(row.get(score_key)),
        -_f(row.get("safe_rating")),
        _i(row.get("price_cents")),
        _i(row.get("provider_id")),
    )


def top_membership_change_pct(real_rows, counter_rows, size):
    effective_size = min(size, len(real_rows), len(counter_rows))
    if effective_size <= 0:
        return 0.0
    real_top = [_i(row.get("provider_id")) for row in real_rows[:effective_size]]
    counter_top = {_i(row.get("provider_id")) for row in counter_rows[:effective_size]}
    dropped = sum(1 for provider_id in real_top if provider_id not in counter_top)
    return (dropped / effective_size) * 100


def format_row(rank, row, score_key):
    provider_name = row.get("provider_display_name") or f"Provider {_i(row.get('provider_id'))}"
    return (
        f"#{rank:02d} "
        f"provider_id={_i(row.get('provider_id'))} "
        f"name={provider_name!r} "
        f"score={_f(row.get(score_key)):.6f} "
        f"verified={_f(row.get('verified_bonus')):.1f} "
        f"rating={_f(row.get('safe_rating')):.2f} "
        f"completed={_i(row.get('safe_completed'))} "
        f"price_cents={_i(row.get('price_cents'))}"
    )


def analyze_against_baseline(baseline_rows, scenario_rows, scenario_score_key):
    baseline_rank = {
        _i(row.get("provider_id")): index
        for index, row in enumerate(baseline_rows, start=1)
    }
    scenario_rank = {
        _i(row.get("provider_id")): index
        for index, row in enumerate(scenario_rows, start=1)
    }

    moves = []
    for row in baseline_rows:
        provider_id = _i(row.get("provider_id"))
        old_rank = baseline_rank[provider_id]
        new_rank = scenario_rank[provider_id]
        rank_delta = new_rank - old_rank
        moves.append(
            {
                "provider_id": provider_id,
                "name": row.get("provider_display_name"),
                "baseline_rank": old_rank,
                "scenario_rank": new_rank,
                "rank_delta": rank_delta,
                "verified_bonus": _f(row.get("verified_bonus")),
                "score_real": _f(row.get("hybrid_score")),
                "scenario_score": _f(row.get(scenario_score_key)),
            }
        )

    changed_moves = [move for move in moves if move["rank_delta"] != 0]
    changed_moves.sort(
        key=lambda move: (-abs(move["rank_delta"]), move["baseline_rank"], move["provider_id"])
    )

    average_abs_displacement = mean(abs(move["rank_delta"]) for move in moves)
    max_displacement = max(moves, key=lambda move: abs(move["rank_delta"]))

    return {
        "moves": moves,
        "changed_moves": changed_moves,
        "average_absolute_displacement": average_abs_displacement,
        "top_3_membership_change_pct": top_membership_change_pct(baseline_rows, scenario_rows, 3),
        "top_5_membership_change_pct": top_membership_change_pct(baseline_rows, scenario_rows, 5),
        "max_displacement": max_displacement,
    }


def print_summary(label, rows, analysis, score_key, show_moves):
    print(f"=== TOP 10 {label} ===")
    for index, row in enumerate(rows[:10], start=1):
        print(format_row(index, row, score_key))
    print()

    print(f"=== SUMMARY: {label} ===")
    print(f"providers_with_rank_change={len(analysis['changed_moves'])}")
    print(f"average_absolute_displacement={analysis['average_absolute_displacement']:.4f}")
    print(f"top_3_membership_change_pct={analysis['top_3_membership_change_pct']:.2f}")
    print(f"top_5_membership_change_pct={analysis['top_5_membership_change_pct']:.2f}")
    max_displacement = analysis["max_displacement"]
    print(
        "max_displacement="
        f"{abs(max_displacement['rank_delta'])} "
        f"(provider_id={max_displacement['provider_id']}, "
        f"baseline_rank={max_displacement['baseline_rank']}, "
        f"scenario_rank={max_displacement['scenario_rank']})"
    )
    print()

    print(f"=== BIGGEST MOVES: {label} ===")
    if not analysis["changed_moves"]:
        print("No rank changes for this scenario.")
        print()
        return

    for move in analysis["changed_moves"][:show_moves]:
        print(
            "provider_id="
            f"{move['provider_id']} "
            f"name={move['name']!r} "
            f"baseline_rank={move['baseline_rank']} "
            f"scenario_rank={move['scenario_rank']} "
            f"rank_delta={move['rank_delta']} "
            f"verified={move['verified_bonus']:.1f} "
            f"real_score={move['score_real']:.6f} "
            f"scenario_score={move['scenario_score']:.6f}"
        )
    print()


def prepare_rows(raw_rows):
    rows = [dict(row) for row in raw_rows]
    for row in rows:
        row["score_half_verified"] = compute_with_verified_scale(row, 0.5)
        row["score_without_verified"] = compute_with_verified_scale(row, 0.0)
    return rows


def build_scenarios(rows):
    baseline_rows = sorted(rows, key=baseline_sort_key)
    half_rows = sorted(rows, key=lambda row: scenario_sort_key(row, "score_half_verified"))
    no_verified_rows = sorted(rows, key=lambda row: scenario_sort_key(row, "score_without_verified"))

    half_analysis = analyze_against_baseline(
        baseline_rows,
        half_rows,
        "score_half_verified",
    )
    no_verified_analysis = analyze_against_baseline(
        baseline_rows,
        no_verified_rows,
        "score_without_verified",
    )

    return {
        "baseline_rows": baseline_rows,
        "half_rows": half_rows,
        "no_verified_rows": no_verified_rows,
        "half_analysis": half_analysis,
        "no_verified_analysis": no_verified_analysis,
    }


def analyze_slice(*, service_category_id, province, city, limit, offset):
    raw_rows = search_provider_services(
        service_category_id=service_category_id,
        province=province,
        city=city,
        limit=limit,
        offset=offset,
    )
    rows = prepare_rows(raw_rows)
    if not rows:
        return None
    scenarios = build_scenarios(rows)
    scenarios["rows"] = rows
    return scenarios


def discover_slices(*, scope, min_rows, max_slices):
    base_qs = ProviderService.objects.filter(
        is_active=True,
        provider__is_active=True,
    )

    candidates = []

    if scope in {"province", "both"}:
        province_rows = (
            base_qs.values("category_id", "provider__province")
            .annotate(row_count=Count("id"))
            .filter(row_count__gte=min_rows)
            .order_by("-row_count", "category_id", "provider__province")[:max_slices]
        )
        for row in province_rows:
            candidates.append(
                {
                    "scope": "province",
                    "service_category_id": row["category_id"],
                    "province": row["provider__province"],
                    "city": None,
                    "row_count": row["row_count"],
                }
            )

    if scope in {"city", "both"}:
        city_rows = (
            base_qs.exclude(provider__city__isnull=True)
            .exclude(provider__city="")
            .values("category_id", "provider__province", "provider__city")
            .annotate(row_count=Count("id"))
            .filter(row_count__gte=min_rows)
            .order_by("-row_count", "category_id", "provider__province", "provider__city")[:max_slices]
        )
        for row in city_rows:
            candidates.append(
                {
                    "scope": "city",
                    "service_category_id": row["category_id"],
                    "province": row["provider__province"],
                    "city": row["provider__city"],
                    "row_count": row["row_count"],
                }
            )

    candidates.sort(
        key=lambda item: (
            -item["row_count"],
            item["service_category_id"],
            item["province"],
            item["city"] or "",
            item["scope"],
        )
    )
    return candidates[:max_slices]


def format_slice_label(slice_info):
    label = (
        f"cat={slice_info['service_category_id']} "
        f"prov={slice_info['province']}"
    )
    if slice_info.get("city"):
        label += f" city={slice_info['city']}"
    label += f" [{slice_info['scope']}]"
    return label


def print_matrix_summary(results):
    headers = [
        ("SLICE", 42),
        ("ROWS", 6),
        ("AVG_0.5", 10),
        ("AVG_0.0", 10),
        ("TOP3_0.5", 10),
        ("TOP3_0.0", 10),
        ("MAX_0.5", 8),
        ("MAX_0.0", 8),
    ]

    header_line = " ".join(name.ljust(width) for name, width in headers)
    print(header_line)
    print("-" * len(header_line))

    for result in results:
        max_half = abs(result["half_analysis"]["max_displacement"]["rank_delta"])
        max_zero = abs(result["no_verified_analysis"]["max_displacement"]["rank_delta"])
        row = [
            result["label"][:42].ljust(42),
            str(result["row_count"]).rjust(6),
            f"{result['half_analysis']['average_absolute_displacement']:.4f}".rjust(10),
            f"{result['no_verified_analysis']['average_absolute_displacement']:.4f}".rjust(10),
            f"{result['half_analysis']['top_3_membership_change_pct']:.2f}".rjust(10),
            f"{result['no_verified_analysis']['top_3_membership_change_pct']:.2f}".rjust(10),
            str(max_half).rjust(8),
            str(max_zero).rjust(8),
        ]
        print(" ".join(row))


def main():
    args = parse_args()

    if args.auto_discover:
        candidates = discover_slices(
            scope=args.discovery_scope,
            min_rows=args.min_rows,
            max_slices=args.max_slices,
        )
        if not candidates:
            print("No slices matched the discovery criteria.")
            return

        results = []
        for slice_info in candidates:
            scenarios = analyze_slice(
                service_category_id=slice_info["service_category_id"],
                province=slice_info["province"],
                city=slice_info["city"],
                limit=args.limit,
                offset=args.offset,
            )
            if not scenarios:
                continue
            results.append(
                {
                    "label": format_slice_label(slice_info),
                    "row_count": len(scenarios["rows"]),
                    "half_analysis": scenarios["half_analysis"],
                    "no_verified_analysis": scenarios["no_verified_analysis"],
                }
            )

        if not results:
            print("No non-empty slices returned from the discovered candidates.")
            return

        print("=== MULTI-SLICE SUMMARY ===")
        print_matrix_summary(results)
        return

    if args.service_category_id is None or not args.province:
        raise SystemExit(
            "--service-category-id and --province are required unless --auto-discover is used."
        )

    scenarios = analyze_slice(
        service_category_id=args.service_category_id,
        province=args.province,
        city=args.city,
        limit=args.limit,
        offset=args.offset,
    )

    if not scenarios:
        print("No rows returned for the requested marketplace slice.")
        return

    rows = scenarios["rows"]
    baseline_rows = scenarios["baseline_rows"]
    half_rows = scenarios["half_rows"]
    no_verified_rows = scenarios["no_verified_rows"]
    half_analysis = scenarios["half_analysis"]
    no_verified_analysis = scenarios["no_verified_analysis"]

    print("=== PARAMETERS ===")
    print(
        "service_category_id="
        f"{args.service_category_id} "
        f"province={args.province!r} "
        f"city={args.city!r} "
        f"limit={args.limit} "
        f"offset={args.offset}"
    )
    print()

    print("=== TOP 10 REAL ===")
    for index, row in enumerate(baseline_rows[:10], start=1):
        print(format_row(index, row, "hybrid_score"))
    print()

    print(f"rows_analyzed={len(rows)}")
    print()

    print_summary(
        "WITH VERIFIED 0.5x",
        half_rows,
        half_analysis,
        "score_half_verified",
        args.show_moves,
    )
    print_summary(
        "WITHOUT VERIFIED",
        no_verified_rows,
        no_verified_analysis,
        "score_without_verified",
        args.show_moves,
    )


if __name__ == "__main__":
    main()
