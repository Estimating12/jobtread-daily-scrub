# Daily JobTread Denials & Approvals Scrub

Auto-generates a daily report of JobTread proposal approvals/denials, broken
down by Job Type (price point) and payer Program, and publishes it to a
free static webpage you can bookmark. Runs every morning with no one
triggering it.

## What it does

Every day at the scheduled time, GitHub runs `scripts/generate_scrub.py`,
which:
1. Calls JobTread's API directly for yesterday's closed proposals
   (`customerOrder` documents with status `approved` or `denied`)
2. Groups them by Job Type and by payer Program, flags high-denial-rate
   groups as "At risk"
3. Writes `docs/index.html` — the page you'll visit
4. Appends to `docs/history.json` so a day-over-day trend builds over time
5. Commits the updated files back to the repo, which GitHub Pages serves

## One-time setup (15 minutes)

### 1. Get a JobTread grant key
In JobTread: **Settings → Integrations/API** (naming may vary by plan) →
create an API grant key with read access to jobs, documents, and accounts.
Copy it — you'll only see it once.

### 2. Create a GitHub repository
- Create a **private** repo (e.g. `jobtread-daily-scrub`)
- Upload everything in this folder to it (or `git init` + push)

### 3. Add your grant key as a secret
In the repo: **Settings → Secrets and variables → Actions → New repository secret**
- Name: `JOBTREAD_GRANT_KEY`
- Value: the key from step 1

*(If your org id, Job Type field id, or payer field name differ from the
defaults baked into the script — see the top of `generate_scrub.py` — add
them as additional secrets or repo variables: `JOBTREAD_ORG_ID`,
`JOB_TYPE_FIELD_ID`, `PROGRAM_FIELD_NAME`.)*

### 4. Turn on GitHub Pages
**Settings → Pages** → Source: "Deploy from a branch" → Branch: `main`,
folder: `/docs` → Save.

GitHub will give you a URL like:
```
https://<your-github-username-or-org>.github.io/jobtread-daily-scrub/
```
That's your daily link. Bookmark it.

### 5. Run it once manually to confirm it works
**Actions tab → Daily JobTread Scrub → Run workflow** (this uses the
`workflow_dispatch` trigger). Check the run succeeds, then visit your Pages
URL — you should see today's report. After that it runs itself every day.

## Adjusting the schedule

Edit the `cron` line in `.github/workflows/daily-scrub.yml`. Cron times are
in UTC. For example, `"0 11 * * *"` runs at 7:00 AM ET (EDT).

## If a run fails

Check the **Actions** tab for the run's logs — the most common causes are
an expired/incorrect grant key, or JobTread being temporarily unreachable.
Failed runs don't overwrite yesterday's report, so the page just won't
update that day.

## Notes

- This does not use Claude or the Anthropic API at all — it's a plain
  Python script calling JobTread's own API, so there's nothing to keep
  paying for or re-authenticating.
- The page is public if your repo is public, or restricted to logged-in
  GitHub org members if you use GitHub Pages with a private repo + GitHub
  Enterprise, or you can put the repo/Pages behind your org's existing
  GitHub access controls.
