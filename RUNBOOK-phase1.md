# Cold Outreach Autopilot — Runbook

Engine: `surven_outreach` (Python). Run all commands from `C:\dev\surven-outreach` using the venv:
`.venv\Scripts\python.exe -m surven_outreach.cli <command>`

Commands: `clean` · `scan` · `prepare` · `send --batch <file>` · `mark <email> <status>`

---

## One-time setup
- [x] ImprovMX SMTP password in `.env` (sending as joey@surven.ai) — verified.
- [ ] survengeo IMAP app password in `.env` (`IMAP_PASSWORD=`) — for the auto-stop. (2-Step Verification → App Password → enable IMAP on survengeo@gmail.com.)
- [ ] `clean` once to build the queue from the Prospects export.

## Daily procedure — Phase 1 (approve-first, ~1–2 weeks)

1. **Scan for replies first** (so we never email someone who answered):
   `... cli scan`
   → marks repliers/bounces/opt-outs, prints HOT REPLY lines. Reply to hot leads by hand within the hour (move to the live-audit call).

2. **Prepare today's batch:**
   `... cli prepare`
   → writes `data/batch-<date>.json` (up to 5 new + any due follow-ups; caps enforced; empty on Sundays).

3. **Personalize the new prospects.** For each batch item with `"needs_personal_line": true`, Claude writes the `[PERSONAL]` first line using this prompt, then sets the item's `"personal_line"` in the JSON (drop the line — leave it `""` — on `SKIP`):

   > You write ONE first line for a cold email to a local business. Input: business name, category, city, website, scraped "about" text. Do a quick check of the website/recent news for a real, recent signal (new location, award/press, hiring, high review count, new site/rebrand, seasonal). Output ONE line, ≤12 words, lowercase, no hype, no exclamation marks, referencing a TRUE specific recent thing. If there is no genuine signal and nothing truthful and specific to say, output exactly `SKIP`. Never invent facts.

4. **Approve.** Claude shows Joey the rendered batch; Joey edits/approves.

5. **Send:**
   `... cli send --batch data/batch-<date>.json`
   → sends threaded via ImprovMX SMTP, logs, advances each prospect, re-checks caps.

## Phase 2 — flip to auto (later)
- Set `AUTO_SEND=true`, ensure survengeo IMAP works.
- Register a daily scheduled task (Claude Code scheduled task / Windows Task Scheduler) running: `scan` → `prepare` → (personalize via Anthropic API) → `send`, then notify Joey.
- The volume caps make over-sending impossible regardless of trigger frequency. When steady-state volume wants to exceed the cap, that's the trigger to stand up a secondary sending domain (see the infra doc) before scaling.

## Statuses
`queued` → `active` (in sequence) → `completed` (all 5 sent) · or stopped: `replied` / `bounced` / `opted_out` (→ suppressed, never re-contacted).
