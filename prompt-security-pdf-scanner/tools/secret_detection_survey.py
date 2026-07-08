"""Survey: local secret-detection approaches vs. the Prompt Security API.

Compares three *local* detectors — regex, Shannon entropy, and the two combined —
against the Prompt Security API (reference), on a small labeled dataset. Reports
precision / recall / F1, latency, throughput, and peak memory.

Run (from service venv, network needed for the API column):
    python -m tools.secret_detection_survey            # includes the API column
    python -m tools.secret_detection_survey --no-api   # local approaches only
"""
from __future__ import annotations

import math
import os
import re
import sys
import time
import tracemalloc
from collections import Counter

import httpx

# The APP-ID is a credential and is not committed. Set it in the environment to
# include the API column, e.g.:  PROMPT_SECURITY_APP_ID=... python tools/secret_detection_survey.py
PS_URL = os.environ.get("PROMPT_SECURITY_URL", "https://eu.prompt.security/api/protect")
PS_APP_ID = os.environ.get("PROMPT_SECURITY_APP_ID", "")


def _tok(n: int) -> str:
    import itertools, string  # local import keeps top clean
    pool = string.ascii_letters + string.digits
    it = itertools.cycle(pool)
    return "".join(next(it) for _ in range(n))


# --- labeled dataset: (text, is_secret) ------------------------------------
DATASET = [
    # positives
    ("aws_access_key_id = AKIAIOSFODNN7EXAMPLE", True),
    ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", True),  # no prefix -> entropy job
    ("github token: ghp_" + _tok(36), True),
    ("STRIPE_KEY=sk_live_" + _tok(24), True),
    ("google api key=AIza" + _tok(35), True),
    ("slack: xoxb-123456789012-1234567890123-" + _tok(24), True),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0." + _tok(43), True),
    ("-----BEGIN RSA PRIVATE KEY-----\n" + _tok(64) + "\n-----END RSA PRIVATE KEY-----", True),
    ("openai key sk-" + _tok(48), True),
    ("password = Sup3rS3cr3tP@ssw0rd!", True),
    ("here is a credential blob: " + _tok(46), True),  # unprefixed high-entropy
    # negatives (incl. hard ones that tempt entropy false positives)
    ("The quarterly report is due next Friday, please review it.", False),
    ("Meeting notes: we agreed to ship the migration ahead of schedule.", False),
    ("request id 550e8400-e29b-41d4-a716-446655440000 processed ok", False),  # UUID
    ("commit 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b merged to main", False),  # git sha (hex)
    ("Contact: jane.doe@example.com or call 415-555-0132 for details.", False),
    ("The temperature was 72 degrees and the sky was clear all day.", False),
    ("Order #100294 shipped via carrier on 2026-07-01 to the warehouse.", False),
    ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do", False),
    ("The file path is /usr/local/bin/python3 and the port is 8000.", False),
]


# --- Approach 1: regex ------------------------------------------------------
REGEX_RULES = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"sk_live_[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*[\"']?\S{6,}"),
]


def regex_predict(text: str) -> bool:
    return any(r.search(text) for r in REGEX_RULES)


# --- Approach 2: Shannon entropy -------------------------------------------
def shannon(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-/+=.]{16,}")  # candidate long tokens


def entropy_predict(text: str, *, min_len: int = 20, min_entropy: float = 4.0) -> bool:
    for tok in _TOKEN_RE.findall(text):
        core = tok.strip("=.-/")
        if len(core) >= min_len and shannon(core) >= min_entropy:
            return True
    return False


# --- Approach 3: combined ---------------------------------------------------
def combined_predict(text: str) -> bool:
    return regex_predict(text) or entropy_predict(text)


# --- Reference: Prompt Security API ----------------------------------------
def ps_predict(text: str, client: httpx.Client) -> bool:
    r = client.post(PS_URL, headers={"APP-ID": PS_APP_ID, "Content-Type": "application/json"},
                    json={"prompt": text})
    p = (r.json().get("result") or {}).get("prompt") or {}
    return bool((p.get("findings") or {}).get("Secrets"))


# --- metrics ----------------------------------------------------------------
def score(preds: list[bool]) -> dict:
    tp = sum(1 for pr, (_, lab) in zip(preds, DATASET) if pr and lab)
    fp = sum(1 for pr, (_, lab) in zip(preds, DATASET) if pr and not lab)
    fn = sum(1 for pr, (_, lab) in zip(preds, DATASET) if not pr and lab)
    tn = sum(1 for pr, (_, lab) in zip(preds, DATASET) if not pr and not lab)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    acc = (tp + tn) / len(DATASET)
    return {"precision": prec, "recall": rec, "f1": f1, "accuracy": acc, "tp": tp, "fp": fp, "fn": fn}


def run_local(name: str, fn) -> dict:
    tracemalloc.start()
    t0 = time.perf_counter()
    preds = [fn(text) for text, _ in DATASET]
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    m = score(preds)
    m.update(name=name, per_sample_us=elapsed / len(DATASET) * 1e6,
             throughput=len(DATASET) / elapsed, peak_kb=peak / 1024)
    return m


def run_api() -> dict:
    with httpx.Client(timeout=30) as c:
        t0 = time.perf_counter()
        preds = [ps_predict(text, c) for text, _ in DATASET]
        elapsed = time.perf_counter() - t0
    m = score(preds)
    m.update(name="Prompt Security API", per_sample_us=elapsed / len(DATASET) * 1e6,
             throughput=len(DATASET) / elapsed, peak_kb=float("nan"))
    return m


def main() -> None:
    include_api = "--no-api" not in sys.argv and bool(PS_APP_ID)
    if "--no-api" not in sys.argv and not PS_APP_ID:
        print("(API column skipped — set PROMPT_SECURITY_APP_ID to include it.)")
    rows = [
        run_local("Regex", regex_predict),
        run_local("Entropy", entropy_predict),
        run_local("Regex+Entropy", combined_predict),
    ]
    if include_api:
        rows.append(run_api())

    print(f"\nDataset: {len(DATASET)} samples "
          f"({sum(1 for _, l in DATASET if l)} secrets, {sum(1 for _, l in DATASET if not l)} clean)\n")
    hdr = f"{'approach':<20} | {'prec':>5} | {'rec':>5} | {'F1':>5} | {'acc':>5} | {'FN':>3} | {'FP':>3} | {'latency/sample':>16} | {'peak mem':>9}"
    print(hdr)
    print("-" * len(hdr))
    for m in rows:
        lat = f"{m['per_sample_us']:.1f} us" if m["per_sample_us"] < 1000 else f"{m['per_sample_us']/1000:.1f} ms"
        mem = "n/a (net)" if m["peak_kb"] != m["peak_kb"] else f"{m['peak_kb']:.0f} KB"
        print(f"{m['name']:<20} | {m['precision']:>5.2f} | {m['recall']:>5.2f} | {m['f1']:>5.2f} | "
              f"{m['accuracy']:>5.2f} | {m['fn']:>3} | {m['fp']:>3} | {lat:>16} | {mem:>9}")
    print()


if __name__ == "__main__":
    main()
