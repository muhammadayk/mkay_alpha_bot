# MKAY Web3 Opportunity Radar

This is the first, deliberately narrow pipeline for the Radar:

```text
Airdrops.io public listing → public guide pages → clean/normalize → deduplicate → database → Telegram review queue
```

It is **review-first**: every imported opportunity has `REVIEW` status. The collector does not publish to a community channel automatically, and it does not follow the site's `/visit/` redirect links. A human should verify every link and never enter a seed phrase.

## What is included

- **Airdrops** — a polite Airdrops.io collector plus AirdropAlert's official RSS feed.
- Default limit: 10 records per source per run (`RADAR_MAX_ITEMS_PER_RUN`).
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
4. Start locally (no account required for Airdrops.io or AirdropAlert):

   ```powershell
   $env:PYTHONPATH = "src"
   python -m mkay_radar.cli collect-all
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

## Free always-on deployment

Use GitHub Actions for scheduled collection and the Cloudflare Worker in `worker/` for instant Telegram webhooks. The Worker replaces the local `telegram-listen` command once deployed. Follow the deployment steps provided with your Cloudflare account, set all four Worker secrets, then set Telegram's webhook to the Worker URL. Never commit `.env` or any secret.

## Source-access boundaries

The Airdrops.io collector reads only the site's public home/listing and a limited number of linked public pages, avoids the site's own sponsored/tracking redirect links (`/visit/`), search, admin, login, and any protected areas, and waits between page requests. Before scheduling it, configure a real project contact in `RADAR_USER_AGENT` (set it as a repo *variable*, not a secret, at Settings → Secrets and variables → Actions → Variables) and re-check the source's current policies; source layouts and permissions can change.

**A known limitation worth remembering:** some sites run bot-protection (often Cloudflare) that blocks requests from cloud/datacenter IP ranges — including GitHub Actions' shared runners — even with a normal User-Agent, while the same request succeeds fine from a home connection. If a collector works locally but returns `403 Forbidden` only in the scheduled workflow, this is the most likely cause, not a code bug, and isn't something a different User-Agent or retry logic reliably fixes. This is exactly why NFTCalendar.io was pulled back out (see below) rather than left failing silently in CI.

`collect-all` runs every source independently: one source's failure never stops the others, and prints which one failed rather than crashing (see `run_collectors` in `cli.py`). The workflow step does still exit non-zero when any configured source fails, so a genuine problem (e.g. Airdrops.io itself breaking, or bad Supabase credentials) is visible in the Actions tab rather than silently swallowed — but with only known-reliable sources currently wired in, this should stay quiet in normal operation.

## What is intentionally not included yet

No paid proxy, AI API, or browser automation. CryptoRank is intentionally excluded until it grants a suitable licence or written permission: its terms restrict automated extraction and republishing.

**NFT whitelist/mint opportunities** were briefly collected from NFTCalendar.io's public listing pages, but that source blocks requests from cloud/datacenter IP ranges (including GitHub Actions' shared runners) even with a normal User-Agent, so the scheduled workflow failed reliably even though the collector worked fine locally. Rather than leave a source that only works some of the time, it was pulled back out. If a cleaner path turns up later — an official API/RSS feed, or a self-hosted runner that uses a non-blocked IP — a matching collector can be added back the same way the others were.

**Web3 jobs/gigs** were briefly tried via web3.career's documented API, but the source stopped responding reliably and was pulled back out rather than shipped in a broken state. If a solid jobs/gigs source turns up later (official API or RSS, not a scrape of an undocumented endpoint), it can be added the same way the other collectors were.

**Bounties, quests, and campaigns** (Gitcoin, Galxe, Zealy, Layer3, Superteam Earn, and similar) are also not included yet. As of this writing, none of them offer a public RSS feed or a documented, ToS-compliant public API for third-party bots the way Airdrops.io and AirdropAlert do — most either require scraping their app's internal endpoints (undocumented, ToS status unclear) or a paid/partnered API. Rather than guess at an endpoint or risk violating a platform's terms, the options are:

1. Reach out to one of these platforms directly for official API/partner access, and a matching collector can be added the same way the other sources were (see `src/mkay_radar/sources/` for the pattern: a `collect()` method returning a list of `Opportunity` records).
2. Curate them manually into the Telegram review queue in the meantime — the review/approve/broadcast pipeline works the same regardless of where an opportunity came from.

If you get access to one of these (or another Web3-specific job, bounty, or new-project source) and want it wired in, open an issue or a PR describing the feed/API and its terms.
