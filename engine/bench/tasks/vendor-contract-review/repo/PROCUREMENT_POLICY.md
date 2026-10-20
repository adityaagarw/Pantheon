# Procurement policy: the vendor contract register (v3)

Legal keeps a register of every active vendor contract, reviewed on a **review date**. This
policy says what goes in the register, how each value is worked out, and which contract
terms break the policy.

The contracts are in `contracts/`. A contract may have **amendments** and **letters** in
files next to it (`CT-104-amendment-1.md`, `CT-109-termination.md`, …). An amendment
changes only what it says it changes. Later amendments win over earlier ones.

## 1. Which contracts are listed

Every contract, except one ended by a termination agreement whose termination is
effective **on or before** the review date. A contract whose termination takes effect after
the review date is listed, with `terminates_on` set.

## 2. Values

All money is in US dollars, converted at these fixed register rates: **1 EUR = 1.08 USD**,
**1 GBP = 1.27 USD**. Round to cents.

- **annual_fee_usd**: what the contract costs per year. Monthly fees count twelve times.
- **current_term_end**: the last day of the term running on the review date. The initial
  term ends the day before the same date `N` months after the effective date (effective
  2024-05-01, 24 months: ends 2026-04-30). If that is before the review date and the
  contract renews automatically, it has renewed: each renewal term ends the day before the
  same date `M` months after the previous end date plus one day. Repeat until the end is on
  or after the review date. When a month has no such day, use its last day.
- **auto_renews**: whether the contract renews by itself unless someone gives notice.
- **notice_deadline**: only for contracts that renew automatically, otherwise `null`. The
  last day a notice of non-renewal can be given for the current term:
  - `N days`: `N` calendar days before the term end;
  - `N weeks`: `7 × N` calendar days before the term end;
  - `N months`: the same day `N` months before the term end (or that month's last day);
  - `N business days`: count back from the term end, which isn't counted, over business
    days (Monday to Friday, not a company holiday) until you have `N`; the deadline is the
    business day you stopped on.
- **liability_cap_usd**: the most the vendor can owe us. A fixed amount is converted; "N
  times the annual fees" is `N × annual_fee_usd`; "the fees paid or payable in the twelve
  months before the claim" is `annual_fee_usd`; "the greater of A and B" is the larger;
  no cap (or unlimited liability) is `null`.
- **governing_law**: the jurisdiction whose law governs the contract, e.g. `Delaware`,
  `England and Wales`.
- **personal_data**: whether the vendor processes our personal data (staff, customers or
  anyone else's) for us.
- **dpa**: whether a signed data processing agreement or schedule is part of the contract.
  A promise to agree one later is not a DPA.
- **termination_for_convenience**: whether we can end the contract without a reason
  before its term ends.
- **renewal_price_cap_pct**: the most the vendor may raise prices at a renewal, as a fixed
  percentage; `null` if it isn't capped at a fixed percentage (no limit, list prices, an
  index such as CPI, …) or the contract doesn't renew automatically.
- **terminates_on**: the effective date of a termination agreement after the review date,
  otherwise `null`.

## 3. Policy violations

| Code | Breaks the policy when |
| --- | --- |
| `P1` | it renews automatically and its notice deadline is more than 90 days before its term end |
| `P2` | the liability cap is less than one year of fees (`liability_cap_usd < annual_fee_usd`; no cap is fine) |
| `P3` | the governing law isn't one of: England and Wales, Delaware, New York, Germany, Ireland |
| `P4` | the vendor processes personal data and there is no DPA |
| `P5` | the initial term is longer than 36 months and we can't terminate for convenience |
| `P6` | it renews automatically and renewal prices aren't capped at 5 % or less |

## 4. Action

For each listed contract, the first that applies:

1. `ending`: it has a termination after the review date.
2. `ok`: no violations.
3. `missed`: it renews automatically and its notice deadline is before the review date.
4. `renegotiate_now`: its notice deadline (or, if it doesn't renew automatically, its term
   end) is no more than 90 days after the review date.
5. `renegotiate_later`: otherwise.

## 5. Company holidays

2026: 1 Jan, 3 Apr, 6 Apr, 1 May, 25 May, 24 Dec, 25 Dec, 31 Dec.
2027: 1 Jan, 26 Mar, 29 Mar, 3 May, 31 May, 24 Dec, 31 Dec.

## 6. What Legal hands in

- `register.json`: `{"review_date": "YYYY-MM-DD", "contracts": [...]}` with one object per
  listed contract, sorted by `contract_id`, with the keys `contract_id`, `vendor`, and
  every value of section 2, plus `violations` (codes, sorted) and `action`. Dates are ISO
  strings, money is a number.
- `memo.md`: a memo for the CFO. It names every vendor whose action is `missed` or
  `renegotiate_now`, says what to do about each and by when, and gives the total
  `annual_fee_usd` of the contracts with violations.
