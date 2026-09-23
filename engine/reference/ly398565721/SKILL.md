---
name: high-school-timetable
description: Use when generating or revising a Chinese high-school timetable or its early-reading, quiet-study, and evening-study duties from appointment workbooks and rule documents.
---

# High-school Timetable

Produce a genuinely solved, independently validated timetable. Do not infer or silently relax rules.

## Start every run

1. Read the appointment workbook, rule document, and any requested format references without modifying them.
2. Read [the constraint catalog](references/constraint-catalog.md) and prepare a complete `本次约束确认表` containing every rule's identifier, default state, current state, value, source, and whether confirmation is required.
3. Ask every unanswered item in [run intake](references/run-intake.md), one at a time. Do not solve until all required choices are confirmed.
4. Use the matching school adapter. If the workbook layout is unknown, audit it first and stop for confirmation rather than guessing columns.

## Solve and validate

1. Solve formal lessons with CP-SAT and independently validate all active hard rules.
2. Freeze the validated formal schedule before duty assignment. Duties never move formal lessons.
3. Solve duties, then independently validate all duty hard rules.
4. If either stage is infeasible, report the violated rule set and the minimum candidate relaxation. Wait for user approval before rerunning with any relaxation.

## Deliver

Read [the output contract](references/output-contract.md). Export exactly four workbooks from the same frozen formal-schedule and duty-roster JSON. Use the spreadsheet workflow for authoring and inspect key cells, formula errors, and rendered layout before delivery.

## Boundaries

- Source and reference workbooks remain read-only.
- Hard rules must exist in both the solver and its independent validator.
- O04 is deleted: do not prefer the third evening duty for multi-class or non-headteacher teachers.
- Every run records its confirmed rules and solver/validation reports in its own run directory.
