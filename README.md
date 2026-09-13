# MKAY Web3 Opportunity Radar

This is the first, deliberately narrow pipeline for the Radar:

```text
Airdrops.io public listing → public guide pages → clean/normalize → deduplicate → database → Telegram review queue
```

It is **review-first**: every imported opportunity has `REVIEW` status. The collector does not publish to a community channel automatically, and it does not follow the site's `/visit/` redirect links. A human should verify every link and never enter a seed phrase.

## What is included

- A polite Airdrops.io collector using `requests` + BeautifulSoup; default limit: 10 individual public guides per run.
- Normalization into one opportunity record (title, project, category, chain, actions, reward, source status, link).
- Deduplication via canonical source URL, content hash, and a conservative title-similarity fallback.
- Local SQLite by default, with a Supabase/PostgreSQL storage adapter when credentials are added.
- Telegram HTML messages prepared for a private review chat; sending is explicit via `--send`.
- A small test suite for normalization, deduplication, and review formatting.

## Free-first setup

1. Install Python 3.11+ and create a virtual environment.
2. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`. Do not commit it.
4. Start locally (no account required):

   ```powershell
   $env:PYTHONPATH = "src"
   python -m mkay_radar.cli collect-airdrops
   python -m mkay_radar.cli review --limit 5
   ```

The local database is created at `data/radar.db` and is ignored by Git.

## Move to Supabase when ready

1. Create a free Supabase project.
2. Run [`sql/schema.sql`](sql/schema.sql) in its SQL Editor.
3. In `.env`, set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`; remove/ignore `RADAR_DATABASE_URL`.
4. Run the same commands. The service-role key is server-only—do not put it in a browser or repository.

Supabase's free tier is suitable for V1, but free projects can pause after inactivity. Keep a small scheduled run only after the local pipeline is trusted.

## Telegram review delivery

Create a bot using BotFather, add it to a private review chat, and set `TELEGRAM_BOT_TOKEN` plus `TELEGRAM_REVIEW_CHAT_ID` in `.env`. Then use:

```powershell
python -m mkay_radar.cli review --limit 5 --send
```

`--send` is intentional: it sends only unsent records currently in `REVIEW`, each with **Approve** and **Reject** buttons. Start by running the command without `--send` to inspect exact messages. After choosing a button in Telegram, apply that decision to the database with:

```powershell
python -m mkay_radar.cli process-telegram-actions
```

Approved and rejected records no longer appear in the review queue. This is still not public posting; it is the private editorial decision step.

## Telegram automation and broadcasts

Run the listener while the bot should respond immediately to button taps:

```powershell
python -m mkay_radar.cli telegram-listen
```

For each destination group, add the bot as an admin, then have a human administrator send `/enable_broadcast` in that group. Telegram does not provide bots a list of every group they belong to, so this one-time registration is required. When you approve a review in the private group, the listener updates its status and sends an alpha post to every registered group. The listener must remain running; deploy it to an always-on host before relying on it when your computer is off.

## Source-access boundaries

The collector reads the site's public home/listing and a limited number of linked public guide pages. It follows `robots.txt` exclusions, avoids `/visit/`, search, admin, login, and any protected areas, and waits between guide requests. Before scheduling it, configure a real project contact in `RADAR_USER_AGENT` and re-check the source's current policies; source layouts and permissions can change.

## What is intentionally not included yet

No paid proxy, hosting, AI API, automatic public posting, browser automation, or additional source. The next source should be added only after this flow proves stable across several manual reviews.
