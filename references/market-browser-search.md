# Logged-in Xianyu and Xiaohongshu search

Use this protocol only after the QianDao batch snapshot needs an independent listing or demand cross-check.

## Source and permission boundary

- Evidence allowlist: POP MART official facts, 千岛、闲鱼、小红书.
- Excluded: 淘宝、京东, every other marketplace, and all overseas prices, rankings, transactions, or community signals.
- Use an available interactive browser only when it can reuse the user's
  existing logged-in session. A host-provided Chrome connector is one optional
  implementation, not a required dependency.
- Restrict work to four read-only operations: search Xianyu, inspect one
  Xianyu item, search Xiaohongshu, and inspect one Xiaohongshu note. Map these
  operations to whatever browser tools the host actually provides.
- Allow only `goofish.com` and `xiaohongshu.com`. Never publish, comment, like, follow, message, buy, or delete.
- Keep browser credentials inside the user's browser. Do not export Cookie, Storage State, local storage, or a browser profile.
- Stop immediately on login loss, CAPTCHA, abnormal-traffic warnings, or access challenges. Continue from QianDao with at most medium confidence; do not add another source.
- If no suitable browser capability exists, skip both sources, report the
  missing cross-check, and continue from QianDao. Never ask the user to share
  credentials or configure a third-party search API.

## Tight search loop

1. Reuse one search tab per site. Xianyu and Xiaohongshu may run concurrently.
2. Search the exact official series name once on each site.
3. Only when the series results cannot distinguish the market leaders, inspect at most two diagnostic designs: proposed hot and proposed weak. Search a hidden design only when the user explicitly requests it.
4. Wait for result cards to appear, then run one DOM extraction that returns at most the first 20 cards. Do not take screenshots, request repeated page snapshots, inspect cards one by one, or open every design.
5. Deduplicate across queries by `item_id` or `note_id` before analysis.
6. Open detail pages only for decision-relevant missing fields or a contradiction. Reuse one detail tab per site and inspect at most five records per focused check.
7. Record query, observation time, raw count, unique count, elapsed time, login/challenge status, and field completeness.

## Xianyu extraction

Search URL:

```text
https://www.goofish.com/search?q=<URL-encoded keyword>
```

Collect cards whose item link yields a stable item ID. Return:

```text
item_id, title, price, condition, location, url
```

Normalize the canonical item URL and numeric price. Classify each record as single confirmed design, sealed blind box, full set, wanted listing, accessory, customized/damaged, or unrelated. Only comparable active single-design listings support a single-design price range. A listing price is never a completed sale.

On a detail page, restrict description extraction to the product's main region and choose the longest product description there; exclude return-policy and generic platform text.

## Xiaohongshu extraction

Search URL:

```text
https://www.xiaohongshu.com/search_result?keyword=<URL-encoded keyword>
```

Extract from `section.note-item` cards; the current page must not rely on `window.__INITIAL_STATE__.search.feeds`. Return:

```text
note_id, title, author, liked_count, publish_time, url
```

Require a normalized series or verified design-name match in the title or visible excerpt. Reject unrelated same-franchise products and generic franchise posts before synthesis. Deduplicate by `note_id`.

Normalize the visible zero labels `赞`, `收藏`, and `评论` to numeric `0`. If navigation requires a temporary `xsec_token`, keep it only in memory for that page load; remove it from returned records, logs, caches, and reports.

On a detail page, return:

```text
note_id, title, author, description, publish_time,
liked_count, collected_count, comment_count
```

Use repeated relevant posts as demand direction only. Engagement is not sales evidence.

## Completion gate

The browser check is complete when:

- result IDs are unique after deduplication;
- required-field completeness is reported;
- irrelevant Xianyu listing types and Xiaohongshu posts are excluded;
- no browser credential or temporary access token is saved or returned;
- every price is labeled as sold evidence, buyer bid, seller ask, or active listing;
- failure on either site reduces confidence instead of widening the source set.
