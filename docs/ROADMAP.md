# hive — Roadmap & εκκρεμότητες

TUI orchestrator (Python/Textual πάνω σε tmux) για πολλαπλές παράλληλες συνεδρίες Claude Code.
Λεπτομέρειες ανά φάση: `docs/superpowers/specs/`, `docs/superpowers/plans/`.

## Φάσεις

| Φάση | Τι | Κατάσταση |
|---|---|---|
| 1 | Scaffold, config, dashboard, tmux sessions (2026-05-10) | ✅ |
| 2 | Hook-based state detection (WORKING/WAITING/BOOTSTRAPPING) | ✅ |
| 3 | Codex findings cleanup: safety module, name validation, atomic writes, hardening | ✅ |
| 4 | Session synopsis (LLM περίληψη συνεδρίας, cache, Vertex/Anthropic) | ✅ |
| 5 | UI polish: splash, non-intrusive hooks, `uninstall-hooks`, σταθερή σειρά λίστας, απλοποίηση tmux bindings | ✅ |
| 6 | hive-go: rewrite σε Go (Bubbletea + PTY), χωρίς tmux, 19 tasks | 🔄 tasks 1–18 υλοποιημένα στο master του `~/Projects/hive-go` (2026-05-17, τελευταίο `5565ab1`)· απομένει Task 19 (integration/verification, χωρίς commit) και uncommitted αλλαγές στο hive-go |

## Αποφάσεις (να μην ξαναρωτηθούν)

- Το tmux προκαλεί παρεμβολές με το Claude Code → rewrite σε Go χωρίς tmux (spec 2026-05-17).
- hive-go: feature parity με το τρέχον hive, χωρίς νέα features στο v1.
- hive-go: συμβατό με υπάρχον config και hook state files· single binary (hive + hive-hook).
- Εκτός v1 (non-goals): image paste, Windows.
- Το hive-go ζει σε ξεχωριστό repo (`~/Projects/hive-go/`, βάσει spec).
- Αλλαγή ονόματος ενεργών συνεδριών απαγορεύεται (αποφυγή desync).
- Hooks: ρητά absolute paths, μη παρεμβατικά.

## Εκκρεμότητες χρήστη

—

## Εκκρεμότητες Claude

- [ ] Υλοποίηση hive-go βάσει `docs/superpowers/plans/2026-05-17-hive-go.md` (tasks 1–18 ✅ 2026-05-17, `76882a9`..`5565ab1` στο hive-go· απομένει Task 19)
- [ ] hive-go Task 19: full integration test (`go test ./...`, build, `go vet`, smoke test) και commit των εκκρεμών αλλαγών
- [ ] Untracked `~/` στο root του repo (πιθανώς τυχαίος φάκελος) — έλεγχος/καθαρισμός (εκτίμηση)
- [ ] Απόφαση για τύχη του Python hive μετά το hive-go (συντήρηση ή αποσυρση) (εκτίμηση)

## Ιδέες / μελλοντικά

- Image paste στο hive-go (δυνατό λόγω άμεσης πρόσβασης σε PTY).
- Janitor για παλιά `.json.tmp` αρχεία στο hook state dir μετά από crash (`docs/superpowers/specs/2026-05-11-codex-findings-design.md`).
- Διάσπαση του `app.py` σε μικρότερα modules (`docs/superpowers/specs/2026-05-11-codex-findings-design.md`, non-goals).

## Ιστορικό

- 2026-05-10: project scaffold και config module
- 2026-05-11: hook-based state detection, codex findings cleanup, session synopsis (specs/plans)
- 2026-05-17: hive-go design spec και implementation plan
- 2026-10-24: ROADMAP δημιουργήθηκε από ανασκόπηση repo/memory
- 2026-10-24: tidy — φάση 6 σε 🚧 (το hive-go έχει commits στο `~/Projects/hive-go`), προστέθηκαν 2 ιδέες από το codex-findings spec
- 2026-10-24: tidy — φάση 6 σε 🔄 (tasks 1–18 του hive-go plan στο master του hive-go· απομένει Task 19), νέα todo για αυτό
