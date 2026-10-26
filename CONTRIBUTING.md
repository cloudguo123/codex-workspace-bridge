# Contributing

Use Python 3.11+, run `python3 -B -m unittest discover -s tests -v`.

New App layouts need anonymized/synthetic schema fixtures, explicit table/path/credential rules and failure tests before enabling writes. Do not attach real user databases or reproduce secrets in a fixture. Test conflict preservation and interrupted commit/rollback, not just the happy path.

Keep the default offline/read-only workflow. Any future transport or App API adapter must state its credential scope, stop conditions and whether it mutates or starts a task. Avoid claims of official support or full UI continuity without direct verification.
