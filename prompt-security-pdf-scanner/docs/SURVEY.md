# Side note: local secret-detection approaches vs. the Prompt Security API

A small survey exploring whether cheap **local** detection could pre-screen text
before (or alongside) the Prompt Security API — to cut calls/egress and to broaden
coverage. **This is exploratory, not a rigorous benchmark** — read the limitations.

Reproduce: `python tools/secret_detection_survey.py` (from the repo root, service venv).

## Method

- **Dataset:** 20 hand-authored samples — 11 containing a secret (AWS access/secret
  keys, GitHub/Stripe/Google/Slack tokens, JWT, RSA private key, OpenAI key, a
  password assignment, an unprefixed high-entropy blob) and 9 clean (prose, a UUID,
  a git SHA, an email+phone, file paths — deliberately chosen to tempt false
  positives).
- **Approaches:** (1) **Regex** ruleset, (2) **Shannon entropy** over long tokens,
  (3) **Regex+Entropy** combined — all local, no dependencies. Reference column:
  the **Prompt Security API**.
- **Metrics:** precision / recall / F1 / accuracy vs. labels; latency per sample;
  peak memory (`tracemalloc`). Measured on the dev laptop, Python 3.13.

## Results (representative)

| approach | precision | recall | F1 | accuracy | latency/sample | peak mem |
|---|---|---|---|---|---|---|
| Regex | 1.00 | 0.82 | 0.90 | 0.90 | ~5 µs | ~2 KB |
| Entropy | 1.00 | 0.82 | 0.90 | 0.90 | ~13 µs | ~8 KB |
| **Regex+Entropy** | **1.00** | **1.00** | **1.00** | **1.00** | ~9 µs | ~3 KB |
| Prompt Security API | 1.00 | 0.18 | 0.31 | 0.55 | ~260 ms | n/a (network) |

## Interpretation

- **Regex and entropy are complementary.** Each misses 2 samples — but *different*
  ones: regex misses secrets with **no known prefix** (e.g. a bare 40-char AWS
  secret key, a random blob); entropy misses **low-entropy but structured** secrets
  (e.g. a `password = …` assignment). Combined, they cover the whole set.
- **Local is ~5 orders of magnitude faster** (~microseconds vs ~260 ms) and needs
  **kilobytes** of memory. It also runs on-device (no data egress).
- **The API's 0.18 recall is a property of our test key, not the product.** The
  provided demo `APP-ID` is **AWS-scoped** (verified separately: only AWS access/
  secret keys fire; correctly-formatted GitHub/Stripe/Google/Slack/OpenAI/PEM tokens
  do not). Detection breadth is a tenant-policy setting. Within its scope the API is
  precise; it's simply configured narrowly here.

## Limitations (important — don't over-read this)

- **Tiny, self-authored dataset (n=20).** The same author wrote both the regex rules
  and the samples, so local scores are **optimistic** — this measures "do my rules
  match my examples," not real-world performance. On messy real data, expect regex
  recall to drop (novel formats) and precision to drop (false positives on hashes,
  UUIDs, base64 of normal text).
- **Entropy is threshold-sensitive.** The clean git SHA / UUID passed only because
  they sit just under the entropy/length thresholds; different tuning flips them to
  false positives. Real deployments need careful tuning + allowlists.
- **The API column depends on network + the key's policy**, so its latency and
  recall aren't apples-to-apples with a local library.

## Not benchmarked (and why)

- **YARA** — designed for malware/binary signatures, not credential formats; regex
  rule-sets (gitleaks/`detect-secrets` style) are the right tool.
- **`detect-secrets` (Yelp)** — would formalize exactly the regex+entropy combo with
  a maintained plugin set; the production version of "Regex+Entropy" above.
- **LLM / 1-shot classification** — accuracy might improve on ambiguous cases, but
  latency (100s of ms–seconds) and cost make it the wrong choice for a *pre-screen*
  whose entire point is to be cheaper/faster than the network call it gates.

## Takeaway

A combined **regex+entropy** pass is essentially free (µs, KB, on-device) and, on
known formats, has strong recall — a solid **pre-screen**: prioritize likely chunks
(faster early-exit), optionally gate near-zero-signal chunks (cut calls/egress, with
a documented recall trade-off), and even **broaden coverage** where the provider key
is narrowly scoped. The Prompt Security API remains the **authority** for the
categories its policy enables. (Integration is described in the main README's
Performance/scaling → Next; this survey is the evidence behind that recommendation.)
