-- BL-008C.23 -- idempotent swing-analysis usage charging.
--
-- PREPARED, NOT EXECUTED against production. This migration closes a
-- real exactly-once-accounting gap in the existing
-- increment_free_swing_usage() RPC: if a client's network connection
-- drops AFTER the RPC has committed server-side but BEFORE the client
-- receives the success response, the client cannot tell "it failed" from
-- "it succeeded but the ack was lost" -- and increment_free_swing_usage()
-- itself has no way to recognize a retried call as the same logical
-- attempt, so a naive client-side retry would double-charge.
--
-- Fix: a per-attempt idempotency key, generated once by the client (see
-- subscription_storage.py's new increment_free_swing_count_idempotent),
-- recorded in a small append-only ledger with a UNIQUE(user_id,
-- idempotency_key) constraint. Retrying the exact same attempt (same
-- key) is then a guaranteed no-op on the second call -- the ledger
-- insert hits the unique constraint and is skipped, and the RPC returns
-- the current (unchanged) count instead of incrementing again.
--
-- This is deliberately the SMALLEST schema change that achieves real
-- exactly-once semantics: one new table, one new function. It does NOT
-- replace or alter usage_counters or increment_free_swing_usage() --
-- both are left exactly as they are, so nothing already deployed can
-- break if this migration is never applied, and existing code calling
-- the old RPC keeps working unchanged. The new Python code in this same
-- commit tries the new, idempotent RPC first and transparently falls
-- back to the old one if this migration has not been applied yet to a
-- given environment (detected by Postgres/PostgREST's own "function
-- does not exist" error, 42883 / PGRST202 -- never by guessing).

create table if not exists public.swing_analysis_charges (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null references auth.users(id) on delete cascade,
    idempotency_key text not null,
    created_at      timestamptz not null default now(),
    unique (user_id, idempotency_key)
);

comment on table public.swing_analysis_charges is
    'BL-008C.23: append-only ledger of swing-analysis usage charges, '
    'keyed by a client-generated per-attempt idempotency key. Exists '
    'only so increment_free_swing_usage_idempotent() can recognize a '
    'retried call as the same attempt and avoid double-charging. Never '
    'written to directly by client code -- only through that '
    'SECURITY DEFINER function.';

alter table public.swing_analysis_charges enable row level security;

-- Same tamper-resistance pattern as usage_counters: a SELECT-only
-- policy scoped to the owning user. No INSERT/UPDATE/DELETE policy for
-- any client role -- writes are only possible through the RPC below.
drop policy if exists swing_analysis_charges_select_self on public.swing_analysis_charges;
create policy swing_analysis_charges_select_self
    on public.swing_analysis_charges
    for select
    using (auth.uid() = user_id);

create or replace function public.increment_free_swing_usage_idempotent(p_idempotency_key text)
returns int
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
    v_user_id      uuid;
    v_new_charge   boolean;
    v_count        int;
begin
    v_user_id := auth.uid();
    if v_user_id is null then
        raise exception 'Not signed in.' using errcode = '28000';
    end if;

    if p_idempotency_key is null or length(trim(p_idempotency_key)) = 0 then
        raise exception 'Missing idempotency key.' using errcode = '22023';
    end if;

    with ins as (
        insert into public.swing_analysis_charges (user_id, idempotency_key)
            values (v_user_id, p_idempotency_key)
        on conflict (user_id, idempotency_key) do nothing
        returning 1
    )
    select exists(select 1 from ins) into v_new_charge;

    if v_new_charge then
        insert into public.usage_counters (user_id, free_swings_used)
            values (v_user_id, 1)
        on conflict (user_id) do update
            set free_swings_used = public.usage_counters.free_swings_used + 1,
                updated_at       = now()
            returning free_swings_used into v_count;
    else
        -- Same attempt, already charged (a retried/duplicate call) --
        -- idempotent no-op. Return the CURRENT count unchanged, not an
        -- error, so a legitimate retry still gets a normal-looking
        -- success response instead of surfacing an internal detail.
        select free_swings_used into v_count
            from public.usage_counters
            where user_id = v_user_id;
        v_count := coalesce(v_count, 0);
    end if;

    return v_count;
end;
$function$;

comment on function public.increment_free_swing_usage_idempotent(text) is
    'BL-008C.23: idempotent counterpart to increment_free_swing_usage(). '
    'Same auth.uid()-derived user, same usage_counters table -- adds '
    'only a per-attempt idempotency key so a retried call (lost ack, '
    'client-side retry) cannot double-charge.';

grant execute on function public.increment_free_swing_usage_idempotent(text) to authenticated;
-- Deliberately NOT granted to anon, unlike some of the pre-existing
-- RPCs flagged in BL-008C.22's advisor review -- this one has no reason
-- to be callable before sign-in (it always raises for a null auth.uid()
-- anyway, but there's no reason to grant it at all).
