BEGIN;

-- A pending reservation survives an ambiguous Calendar write. A worker can
-- reclaim the same booking after a crash without opening its slot to others.
ALTER TABLE public.bookings
    ADD COLUMN approval_token uuid,
    ADD COLUMN approval_started_at timestamptz;

CREATE FUNCTION public.claim_booking_decision(booking_id uuid, worker_token uuid, approving boolean)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    target public.bookings%ROWTYPE;
    recovering boolean;
BEGIN
    -- Use the same lock order as replacement creation: requester, booking, room.
    PERFORM pg_advisory_xact_lock(9032, hashtext(user_id::text))
        FROM public.bookings WHERE id = booking_id;
    SELECT * INTO STRICT target FROM public.bookings WHERE id = booking_id FOR UPDATE;
    IF target.status <> 'pending' THEN
        RAISE EXCEPTION 'Booking already %.', target.status USING ERRCODE = 'P0001';
    END IF;
    IF target.is_locked AND target.locked_at > now() - interval '15 minutes' THEN
        RAISE EXCEPTION 'Booking is being edited.' USING ERRCODE = 'P0001';
    END IF;
    IF target.approval_started_at > now() - interval '5 minutes' THEN
        RAISE EXCEPTION 'Another decision is in progress.' USING ERRCODE = 'P0001';
    END IF;
    IF approving THEN
        PERFORM pg_advisory_xact_lock(9031, hashtext(target.room_id));
        IF EXISTS (
            SELECT FROM public.bookings b WHERE b.id <> booking_id
            AND b.room_id = target.room_id
            AND (b.status = 'approved' OR b.approval_token IS NOT NULL)
            AND b.start_time < target.end_time AND b.end_time > target.start_time
        ) THEN
            RAISE EXCEPTION 'Room already reserved for this time.' USING ERRCODE = 'P0001';
        END IF;
    END IF;
    recovering := target.approval_token IS NOT NULL;
    UPDATE public.bookings SET approval_token = worker_token,
        approval_started_at = now(), is_locked = false, locked_at = NULL, locked_by = NULL
        WHERE id = booking_id RETURNING * INTO target;
    RETURN jsonb_build_object('booking', to_jsonb(target), 'recovering', recovering);
END;
$$;

CREATE FUNCTION public.create_pending_booking(
    requester uuid, room_key text, room_label text, booking_title text, details text,
    starts timestamptz, ends timestamptz, accepted_use boolean, accepted_availability boolean
) RETURNS SETOF public.bookings LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(9032, hashtext(requester::text));
    IF EXISTS (SELECT FROM public.bookings WHERE user_id = requester
               AND status = 'pending' AND approval_token IS NOT NULL
               AND start_time < ends AND end_time > starts) THEN
        RAISE EXCEPTION 'An overlapping request is being decided.' USING ERRCODE = 'P0001';
    END IF;
    UPDATE public.bookings SET status = 'expired', is_locked = false, locked_at = NULL, locked_by = NULL
        WHERE user_id = requester AND status = 'pending' AND approval_token IS NULL
        AND start_time < ends AND end_time > starts;
    RETURN QUERY INSERT INTO public.bookings
        (user_id, room_id, room_name, title, description, start_time, end_time,
         acknowledged_use, acknowledged_availability)
        VALUES (requester, room_key, room_label, booking_title, details, starts, ends,
                accepted_use, accepted_availability) RETURNING *;
END;
$$;

-- Older syncs could advance the checksum after publishing only some chunks.
-- Rebuild each legacy file once; a successful atomic swap marks it complete.
ALTER TABLE public.rag_files ADD COLUMN index_complete boolean NOT NULL DEFAULT false;

-- Compare against the revision read before downloading and embedding. Keep
-- the old file and all its chunks visible until the replacement commits.
CREATE FUNCTION public.replace_rag_index(
    drive_id text, file_name text, file_mime text, revision text,
    expected_revision text, chunks jsonb
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    target_id uuid;
    current_revision text;
BEGIN
    IF chunks IS NULL OR jsonb_typeof(chunks) <> 'array' THEN
        RAISE EXCEPTION 'Expected an array of document chunks.';
    END IF;
    PERFORM pg_advisory_xact_lock(9033, hashtext(drive_id));
    SELECT id, md5_checksum INTO target_id, current_revision
        FROM public.rag_files WHERE gdrive_id = drive_id FOR UPDATE;
    IF current_revision IS DISTINCT FROM expected_revision THEN
        RAISE EXCEPTION 'Index changed during sync. Scan again.' USING ERRCODE = '40001';
    END IF;
    INSERT INTO public.rag_files (gdrive_id, name, mime_type, md5_checksum, index_complete)
        VALUES (drive_id, file_name, file_mime, revision, true)
        ON CONFLICT (gdrive_id) DO UPDATE SET name = excluded.name, mime_type = excluded.mime_type,
            md5_checksum = excluded.md5_checksum, index_complete = true, updated_at = now()
        RETURNING id INTO target_id;
    DELETE FROM public.rag_chunks WHERE file_id = target_id;
    INSERT INTO public.rag_chunks (file_id, content, embedding)
        SELECT target_id, chunk->>'content', (chunk->>'embedding')::vector(768)
        FROM jsonb_array_elements(chunks) AS chunk;
    RETURN target_id;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_booking_decision(uuid, uuid, boolean),
    public.create_pending_booking(uuid,text,text,text,text,timestamptz,timestamptz,boolean,boolean),
    public.replace_rag_index(text,text,text,text,text,jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_booking_decision(uuid, uuid, boolean),
    public.create_pending_booking(uuid,text,text,text,text,timestamptz,timestamptz,boolean,boolean),
    public.replace_rag_index(text,text,text,text,text,jsonb) TO service_role;
COMMIT;
