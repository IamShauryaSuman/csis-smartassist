BEGIN;

CREATE TABLE public.roles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE public.role_permissions (
    role_id TEXT NOT NULL REFERENCES public.roles(id) ON DELETE CASCADE,
    permission_id TEXT NOT NULL,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE public.user_roles (
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    role_id TEXT NOT NULL REFERENCES public.roles(id),
    assigned_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id)
);

INSERT INTO public.roles (id, name) VALUES
    ('booking_admin', 'Booking Admin'),
    ('knowledge_admin', 'Knowledge Admin'),
    ('super_admin', 'Super Admin');

INSERT INTO public.role_permissions (role_id, permission_id) VALUES
    ('booking_admin', 'bookings.manage'),
    ('knowledge_admin', 'knowledge.manage'),
    ('super_admin', 'bookings.manage'),
    ('super_admin', 'knowledge.manage'),
    ('super_admin', 'roles.manage');

-- Preserve existing responsibilities without granting role-assignment powers.
INSERT INTO public.user_roles (user_id, role_id)
SELECT p.id, r.id FROM public.profiles p CROSS JOIN public.roles r
WHERE p.is_admin = TRUE AND r.id IN ('booking_admin', 'knowledge_admin');

CREATE FUNCTION public.has_permission(permission_name TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.user_roles ur
        JOIN public.role_permissions rp ON rp.role_id = ur.role_id
        WHERE ur.user_id = auth.uid() AND rp.permission_id = permission_name
    );
$$;
REVOKE ALL ON FUNCTION public.has_permission(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.has_permission(TEXT) TO authenticated;

ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;

GRANT SELECT ON public.roles, public.role_permissions, public.user_roles TO authenticated;
GRANT ALL ON public.roles, public.role_permissions, public.user_roles TO service_role;

CREATE POLICY "Authenticated users can read roles" ON public.roles
    FOR SELECT TO authenticated USING (TRUE);
CREATE POLICY "Authenticated users can read role permissions" ON public.role_permissions
    FOR SELECT TO authenticated USING (TRUE);
CREATE POLICY "Users can read relevant assignments" ON public.user_roles
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() OR public.has_permission('roles.manage'));

-- Booking mutations go through the API so status changes cannot skip Calendar.
DROP POLICY "Users can insert own bookings" ON public.bookings;
DROP POLICY "Admins can manage all bookings" ON public.bookings;
CREATE POLICY "Booking managers can read all bookings" ON public.bookings
    FOR SELECT TO authenticated USING (public.has_permission('bookings.manage'));
REVOKE INSERT, UPDATE, DELETE ON public.bookings FROM anon, authenticated;

DROP POLICY "Admins can view all profiles" ON public.profiles;
CREATE POLICY "Managers can read profiles" ON public.profiles
    FOR SELECT TO authenticated
    USING (public.has_permission('bookings.manage') OR public.has_permission('roles.manage'));

DROP POLICY "Admins can manage rag_files" ON public.rag_files;
DROP POLICY "Admins can manage rag_chunks" ON public.rag_chunks;
CREATE POLICY "Knowledge managers can manage files" ON public.rag_files
    FOR ALL TO authenticated
    USING (public.has_permission('knowledge.manage'))
    WITH CHECK (public.has_permission('knowledge.manage'));
CREATE POLICY "Knowledge managers can manage chunks" ON public.rag_chunks
    FOR ALL TO authenticated
    USING (public.has_permission('knowledge.manage'))
    WITH CHECK (public.has_permission('knowledge.manage'));

DROP POLICY "Admins can manage rooms" ON public.rooms;
CREATE POLICY "Booking managers can manage rooms" ON public.rooms
    FOR ALL TO authenticated
    USING (public.has_permission('bookings.manage'))
    WITH CHECK (public.has_permission('bookings.manage'));

DROP FUNCTION public.is_admin();
ALTER TABLE public.profiles DROP COLUMN is_admin;

-- These RPCs are backend-only: arbitrary target/actor IDs must never be public.
CREATE FUNCTION public.get_user_access(target_user_id UUID)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT jsonb_build_object(
        'roles', COALESCE((
            SELECT jsonb_agg(ur.role_id ORDER BY ur.role_id)
            FROM public.user_roles ur WHERE ur.user_id = target_user_id
        ), '[]'::jsonb),
        'permissions', COALESCE((
            SELECT jsonb_agg(p.permission_id ORDER BY p.permission_id) FROM (
                SELECT DISTINCT rp.permission_id FROM public.user_roles ur
                JOIN public.role_permissions rp ON rp.role_id = ur.role_id
                WHERE ur.user_id = target_user_id
            ) p
        ), '[]'::jsonb)
    );
$$;

CREATE FUNCTION public.get_permission_recipients(permission_name TEXT)
RETURNS TABLE(email TEXT) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = ''
AS $$
    SELECT DISTINCT p.email FROM public.profiles p
    JOIN public.user_roles ur ON ur.user_id = p.id
    JOIN public.role_permissions rp ON rp.role_id = ur.role_id
    WHERE rp.permission_id = permission_name AND p.email <> '';
$$;

CREATE FUNCTION public.set_user_roles(target_user_id UUID, requested_roles TEXT[], actor_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
BEGIN
    -- Serialize assignment changes, including permission checks and last-admin checks.
    PERFORM pg_advisory_xact_lock(80623008);
    IF NOT EXISTS (
        SELECT 1 FROM public.user_roles ur
        JOIN public.role_permissions rp ON rp.role_id = ur.role_id
        WHERE ur.user_id = actor_id AND rp.permission_id = 'roles.manage'
    ) THEN
        RAISE EXCEPTION 'Permission denied.' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.profiles WHERE id = target_user_id) THEN
        RAISE EXCEPTION 'User not found.' USING ERRCODE = 'P0002';
    END IF;
    IF requested_roles IS NULL OR EXISTS (
        SELECT 1 FROM unnest(requested_roles) r(id)
        WHERE r.id IS NULL OR NOT EXISTS (SELECT 1 FROM public.roles WHERE id = r.id)
    ) THEN
        RAISE EXCEPTION 'Unknown role.' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.user_roles WHERE user_id = target_user_id AND role_id = 'super_admin'
    ) AND NOT ('super_admin' = ANY(requested_roles)) AND (
        SELECT count(*) FROM public.user_roles WHERE role_id = 'super_admin'
    ) = 1 THEN
        RAISE EXCEPTION 'Cannot remove the last super admin.' USING ERRCODE = '23514';
    END IF;

    DELETE FROM public.user_roles
    WHERE user_id = target_user_id AND NOT (role_id = ANY(requested_roles));
    INSERT INTO public.user_roles (user_id, role_id, assigned_by)
    SELECT target_user_id, r.id, actor_id FROM (SELECT DISTINCT unnest(requested_roles) AS id) r
    ON CONFLICT (user_id, role_id) DO NOTHING;
END;
$$;

REVOKE ALL ON FUNCTION public.get_user_access(UUID) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.get_permission_recipients(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.set_user_roles(UUID, TEXT[], UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_user_access(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_permission_recipients(TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.set_user_roles(UUID, TEXT[], UUID) TO service_role;

COMMIT;
