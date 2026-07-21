## F-04 — Validator numeric grounding: fix + §3.1 characterization

**Severity:** critical (was) → fixed
**README claim affected:** #3 ("every proper noun/number is backed by the brief")
**Status:** fixed (numeric scope) · open gaps flagged below (non-numeric scope)

### Claim under test
Numbers in slot fills are grounded by the research brief. Before M0.3 they rode the same
`fuzz.partial_ratio ≥ 90` path as entities.

### Method
`tests/unit/test_validator_numeric.py` (normalization + matching units, 15 tests) and
`tests/adversarial/test_validator_attacks.py` (the full `TESTING.md` §3.1 table, 26
predict-then-run tests). Run: `pytest tests/unit/test_validator_numeric.py
tests/adversarial/test_validator_attacks.py` — all pass; full suite 133 passed, 0 skipped.

### Predicted vs actual — every §3.1 row
| Row | Predicted | Actual | Verdict |
|---|---|---|---|
| `$4M`→`$40M` | reject | reject | fixed by M0.3 |
| `1,000`→`10,000` | reject | reject | fixed (this was the confirmed bug: partial_ratio ≥ 90 passed it before) |
| `$4.2M`→`$4M` (only 4.2 in brief) | reject | reject | fixed — silent rounding caught |
| `1,000`→`1000` | pass | pass | normalization works, no false rejection |
| `Q3 2025`→`third quarter` | pass | pass | no digits/caps in fill → nothing to ground |
| `Acme Corp`→`Acme Group` | reject (~84 < 90) | reject | entity path OK here |
| `Series A`→`Series B` | reject (87.5 < 90) | reject | entity path OK here |
| `Anthropic`→`Anthropics` | pass (bad) | pass | **GAP-1** below |
| `iPhone`/`deepmind`/`eBay` | pass (invisible) | pass | **GAP-2** below |
| sentence-initial common noun | pass | pass | by design |
| `March 2024`→`2024` | pass | pass | year genuinely grounded |
| `12%`→`12x` | pass | pass | **GAP-3** — accepted limitation of approved rule |
| hallucinated `40%` | reject | reject | percent strictness works |
| banned: case / punctuation | reject | reject | substring-on-lowercase holds |
| banned: double space | pass (uncaught) | pass | **GAP-4** |
| banned: unicode lookalike | pass (uncaught) | pass | **GAP-4** |
| "Hope this email finds you well!" | pass (uncaught) | pass | **GAP-5** — list lacks the I-less variant |
| subject 7/8, body 90/91 words | boundary exact | as predicted | caps are `>`, boundaries legal |
| body with URL | one token | as predicted | str.split() semantics |
| em-dash in body (not slots) | pass (uncaught) | pass | **GAP-6** — dash check scans slots only |

**Every prediction matched the observed behavior** (26/26). I checked behavior, not just
that tests pass — the two mandated pairs were also verified interactively before the
tests were written.

### Flagged gaps — NOT fixed (outside M0.3's approved scope; all are knob/product decisions)
- **GAP-1** Pluralized entities pass fuzzy at ≈95 (`Anthropics`). Fix = threshold or
  token-boundary change → knob decision.
- **GAP-2** Lowercase brands (`deepmind`, `iPhone`, `eBay`) are invisible to `_PROPER_RE`
  — never extracted, never gated.
- **GAP-3** `12x` extracts as bare `12`, which the approved asymmetric rule lets ground
  against `12%`. Strict-kinds-both-ways would catch it at the cost of false rejections.
- **GAP-4** Banned-phrase substring match is defeated by doubled whitespace and unicode
  lookalikes. Fix = normalize whitespace + confusables before matching.
- **GAP-5** Phrase list lacks "hope this email finds you well" (without leading "i").
  List additions are a product knob.
- **GAP-6** Em-dash check scans joined slots, not `body_text` (minor: skeleton bodies
  are human-authored).

### Product question for the human
None blocking. GAP-1..6 are candidates for a future validator-hardening epic; each needs
a knob or product decision per `TESTING.md` §0.
