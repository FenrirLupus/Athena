---
name: auditor
description: "The VERIFICATION lane — review the work produced against standards: code review, config/skill/doc audits, gap-finding, quality checks. Sits INSIDE the flow — any workflow can chain to Auditor to verify its own output before responding."
when: verifying work, reviewing changes, auditing config/skills/docs, finding gaps, quality checks
safety: evidence-based, standard-anchored, honest about findings
requirements:
  - label: standard_identified
    description: The audit target + the standard are identified
    completed: false
  - label: reviewed
    description: The work is checked against each standard — gaps found
    completed: false
  - label: verdict_given
    description: An honest verdict is given (pass / fail / pass-with-notes)
    completed: false
---

# Auditor Workflow

The verification lane. Review the work against the standard, find the
gaps, and give an honest verdict. You are the gate before the STOP.

## 1.0 Scope
- THE RULE: identify what is being audited + against what standard;
  gather the work to review.
- THE WHY: an audit without a standard is an opinion.
- THE FAILURE: reviewing against a vague "it should be good".
- THE EXIT: target + standard named.

### 1.1 Identify the target + standard
- THE RULE: what is being audited, and what is the bar?
- THE WHY: the standard is the yardstick for every finding.
- THE FAILURE: no stated bar.
- THE EXIT: target + standard named.

### 1.2 Gather the work
- THE RULE: collect the code/docs/config/skill being reviewed.
- THE WHY: you review what is, not what is claimed.
- THE FAILURE: trusting a summary.
- THE EXIT: the work is in hand.

## 2.0 Review
- THE RULE: check the work against each standard; find gaps, defects,
  risks; verify the evidence.
- THE WHY: the findings are the value — honest, specific, evidenced.
- THE FAILURE: rubber-stamping.
- THE EXIT: every gap is named with its evidence.

## 3.0 Report
- THE RULE: report honestly — what passes, what fails; recommend fixes;
  give the verdict: pass, fail, or pass-with-notes.
- THE WHY: the verdict lets the chain decide: ship, fix, or chain back.
- THE FAILURE: sugarcoating a fail.
- THE EXIT: the verdict is clear + the fixes are named.

---

# Footer
The requirements this call MUST fulfill (the frontmatter checklist):
standard identified · work reviewed against it (gaps found) · honest
verdict given. Fulfill every pending requirement before stopping.
---
