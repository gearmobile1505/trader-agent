# Brief: change take-profit target $125 → $100 across all pairs

Owner-directed change (2026-10-04). This modifies a frozen risk parameter
($125 TP) on the owner's explicit instruction after reviewing the evidence
below. All other risk parameters stay frozen.

## Owner's evidence

- Owner observation: many trades reach +$100 unrealized and then reverse
  without ever reaching the $125 TP.
- Corroborated by the 2026-10-04 weekly review: average win is +$66.37
  against the $125 target — the $125 level is rarely achieved. Winners
  appear to stall in the $100–$125 zone and reverse into the +$50 profit
  lock (or worse), so the TP change banks +$100 on trades that currently
  exit around +$50.

## Required change

Change the take-profit target from **$125 to $100 for every symbol**
(across all pairs). Locate the TP constant(s) — expected to be TP_CONFIG
or equivalent — and change every occurrence. No symbol is exempt.

## Interaction notes (do not break these)

- The +$75 → breakeven rule is unchanged.
- The +$90 → lock +$50 second-stage rule is unchanged and still triggers
  before the new TP (no ordering conflict).
- Sizing formula, $100 target risk, $400 daily-loss gate, sessions, and
  all entry-vetting gates are untouched.

## Required evidence before merge (replay gate)

Run a replay over `alerts_log.jsonl` (and broker history where needed)
comparing TP=$100 vs TP=$125 and post in this PR:

1. Count of historical trades that touched +$100 unrealized and then
   failed to reach +$125 (the owner's claimed population).
2. Count of historical trades that reached +$125 (the population that
   loses $25 each under the new TP).
3. Net P&L delta of TP=$100 vs TP=$125 across the replay window.

If the replay shows the change is net negative, say so plainly with the
numbers instead of implementing — the owner decides with evidence.

## Tests

- Update existing TP-related tests to the $100 target; add a test
  asserting every symbol's configured TP equals $100 (no pair left at
  $125).

## Frozen

No change to $100 target risk, $75 breakeven, the +$90 → +$50 lock,
$400 max daily loss, sizing formula, lot caps, sessions, or any entry
vetting gate.

show the replay numbers before merge, no deploy until approved.
