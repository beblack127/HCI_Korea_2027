# Post-study refinements

## Changes

- A direct definition question about a specific term can be recorded as possible concept-support need. It remains distinct from an expressed difficulty and does not imply frustration or confusion. The original input is kept as evidence.
- The first user input is retained as `initial_interest`; `current_direction` can evolve independently.
- Explicit requests to summarize the current session are routed to a deterministic, evidence-grounded recap. The request does not confirm readiness or imply that the user selected the wrap-up action. No new candidate is required.
- Support plans now include `source`, identifying whether the current request, a user selection, an inferred state, or the default policy set the mode.
- Detailed and concept responses are capped at 900 characters. Their candidate list starts collapsed.
- Candidate repetition checks compare the new list against all response batches in the active direction context. A refinement chain at depth three or more asks for a narrower subproblem, situation, or scope. Repetition remains a system issue.
- The state panel groups the current direction, needed help, and next step in one summary card. Readiness, evidence, history, and detailed state remain folded. When evidence exists, `problem_basis` subject, focus, and context are shown as a path.

## Targeted Gemini replay

`scripts/replay_difficulty.py` reads the study database in read-only mode and re-estimates only user-input turns from the 11 non-empty sessions. It does not regenerate answers, perform literature searches, or write to the database. The quote-free comparison report is written outside the repository.

Replay used Gemini 3.5 Flash-Lite and the updated difficulty criteria (`11.18-v1`). It made 37 estimate calls for 37 user-input turns. The saved pre-change estimates had `unknown` difficulty status on all 37 turns; the updated estimates also had `unknown` on all 37. The replay found no exact definition-question inputs, no direct difficulty expressions, and no recorded difficulty follow-up outcomes. The inputs included one general concept-support request. Consequently, these study logs do not establish that the new concept-question rule or resolution inference improves detection. The replay specifically checks difficulty inference; it does not validate the later changes to session recap, panel layout, or recommendation repetition handling.

The recorded behavior patterns were candidate revisits and search-plus-review activity. Those actions alone remain insufficient to infer difficulty, since they can reflect ordinary exploration. Synthetic regression checks cover a definition question, ordinary information requests, and evidence-backed follow-up changes.

Report: `/private/tmp/sense_difficulty_replay.json` (local only; no utterance text or API credentials).

## Verification notes

- Full Python suite: 155 tests passed with `STUDY_ACCESS_CODE` unset for the test process. The local study-code gate otherwise returns `401` before the tests reach the API, so leave the real participant configuration unchanged.
- Frontend suite: 33 tests passed. Python compilation and `git diff --check` passed.
- This retrospective replay compares model output on historical input; it does not establish how participants would have reacted to the revised system.
