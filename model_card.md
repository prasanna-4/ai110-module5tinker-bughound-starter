# BugHound Mini Model Card (Reflection)

---

## 1) What is this system?

**Name:** BugHound
**Purpose:** Analyze a Python code snippet, propose a minimal fix, run reliability checks on the proposed change, and decide whether the fix is safe enough to auto-apply or whether it requires human review.

**Intended users:** Students learning agentic AI workflows, AI reliability concepts, and how LLMs are integrated into larger rule-based systems.

---

## 2) How does it work?

BugHound runs a five-step agentic loop every time the user submits code:

1. **PLAN** — The agent logs that it is starting a scan-and-fix workflow. No code analysis happens here; this step exists to make the agent's intent explicit and traceable.

2. **ANALYZE** — The agent examines the code for issues. In **Heuristic mode** it applies three hand-written rules: detect `print(` statements (Code Quality / Low), detect bare `except:` blocks (Reliability / High), and detect `TODO` comments (Maintainability / Medium). In **Gemini mode** it sends the code to the Gemini API with a strict prompt requiring a JSON array of `{type, severity, msg}` objects. If the API call fails, or if the returned JSON is malformed or contains issues with unrecognized severity values or empty messages, the agent falls back to heuristics automatically.

3. **ACT** — The agent proposes a fix. In **Heuristic mode** it applies mechanical transformations: bare `except:` → `except Exception as e:`, and `print(` → `logging.info(` with an `import logging` header added if missing. In **Gemini mode** it sends both the detected issues and the original code to the API with a prompt that instructs it to return only plain Python with minimal, behavior-preserving changes. If the API returns markdown fences the agent strips them; if it returns empty output it falls back to heuristics.

4. **TEST** — `assess_risk()` scores the proposed fix from 0 to 100, deducting points for high-severity issues, structural changes (code shrinking, return statements removed, functions removed or renamed), and bare-except modifications. The score determines a risk level: **low** (≥75), **medium** (40–74), or **high** (<40).

5. **REFLECT** — The agent sets `should_autofix = True` only when risk level is **low**. Otherwise it recommends human review. Either way, it logs its decision.

Heuristics are used when no API client is configured, when the API call throws an exception, or when the model's output fails validation. This keeps the system functional offline and prevents bad LLM output from propagating downstream.

---

## 3) Inputs and outputs

**Inputs tested:**

| File | Shape | Expected difficulty |
|---|---|---|
| `cleanish.py` | 5-line function with `import logging` | Should produce no issues |
| `print_spam.py` | 9-line script, multiple `print()` calls | Low-severity code quality issues |
| `flaky_try_except.py` | 10-line function with bare `except:` | High-severity reliability issue |
| `mixed_issues.py` | 10-line function with TODO, print, and bare `except:` | Mix of Low, Medium, High |
| Empty string | 0 lines | Edge case: no issues, no fix |

**Outputs observed (Heuristic mode):**

- `cleanish.py`: 0 issues, code returned unchanged, risk score 100 (low), auto-fix enabled.
- `print_spam.py`: 1 issue (Code Quality / Low), fix adds `import logging` and replaces all `print(` calls with `logging.info(`. Risk score 95 (low).
- `flaky_try_except.py`: 1 issue (Reliability / High), fix replaces `except:` with `except Exception as e:`. Risk score 55 (medium) — high-severity deduction (-40) plus bare-except modification (-5).
- `mixed_issues.py`: 3 issues (High + Medium + Low). Risk score 35 (high), auto-fix disabled. The scoring model correctly blocks auto-fix when multiple severity levels combine.

---

## 4) Reliability and safety rules

### Rule 1: High-severity issue deducts 40 points

**What it checks:** Any issue in the detected list with `severity == "High"` reduces the score by 40.

**Why it matters:** High-severity issues (like bare `except:`) indicate that the original code was already masking errors. Any fix to such code carries a higher chance of introducing subtle behavioral changes.

**False positive it could cause:** A High-severity lint warning that is cosmetic (e.g., a variable name style violation flagged as High by the LLM) would unnecessarily suppress auto-fix for a trivial change.

**False negative it could miss:** Two Medium-severity issues (-20 each) together produce the same score drop as one High issue, but the reasoning in the `reasons` list doesn't make this cumulative danger obvious to the reviewer.

---

### Rule 2: Removed/renamed function penalizes 25 points

**What it checks:** Extracts all `def funcname(` names from both original and fixed code using a regex. If any name present in the original is absent from the fixed code, 25 points are deducted.

**Why it matters:** Removing or renaming a function breaks any callers in other modules or files that aren't shown to BugHound. This is exactly the kind of change that looks fine locally but silently breaks a larger system.

**False positive it could cause:** If the LLM renames a function to a better name (e.g., `get_val` → `get_user_value`) the rule penalizes a legitimate improvement. The agent cannot distinguish a rename from a deletion.

**False negative it could miss:** A function whose body is completely rewritten but whose name is preserved passes this check, even if the semantic behavior has changed entirely.

---

## 5) Observed failure modes

### Failure 1: Heuristic fixer over-edits `print` calls used for CLI output

**Snippet:** A script in `print_spam.py` that uses `print()` as intentional CLI output to the user.

**What went wrong:** The heuristic fixer blindly replaced every `print(` with `logging.info(`. For a CLI tool, `logging.info()` at default log level won't write to stdout unless a handler is configured. The "fix" silently broke the program's intended user-facing output. BugHound had no way to distinguish diagnostic prints from intentional output prints.

---

### Failure 2: Risk assessor permits auto-fix of a high-severity issue at medium score

**Snippet:**
```python
def f():
    try:
        return 1
    except:
        return 0
```
**Fixed by heuristic fixer:**
```python
def f():
    try:
        return 1
    except Exception as e:
        # [BugHound] log or handle the error
        return 0
```

**What went wrong:** The risk score lands at 55 (medium), so `should_autofix` is False — which is correct. However, the risk score is 55 only because of the high-severity deduction (-40) and the bare-except modification (-5). Without that coincidence, a different high-severity issue could score 60 and still land in "medium" territory, implying the boundary between medium and high is somewhat arbitrary and not calibrated to actual change risk.

---

## 6) Heuristic vs Gemini comparison

| Dimension | Heuristic mode | Gemini mode |
|---|---|---|
| Issues detected | Fixed set of 3 pattern-matching rules | Richer, context-aware; catches issues like division by zero, missing type checks, misleading variable names |
| False positives | Very low (rules are explicit) | Moderate; model sometimes flags style choices as bugs |
| Issue consistency | 100% reproducible | Non-deterministic; same code can produce different issue counts across runs |
| Fix quality | Mechanical but safe | More readable, but sometimes over-edits or changes control flow |
| Risk scorer agreement | Scores track actual changes reliably | Scores can understate risk if the LLM makes many small changes spread across the file |

Gemini mode detected that `mixed_issues.py` had a division-by-zero risk (`x / y` with no check for `y == 0`), which the heuristics completely missed. On the other hand, the Gemini fix for `print_spam.py` rewrote more of the file than necessary, adding a `logging.basicConfig()` call that the heuristic fixer never adds.

---

## 7) Human-in-the-loop decision

**Scenario:** The LLM rewrites a function with a `return` statement inside a loop — changing `return` to `break` + a variable — which preserves the final value but alters early-exit behavior in subtle ways. The diff looks small; the risk scorer sees no removed returns (the word `return` is still present) and assigns a low risk level.

**Trigger to add:** Detect when the number of `return` statements in the fixed code differs from the original, not just whether the word `return` is present. If the count changes, flag for human review regardless of overall risk score.

**Where to implement it:** `reliability/risk_assessor.py`. It's a structural check like the existing ones, and keeping all guardrail logic in one place makes the system easier to audit.

**Message to show the user:** "The number of return paths changed. This may alter control flow. Auto-fix disabled — please review before applying."

---

## 8) Improvement idea

**New guardrail: validate LLM issue severity at ingestion time**

Currently, if the LLM returns an issue with `severity: "Critical"` or `severity: "Blocker"` (values the risk assessor doesn't recognize), those issues silently pass through `_normalize_issues()` with their string value intact, but `assess_risk()` ignores unrecognized severities and assigns no penalty. The net result is that a hallucinated severity label causes the risk score to be artificially high, and the agent may auto-fix something it shouldn't.

The fix (already implemented in this fork): `_validate_llm_issues()` in `bughound_agent.py` rejects any issue whose `severity` field is not in `{"low", "medium", "high"}` (case-insensitive) or whose `msg` is empty. Dropped issues are logged so the user can see that the LLM produced partially unusable output. This is a small, low-complexity change (one filter function, 5 lines) that closes a silent failure mode without making the agent refuse to run or become more aggressive in falling back to heuristics.
