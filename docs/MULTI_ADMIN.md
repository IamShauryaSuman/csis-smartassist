# Administrator roles

| Role | Access |
| --- | --- |
| Booking Admin | All bookings, approval/rejection, room management, booking diagnostics and notifications |
| Knowledge Admin | Document sync/removal and RAG statistics |
| Super Admin | Both responsibilities above, plus role assignment |

Roles can be combined and apply across the department. Academic roles remain
profile information. In **Admin → Administrator Roles**, find a user by email
and select their roles; clearing all roles restores regular user access.

## Deployment

1. With migrations 001–007 already applied, back up the database and apply 008
   during a maintenance window. It removes `profiles.is_admin`, so the old API/UI
   cannot use the new schema.
   Existing admins keep Booking Admin and Knowledge Admin access.
2. Choose the first Super Admin, who must have completed onboarding. Run this as
   the database owner, replacing the email:

   ```sql
   DO $$
   DECLARE chosen_user UUID;
   BEGIN
       SELECT p.id INTO STRICT chosen_user FROM public.profiles p
       JOIN auth.users u ON u.id = p.id
       WHERE u.email = 'REPLACE_WITH_APPROVED_ADMIN@goa.bits-pilani.ac.in';
       INSERT INTO public.user_roles (user_id, role_id, assigned_by)
       VALUES (chosen_user, 'super_admin', chosen_user)
       ON CONFLICT (user_id, role_id) DO NOTHING;
   END;
   $$;
   ```

3. Deploy the new API and UI together. Assign remaining roles through the UI.
   Role management prevents removing the last Super Admin. The SQL above also
   recovers access after an out-of-band account deletion.

Rollback requires restoring the database backup and old API/UI together.

## Verification

Run `python -m pytest api/tests -q` from the root; run `npm test`, `npm run lint`,
`npm run build`, and `npx tsc --noEmit` from `ui/` after installing dependencies.

API permissions are checked on every protected request, including in local-auth
mode. RLS independently restricts database access; booking writes and role changes
must go through the API. Assignment changes are atomic and record the granting
user. The UI refreshes permissions on navigation, focus and self-assignment.

Tests cover API authorization, role editing, and real SQL policies/migrations in
isolated PGlite PostgreSQL. API tests mock Supabase; SQL tests supply its auth
schema and API roles. They do not exercise live Supabase services or concurrent
database connections and require no production credentials.
