# Local agent guidance

- Use subagents for independent, self-contained work during long or multi-file implementations to reduce main-session context pollution.
- Batch genuinely independent tasks in one parallel delegation call; state shared interfaces and constraints up front.
- Main agent owns integration, shared-file edits, and final verification.
- Give each subagent exact targets, acceptance criteria, and instruction to skip formatters, linters, and project-wide test suites.
- Do not delegate trivial edits or tasks with a shared mutation boundary.
