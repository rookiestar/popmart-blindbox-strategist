# Current market and heat research

This stage runs before any tray calculation. Its purpose is to provide a current external reference, not to dictate the user's personal preference.

## Region and timestamp

- If the user does not specify a market, default to **mainland China, CNY**, and state that choice.
- Record the observation date and time.
- Evidence allowlist: POP MART official facts plus **千岛、闲鱼、小红书 only**.
- Exclude 淘宝、京东, every other marketplace, and every overseas price, transaction, ranking, and community signal.
- Use the regular-only path. Research hidden designs only when the user explicitly requests them; otherwise omit their facts, prices, and demand signals and add one `隐藏款：默认未计入` note.

## Capability gate

Use capabilities already available in the host. The workflow must not require a
new account, paid search service, third-party API key, or exported browser
credential.

Use this order:

1. **User material:** reuse any official URL, saved page, market screenshot, or
   lineup the user already supplied.
2. **Native discovery:** when the host already provides web search, use it only
   to locate an official POP MART page inside the allowlist.
3. **Direct public fetch:** fetch the official page and the QianDao public search
   page without an API key.
4. **Logged-in browser:** when an interactive browser can safely reuse the
   user's existing session, use it for the optional Xianyu and Xiaohongshu
   cross-check.
5. **Graceful fallback:** if current market evidence is unavailable, continue
   tray analysis from user-supplied lineup and clues, label market data
   `unavailable`, and disable resale-based ranking. For a market-only request,
   ask for an official URL, saved page, or screenshots instead of guessing.

## Fast research protocol

The default path should finish without an interactive browser when public
structured data is available.

1. **Capability gate.** Inventory user material, native search, direct fetch,
   Python, public network, and logged-in browser access. Do not ask the user to
   configure a search provider.
2. **Identity.** Locate the POP MART official product page with user material or
   already-available native search, then establish the official Chinese and
   English series names. Discard evidence from outside the allowlist.
3. **Official facts.** Fetch that page directly. Inspect rendered text, JSON-LD,
   `__NEXT_DATA__`, `__NUXT_DATA__`, and product-image alt text in one pass.
4. **Full-market batch.** When Python and public network access are available,
   run:

   ```bash
   python3 scripts/qiandao_market_snapshot.py "<exact Chinese series name>" \
     --category "<series category name on QianDao>" \
     --retail-price <CNY> --expected-count <regulars> \
     --format markdown
   ```

5. **Coverage gate.** Compare returned design IDs/names with the regular lineup.
   If the batch is complete, do not open every design detail page.
6. **Independent checks.** Only when a safe logged-in browser is already
   available, search the exact series once on 闲鱼 and once on 小红书. If
   differentiation is still unclear, inspect at most two designs: proposed hot
   and proposed weak.
7. **Escalation.** Follow `market-browser-search.md`: reuse one tab per site and
   perform one batched DOM extraction per query. One focused failed attempt per
   source is enough; never browse design-by-design.
8. **Stop rule.** If the browser capability, 闲鱼, or 小红书 is inaccessible,
   continue with 千岛, cap evidence confidence at `medium`, and report the
   missing source. Keep the allowlist fixed.
9. **No-market fallback.** If no current market source can be verified, do not
   assign heat or resale labels. Continue non-resale tray analysis when the
   lineup and clues are available; otherwise request user-supplied material.

Target execution shape when all capabilities are available: one official
discovery, one official fetch, one 千岛 batch, one 闲鱼 search, and one 小红书
search. Extra design queries require a concrete unresolved contradiction.

Run the three market-source lookups concurrently when the available tools permit it. Keep each source's observations separate until synthesis.

## Source roles

### 1. Official facts

Use POP MART product pages and official launch announcements for:

- exact series and design names;
- regular count and case size;
- retail price;
- release date;

Add hidden odds and replacement rules only when the user explicitly requests the hidden-design branch.
In that branch, add `--include-secret` and adjust `--expected-count`.

Do not use reseller pages to establish official mechanics when an official source exists.

### 2. 千岛 — primary resale snapshot

Use the batch script first. Its fields mean:

- `average_sold_price` — 千岛 `strikePrice`, displayed by the platform as **成交均价**;
- `highest_buy_order` — current highest buyer bid;
- `lowest_sell_order` — current lowest seller ask;
- `wish_count` — cumulative platform demand signal;
- `wish_count_3d` — recent demand signal when present.

成交均价, current bids, current asks, and wish counts are different evidence types from one platform; they are not independent platforms. Flag crossed or stale-looking order books instead of silently resolving them.

### 3. 闲鱼 — independent listing corroboration

Use one exact-series search, then diagnostic designs only if needed. Prefer:

1. completed or sold transactions;
2. listings with sold count or recent transaction history;
3. active listing prices;
4. search snippets only when the result page itself is inaccessible.

An empty logged-out result is `inaccessible/unverified`, not evidence that no market exists. Do not ask the user to log in when 千岛 already supports a medium-confidence answer.

For every observation, distinguish:

- `成交/已售` — transaction evidence;
- `挂牌` — seller asking price;
- `求购` — buyer bid, not a sale;
- `整盒/套装` — do not mix with single-design prices;
- `拆盒确认款` versus sealed blind box;
- opened, damaged, missing-card, or customized items.

### 4. 小红书 — community demand direction

Use the exact series query first. Look for repeated, release-specific signals:

- repeated wish-list or “求” mentions;
- collector polls;
- unboxing selection discussions;
- rapid sell-through or frequent restocking comments;
- persistent complaints about difficult resale;
- character/IP demand specific to this release.

Do not convert one high-engagement post into broad demand. Distinguish repeated preference from resale liquidity. If login blocks the result after one focused attempt, mark it unavailable and stop.

## Suggested search queries

Replace placeholders with the exact official Chinese and English series/design names:

- `泡泡玛特 <系列名> 官方 常规款`
- `<系列名> <款名> 闲鱼 价格`
- `<系列名> <款名> 成交价`
- `<系列名> 热款 雷款`
- `<系列名> 最想要 投票`
- `<系列名> 二手 溢价`

Search individual design names only for at most two diagnostic designs. Correct aliases only after verifying the official name.

## Price sampling

For each design, aim for at least five recent comparable observations. When possible:

1. exclude obvious outliers, bundles, shipping-only prices, damaged items, and unrelated accessories;
2. use the median as the central reference;
3. provide a realistic low–high range rather than a false single-point “market price”;
4. state sample count and whether values are transactions or listings;
5. compute premium/discount against the comparable retail unit price.

If fewer than three usable observations exist, label confidence `low` and avoid a precise median.

## Confidence

- **High:** complete 千岛 transaction/order-book coverage plus accessible 闲鱼 corroboration and repeated 小红书 demand direction, with no material contradiction.
- **Medium:** complete 千岛 coverage, while 闲鱼 or 小红书 is inaccessible or only one cross-check is usable.
- **Low:** incomplete lineup, missing transaction fields, obvious mismatches, or fewer than three comparable observations for a claimed precise price.
- **Unavailable:** no current allowlisted market evidence could be verified. Do
  not classify heat, estimate resale value, or select `保值优先`.

Never call a single-platform result `high` confidence.

## Heat classification

Classify market demand conservatively:

- **Hot/strong**: clear resale premium or rapid liquidity, supported by multiple signals.
- **Ordinary**: near-retail pricing or mixed demand, with no consistent premium/discount.
- **Weak/poor-resale**: persistent discount, slow liquidity, or repeated low-demand evidence.

“雷款” here means **market weak or difficult to resell**, not aesthetically bad. Keep this separate from the user's disliked list.

Do not invent a numerical heat score unless the underlying data supports every component. Evidence and confidence are more useful than decorative precision.

## Required initial response format

### Series facts

| Field | Current finding |
|---|---|
| Official series name | ... |
| Regular designs / case size | ... |
| Retail unit price | ... |
| Release date | ... |

### Market snapshot

| Design | Market class | Observed price reference | Premium/discount | Liquidity | Main reason | Evidence confidence |
|---|---:|---:|---:|---:|---|---|

Cover every regular design. Then write exactly once: `隐藏款：默认未计入`.

### Decision-relevant summary

State only the evidence-backed conclusions:

- strongest demand / resale candidates;
- likely weak-resale candidates;
- designs with uncertain or conflicting signals;
- whether market price data is too sparse to use as a ranking tie-break.
- which of 千岛、闲鱼、小红书 were actually accessible.

Then ask for:

1. one current tray screenshot;
2. ordered liked designs;
3. ordered disliked designs;
4. number of hint and display cards expected;
5. any goal change such as “strictly avoid dislikes” or “only chase the top target.”

When market evidence is `unavailable`, replace the market table with one short
availability note and continue only from verified user-supplied lineup,
preferences, and tray clues.

## Citation and honesty requirements

- Cite every time-sensitive market or product-mechanics claim.
- Include the observation date in the same section as prices.
- Do not convert active listings into “成交价.”
- Do not claim that social popularity guarantees resale liquidity.
- If the evidence is inaccessible, sparse, or contradictory, say so and reduce confidence.
- Keep every claim inside the POP MART official + 千岛 + 闲鱼 + 小红书 allowlist.
- Do not use 淘宝、京东, other marketplaces, or overseas evidence.
