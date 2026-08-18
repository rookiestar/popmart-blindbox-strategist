#!/usr/bin/env python3
"""Batch-extract QianDao resale data from its public search SSR payload."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any


SEARCH_URL = "https://qiandao.com/search"
NUXT_DATA_RE = re.compile(
    r"""<script\b[^>]*\bid=["']__NUXT_DATA__["'][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)
REACTIVE_TAGS = {
    "Reactive",
    "Readonly",
    "Ref",
    "ShallowReactive",
    "ShallowReadonly",
    "ShallowRef",
}


class SnapshotError(RuntimeError):
    """The public market snapshot could not be fetched or decoded."""


def build_search_url(query: str) -> str:
    return f"{SEARCH_URL}?{urllib.parse.urlencode({'q': query})}"


def fetch_html(url: str, timeout: float = 20.0) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0 Safari/537.36"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except Exception as exc:  # urllib has several transport-specific subclasses
        raise SnapshotError(f"QianDao request failed: {exc}") from exc


def extract_flat_payload(page_html: str) -> list[Any]:
    match = NUXT_DATA_RE.search(page_html)
    if not match:
        if "aliyunwaf" in page_html.lower():
            raise SnapshotError(
                "QianDao returned a WAF challenge; use one Browser page as fallback."
            )
        raise SnapshotError("QianDao page has no __NUXT_DATA__ payload.")
    raw_payload = match.group(1).strip()
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as direct_exc:
        entity_decoded = html.unescape(raw_payload)
        if entity_decoded == raw_payload:
            raise SnapshotError(
                f"Invalid __NUXT_DATA__ JSON: {direct_exc}"
            ) from direct_exc
        try:
            payload = json.loads(entity_decoded)
        except json.JSONDecodeError as fallback_exc:
            raise SnapshotError(
                f"Invalid __NUXT_DATA__ JSON: {fallback_exc}"
            ) from fallback_exc
    if not isinstance(payload, list):
        raise SnapshotError("Unexpected __NUXT_DATA__ root.")
    return payload


def decode_nuxt_payload(flat: list[Any]) -> Any:
    """Decode the Nuxt/devalue subset used by the public search page."""

    memo: dict[int, Any] = {}

    def resolve(reference: Any) -> Any:
        if not isinstance(reference, int) or isinstance(reference, bool):
            return reference
        if reference < 0:
            return {
                -1: None,  # undefined
                -2: None,  # array hole
                -3: math.nan,
                -4: math.inf,
                -5: -math.inf,
                -6: -0.0,
            }.get(reference)
        if reference >= len(flat):
            return reference
        if reference in memo:
            return memo[reference]

        value = flat[reference]
        if isinstance(value, list):
            tag = value[0] if value and isinstance(value[0], str) else None
            if tag in REACTIVE_TAGS:
                decoded = resolve(value[1]) if len(value) > 1 else None
                memo[reference] = decoded
                return decoded
            if tag == "Set":
                decoded_set = [resolve(item) for item in value[1:]]
                memo[reference] = decoded_set
                return decoded_set
            decoded_list: list[Any] = []
            memo[reference] = decoded_list
            decoded_list.extend(resolve(item) for item in value)
            return decoded_list

        if isinstance(value, dict):
            decoded_dict: dict[str, Any] = {}
            memo[reference] = decoded_dict
            decoded_dict.update({key: resolve(item) for key, item in value.items()})
            return decoded_dict

        memo[reference] = value
        return value

    return resolve(0)


def walk_objects(value: Any, seen: set[int] | None = None) -> Iterator[dict[str, Any]]:
    seen = seen or set()
    if not isinstance(value, (dict, list)):
        return
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_objects(child, seen)
    else:
        for child in value:
            yield from walk_objects(child, seen)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


def extract_spu_records(
    decoded: Any,
    *,
    category: str | None = None,
    retail_price: float | None = None,
    include_secret: bool = False,
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for obj in walk_objects(decoded):
        spu = obj.get("spuShow")
        if not isinstance(spu, dict) or spu.get("is_category"):
            continue
        spu_id = str(spu.get("id") or "")
        name = str(spu.get("name") or "").strip()
        category_name = str(spu.get("category_name") or "").strip()
        if not spu_id or not name:
            continue
        if category and category.casefold() not in category_name.casefold():
            continue
        is_secret = bool(re.search(r"隐藏|secret", name, flags=re.IGNORECASE))
        if is_secret and not include_secret:
            continue

        average_sold = _number(spu.get("strikePrice"))
        min_sell = _number(
            spu.get("stock_order_min_sell_price") or spu.get("minOnlinePrice")
        )
        max_buy = _number(spu.get("stock_order_max_buy_price"))
        wish_count = _number(spu.get("wish_count"))
        if not any(
            value is not None
            for value in (average_sold, min_sell, max_buy, wish_count)
        ):
            continue

        premium_pct = None
        if retail_price and average_sold is not None:
            premium_pct = round((average_sold / retail_price - 1) * 100, 1)

        records[spu_id] = {
            "id": spu_id,
            "name": name,
            "category": category_name,
            "is_secret": is_secret,
            "average_sold_price": average_sold,
            "highest_buy_order": max_buy,
            "lowest_sell_order": min_sell,
            "wish_count": wish_count,
            "wish_count_3d": _number(spu.get("wish_count_3d")),
            "premium_to_retail_pct": premium_pct,
            "detail_url": f"https://qiandao.com/spu?id={spu_id}",
        }

    return list(records.values())


def build_snapshot(
    query: str,
    page_html: str,
    *,
    category: str | None = None,
    retail_price: float | None = None,
    include_secret: bool = False,
) -> dict[str, Any]:
    decoded = decode_nuxt_payload(extract_flat_payload(page_html))
    records = extract_spu_records(
        decoded,
        category=category,
        retail_price=retail_price,
        include_secret=include_secret,
    )
    if not records:
        suffix = f" for category {category!r}" if category else ""
        raise SnapshotError(f"No QianDao design records found{suffix}.")
    return {
        "observed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "query": query,
        "category_filter": category,
        "retail_price": retail_price,
        "include_secret": include_secret,
        "source_url": build_search_url(query),
        "record_count": len(records),
        "source_scope": (
            "single-platform snapshot: average sold price, current order book, "
            "and wish counts"
        ),
        "records": records,
    }


def _display_number(value: Any, *, money: bool = False) -> str:
    if value is None:
        return "—"
    if money:
        return f"¥{float(value):g}"
    return f"{value:g}" if isinstance(value, float) else str(value)


def render_markdown(snapshot: dict[str, Any]) -> str:
    rows = [
        (
            "| 序号 | 款式 | 成交均价 | 最高求购 | 最低挂牌 | 想要 | "
            "近3日想要 | 相对官价 |"
        ),
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, record in enumerate(snapshot["records"], start=1):
        premium = record["premium_to_retail_pct"]
        premium_text = "—" if premium is None else f"{premium:+.1f}%"
        rows.append(
            "| {rank} | {name} | {average} | {buy} | {sell} | {wish} | "
            "{wish3d} | {premium} |".format(
                rank=rank,
                name=record["name"],
                average=_display_number(
                    record["average_sold_price"], money=True
                ),
                buy=_display_number(record["highest_buy_order"], money=True),
                sell=_display_number(record["lowest_sell_order"], money=True),
                wish=_display_number(record["wish_count"]),
                wish3d=_display_number(record["wish_count_3d"]),
                premium=premium_text,
            )
        )
    return "\n".join(
        [
            f"观察时间：{snapshot['observed_at']}",
            f"来源：{snapshot['source_url']}",
            f"覆盖：{snapshot['record_count']} 款",
            "",
            *rows,
            "",
            "口径：成交均价 ≠ 当前挂牌；最高求购/最低挂牌是当前订单簿。",
        ]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-extract QianDao resale and demand fields without Browser."
    )
    parser.add_argument("query", help="Exact Chinese series name.")
    parser.add_argument(
        "--category", help="Optional exact/partial QianDao category filter."
    )
    parser.add_argument(
        "--retail-price",
        type=float,
        help="Official unit price in CNY, used only for premium/discount.",
    )
    parser.add_argument(
        "--expected-count",
        type=int,
        help="Fail when the returned design count differs from the official lineup.",
    )
    parser.add_argument(
        "--include-secret",
        action="store_true",
        help="Include hidden/secret designs; default output is regular-only.",
    )
    parser.add_argument(
        "--format", choices=("json", "markdown"), default="json"
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--html-file",
        type=Path,
        help="Parse a saved search page instead of fetching the network.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.html_file:
            page_html = args.html_file.read_text(encoding="utf-8")
        else:
            page_html = fetch_html(
                build_search_url(args.query), timeout=args.timeout
            )
        snapshot = build_snapshot(
            args.query,
            page_html,
            category=args.category,
            retail_price=args.retail_price,
            include_secret=args.include_secret,
        )
        if (
            args.expected_count is not None
            and snapshot["record_count"] != args.expected_count
        ):
            names = [record["name"] for record in snapshot["records"]]
            shown = "、".join(names[:15]) + ("…" if len(names) > 15 else "")
            raise SnapshotError(
                "QianDao coverage mismatch: "
                f"expected {args.expected_count}, got "
                f"{snapshot['record_count']}"
                + (f"（{shown}）" if names else "")
                + ". Filter adjacent series with --category, or include "
                "hidden designs with --include-secret."
            )
    except (OSError, SnapshotError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "markdown":
        print(render_markdown(snapshot))
    else:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
