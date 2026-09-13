-- Run once in the Supabase SQL editor. This is standard PostgreSQL.
create table if not exists opportunities (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  project text not null,
  category text not null check (category in ('AIRDROP', 'TESTNET', 'JOB', 'BOUNTY', 'GRANT', 'QUEST', 'CAMPAIGN', 'CONTEST', 'HACKATHON', 'AMBASSADOR', 'NEW_PROJECT')),
  description text,
  requirements text,
  chain text,
  reward text,
  deadline timestamptz,
  url text not null,
  canonical_url text not null unique,
  source text not null,
  source_status text,
  risk_level text not null default 'REVIEW_REQUIRED',
  score numeric(4,2) not null default 0,
  status text not null default 'REVIEW' check (status in ('NEW', 'REVIEW', 'APPROVED', 'REJECTED', 'POSTED', 'EXPIRED')),
  content_hash text not null,
  discovered_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  review_sent_at timestamptz,
  posted_at timestamptz
);

-- Safe when applying this upgrade to a project created before review buttons.
alter table opportunities add column if not exists review_sent_at timestamptz;

create table if not exists source_records (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references opportunities(id) on delete cascade,
  source text not null,
  source_url text not null,
  collected_at timestamptz not null default now(),
  raw_payload jsonb not null,
  unique(source, source_url)
);

-- Groups that have explicitly enabled approved-alpha broadcasts.
create table if not exists telegram_chats (
  chat_id bigint primary key,
  title text,
  chat_type text not null,
  is_active boolean not null default true,
  registered_at timestamptz not null default now()
);

create index if not exists opportunities_review_idx on opportunities (status, discovered_at desc);

-- Keep the service-role key on the collector only. Do not expose it in a web app.
