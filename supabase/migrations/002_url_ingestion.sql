-- Apply after 001. Existing upload records retain their meaning.
alter table public.analyses add column ingestion jsonb not null default '{"kind":"upload"}'::jsonb;
alter table public.analyses drop constraint analyses_status_check;
alter table public.analyses add constraint analyses_status_check
  check (status in ('queued', 'downloading', 'extracting', 'transcribing', 'transcribed', 'failed'));
