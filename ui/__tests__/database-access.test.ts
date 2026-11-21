// @vitest-environment node

import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { PGlite, type Transaction } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

const ids = {
  user: "00000000-0000-0000-0000-000000000001",
  booking: "00000000-0000-0000-0000-000000000002",
  knowledge: "00000000-0000-0000-0000-000000000003",
  combined: "00000000-0000-0000-0000-000000000004",
  super: "00000000-0000-0000-0000-000000000005",
  legacy: "00000000-0000-0000-0000-000000000006",
};
let db: PGlite;

async function asUser<T>(id: string, run: (tx: Transaction) => Promise<T>) {
  return db.transaction(async tx => {
    await tx.exec("SET LOCAL ROLE authenticated");
    await tx.query("SELECT set_config('request.jwt.claim.sub', $1, true)", [id]);
    await tx.exec("SELECT set_config('request.jwt.claim.role', 'authenticated', true)");
    return run(tx);
  });
}

async function assign(target: string, roles: string[], actor = ids.super) {
  return db.transaction(async tx => {
    await tx.exec("SET LOCAL ROLE service_role");
    return tx.query("SELECT public.set_user_roles($1, $2, $3)", [target, roles, actor]);
  });
}

async function seedAssignments() {
  await db.exec("DELETE FROM public.user_roles");
  for (const [id, roles] of [
    [ids.booking, ["booking_admin"]], [ids.knowledge, ["knowledge_admin"]],
    [ids.combined, ["booking_admin", "knowledge_admin"]], [ids.super, ["super_admin"]],
    [ids.legacy, ["booking_admin", "knowledge_admin"]],
  ] as const) {
    for (const role of roles) {
      await db.query("INSERT INTO public.user_roles(user_id, role_id) VALUES ($1, $2)", [id, role]);
    }
  }
}

beforeAll(async () => {
  db = await PGlite.create({ extensions: { vector } });
  // Reproduce Supabase's identities, API roles, and default table grants.
  await db.exec(`
    CREATE ROLE anon;
    CREATE ROLE authenticated;
    CREATE ROLE service_role BYPASSRLS;
    CREATE SCHEMA auth;
    CREATE TABLE auth.users (id UUID PRIMARY KEY, email TEXT, raw_user_meta_data JSONB DEFAULT '{}');
    CREATE FUNCTION auth.uid() RETURNS UUID LANGUAGE sql STABLE AS $$
      SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid;
    $$;
    CREATE FUNCTION auth.role() RETURNS TEXT LANGUAGE sql STABLE AS $$
      SELECT current_setting('request.jwt.claim.role', true);
    $$;
    GRANT USAGE ON SCHEMA public, auth TO anon, authenticated, service_role;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role;
    CREATE PUBLICATION supabase_realtime;
  `);
  const migrationDir = fileURLToPath(new URL("../../supabase/migrations/", import.meta.url));
  const migrations = (await readdir(migrationDir)).filter(name => name.endsWith(".sql")).sort();
  for (const name of migrations.filter(name => name < "008")) {
    await db.exec(await readFile(`${migrationDir}/${name}`, "utf8"));
  }
  for (const [name, id] of Object.entries(ids)) {
    const email = `${name}@goa.bits-pilani.ac.in`;
    await db.query("INSERT INTO auth.users(id, email) VALUES ($1, $2)", [id, email]);
    await db.query("INSERT INTO public.profiles(id, email, is_admin) VALUES ($1, $2, $3)", [id, email, name === "legacy"]);
  }
  for (const name of migrations.filter(name => name >= "008")) {
    await db.exec(await readFile(`${migrationDir}/${name}`, "utf8"));
  }
  for (const id of [ids.user, ids.knowledge]) {
    await db.query(`
      INSERT INTO public.bookings(user_id, room_id, room_name, title, start_time, end_time)
      VALUES ($1, 'd_153', 'D-153', 'Request', NOW(), NOW() + interval '1 hour')
    `, [id]);
  }
}, 60000);

afterEach(async () => { await seedAssignments(); });
afterAll(async () => { await db?.close(); });

describe("multi-admin migration and PostgreSQL policies", () => {
  it("preserves legacy responsibilities without granting role-management access", async () => {
    const { rows } = await db.query<{ access: { roles: string[]; permissions: string[] } }>(
      "SELECT public.get_user_access($1) AS access", [ids.legacy],
    );
    expect(rows[0].access).toEqual({
      roles: ["booking_admin", "knowledge_admin"], permissions: ["bookings.manage", "knowledge.manage"],
    });
    const columns = await db.query("SELECT column_name FROM information_schema.columns WHERE table_name='profiles' AND column_name='is_admin'");
    expect(columns.rows).toEqual([]);
  });

  it.each([
    [ids.user, []], [ids.booking, ["bookings.manage"]], [ids.knowledge, ["knowledge.manage"]],
    [ids.combined, ["bookings.manage", "knowledge.manage"]],
    [ids.super, ["bookings.manage", "knowledge.manage", "roles.manage"]],
  ])("checks permissions for %s using the authenticated identity", async (id, allowed) => {
    for (const permission of ["bookings.manage", "knowledge.manage", "roles.manage"]) {
      const result = await asUser(id as string, tx => tx.query<{ allowed: boolean }>(
        "SELECT public.has_permission($1) AS allowed", [permission],
      ));
      expect(result.rows[0].allowed).toBe(allowed.includes(permission));
    }
  });

  it("lets users edit their profile but never self-assign roles", async () => {
    await asUser(ids.user, tx => tx.query("UPDATE public.profiles SET full_name='Updated' WHERE id=$1", [ids.user]));
    await expect(asUser(ids.user, tx => tx.query(
      "INSERT INTO public.user_roles(user_id, role_id) VALUES ($1, 'super_admin')", [ids.user],
    ))).rejects.toMatchObject({ code: "42501" });
    await expect(asUser(ids.user, tx => tx.query(
      "UPDATE public.profiles SET is_admin=true WHERE id=$1", [ids.user],
    ))).rejects.toMatchObject({ code: "42703" });
  });

  it.each(["roles", "role_permissions"])("prevents clients from changing the %s catalogue", async table => {
    const result = await asUser(ids.super, tx => tx.exec(`DELETE FROM public.${table}`));
    expect(result[0].affectedRows).toBe(0);
  });

  it("hides other users' assignments except from role managers", async () => {
    const booking = await asUser(ids.booking, tx => tx.query("SELECT user_id FROM public.user_roles"));
    expect(booking.rows).toEqual([{ user_id: ids.booking }]);
    const manager = await asUser(ids.super, tx => tx.query("SELECT user_id FROM public.user_roles"));
    expect(manager.rows.length).toBeGreaterThan(1);
  });

  it.each([
    [ids.user, [ids.user]], [ids.knowledge, [ids.knowledge]],
    [ids.booking, [ids.user, ids.knowledge]], [ids.combined, [ids.user, ids.knowledge]],
    [ids.super, [ids.user, ids.knowledge]],
  ])("limits booking visibility for %s", async (id, visible) => {
    const result = await asUser(id as string, tx => tx.query<{ user_id: string }>("SELECT user_id FROM public.bookings ORDER BY user_id"));
    expect(result.rows.map(row => row.user_id)).toEqual(visible);
  });

  it.each([ids.user, ids.super])("requires API-mediated booking writes even for %s", async id => {
    await expect(asUser(id, tx => tx.query(`
      INSERT INTO public.bookings(user_id, room_id, room_name, title, start_time, end_time, status)
      VALUES ($1, 'd_153', 'D-153', 'Bypass', NOW(), NOW() + interval '1 hour', 'approved')
    `, [id]))).rejects.toMatchObject({ code: "42501" });
  });

  it.each([ids.user, ids.booking])("denies document writes to %s", async id => {
    await expect(asUser(id, tx => tx.exec(`
      INSERT INTO public.rag_files(gdrive_id, name, mime_type, md5_checksum)
      VALUES ('denied', 'Denied', 'text/plain', 'abc')
    `))).rejects.toMatchObject({ code: "42501" });
  });

  it("allows knowledge managers to update documents without gaining room-management access", async () => {
    await asUser(ids.knowledge, tx => tx.exec(`
      INSERT INTO public.rag_files(gdrive_id, name, mime_type, md5_checksum)
      VALUES ('allowed', 'Allowed', 'text/plain', 'abc')
    `));
    const result = await asUser(ids.knowledge, tx => tx.exec("UPDATE public.rooms SET name='Forbidden' WHERE id='d_153'"));
    expect(result[0].affectedRows).toBe(0);
  });

  it.each([
    ["get_user_access", "SELECT public.get_user_access($1)", [ids.super]],
    ["get_permission_recipients", "SELECT * FROM public.get_permission_recipients($1)", ["bookings.manage"]],
    ["set_user_roles", "SELECT public.set_user_roles($1, $2, $3)", [ids.user, ["super_admin"], ids.super]],
  ])("keeps the %s RPC inaccessible to clients", async (_name, sql, params) => {
    await expect(asUser(ids.super, tx => tx.query(sql as string, params as unknown[])))
      .rejects.toMatchObject({ code: "42501" });
  });

  it("assigns combined roles atomically, records the actor, and revokes access", async () => {
    await assign(ids.user, ["booking_admin", "knowledge_admin", "booking_admin"]);
    const assigned = await db.query("SELECT role_id, assigned_by FROM public.user_roles WHERE user_id=$1 ORDER BY role_id", [ids.user]);
    expect(assigned.rows).toEqual([
      { role_id: "booking_admin", assigned_by: ids.super }, { role_id: "knowledge_admin", assigned_by: ids.super },
    ]);
    await assign(ids.user, []);
    const access = await asUser(ids.user, tx => tx.query<{ allowed: boolean }>("SELECT public.has_permission('bookings.manage') AS allowed"));
    expect(access.rows[0].allowed).toBe(false);
  });

  it("refuses assignment by a user whose manager role was revoked", async () => {
    await assign(ids.user, ["super_admin"]);
    await assign(ids.user, []);
    await expect(assign(ids.booking, ["super_admin"], ids.user)).rejects.toMatchObject({ code: "42501" });
  });

  it("rejects unknown users and roles without deleting existing assignments", async () => {
    await expect(assign("00000000-0000-0000-0000-000000000099", [])).rejects.toMatchObject({ code: "P0002" });
    await expect(assign(ids.booking, ["invalid"])).rejects.toMatchObject({ code: "22023" });
    const result = await asUser(ids.booking, tx => tx.query<{ allowed: boolean }>("SELECT public.has_permission('bookings.manage') AS allowed"));
    expect(result.rows[0].allowed).toBe(true);
  });

  it("protects the last super admin, but permits removal when another exists", async () => {
    await expect(assign(ids.super, [])).rejects.toMatchObject({ code: "23514" });
    await assign(ids.user, ["super_admin"]);
    await assign(ids.super, [], ids.user);
    const result = await db.query("SELECT role_id FROM public.user_roles WHERE user_id=$1", [ids.super]);
    expect(result.rows).toEqual([]);
  });

  it("notifies booking managers once and excludes knowledge-only admins", async () => {
    const result = await db.query<{ email: string }>("SELECT * FROM public.get_permission_recipients('bookings.manage')");
    expect(result.rows.map(row => row.email).sort()).toEqual([
      "booking@goa.bits-pilani.ac.in", "combined@goa.bits-pilani.ac.in",
      "legacy@goa.bits-pilani.ac.in", "super@goa.bits-pilani.ac.in",
    ]);
  });
});

describe("reservation and index transactions", () => {
  const token = "00000000-0000-0000-0000-000000000123";
  const create = async (who: string, room: string, title: string | null, start: string, end: string) => {
    const result = await db.query<{ id: string }>(
      "select id from create_pending_booking($1,$2,$2,$3,'',$4,$5,true,true)",
      [who, room, title, start, end],
    );
    return result.rows[0].id;
  };
  const claim = (id: string) => db.query("select claim_booking_decision($1,$2,true)", [id, token]);

  it("reserves across booking IDs, permits adjacent slots, and resumes only the reserved booking", async () => {
    const times = ["2031-04-01T10:00Z", "2031-04-01T11:00Z", "2031-04-01T12:00Z"];
    const first = await create(ids.user, "lab", "First", times[0], times[1]);
    const second = await create(ids.booking, "lab", "Second", times[0], times[1]);
    const next = await create(ids.knowledge, "lab", "Next", times[1], times[2]);
    await claim(first);
    await expect(claim(second)).rejects.toThrow(/already reserved/);
    await expect(claim(first)).rejects.toThrow(/in progress/);
    await claim(next);
    await db.query("update bookings set approval_started_at=now()-interval '6 minutes' where id=$1", [first]);
    await expect(claim(second)).rejects.toThrow(/already reserved/);
    await claim(first);
    await db.query("update bookings set status='approved',approval_token=null where id=$1", [first]);
    await expect(claim(second)).rejects.toThrow(/already reserved/);
  });

  it("rolls back expiration on insert failure, and expires the original on success", async () => {
    const args = ["2031-05-01T10:00Z", "2031-05-01T11:00Z"] as const;
    const old = await create(ids.user, "lab", "Original", ...args);
    const status = async () => (await db.query<{ status: string }>("select status from bookings where id=$1", [old])).rows[0].status;
    await expect(create(ids.user, "lab", null, ...args)).rejects.toThrow();
    expect(await status()).toBe("pending");
    await create(ids.user, "lab", "Replacement", ...args);
    expect(await status()).toBe("expired");
  });

  it("blocks live edit locks, then permits a decision when the lease expires", async () => {
    const id = await create(ids.user, "lab", "Edited", "2031-06-01T10:00Z", "2031-06-01T11:00Z");
    await db.query("update bookings set is_locked=true, locked_at=now() where id=$1", [id]);
    await expect(claim(id)).rejects.toThrow(/being edited/);
    await db.query("update bookings set locked_at=now()-interval '16 minutes' where id=$1", [id]);
    await claim(id);
    expect((await db.query("select is_locked from bookings where id=$1", [id])).rows[0]).toEqual({ is_locked: false });
  });

  it("keeps the previous revision and chunks after a failed or stale replacement", async () => {
    const publish = (revision: string, previous: string | null, size: number) => db.query(
      "select replace_rag_index('test-doc','doc','text/plain',$1,$2,$3)",
      [revision, previous, JSON.stringify([{ content: revision, embedding: Array(size).fill(0.1) }])],
    );
    await publish("v1", null, 768);
    await expect(publish("v2", "v1", 3)).rejects.toThrow();
    await expect(publish("stale", null, 768)).rejects.toThrow(/changed during sync/);
    const read = () => db.query("select md5_checksum,content from rag_files join rag_chunks on file_id=rag_files.id where gdrive_id='test-doc'");
    expect((await read()).rows).toEqual([{ md5_checksum: "v1", content: "v1" }]);
    await publish("v2", "v1", 768);
    expect((await read()).rows).toEqual([{ md5_checksum: "v2", content: "v2" }]);
    // A document deliberately emptied in Drive clears the previous searchable content.
    await db.query("select replace_rag_index('test-doc','doc','text/plain','v3','v2','[]')");
    expect((await read()).rows).toEqual([]);
    expect((await db.query("select md5_checksum,index_complete from rag_files where gdrive_id='test-doc'")).rows)
      .toEqual([{ md5_checksum: "v3", index_complete: true }]);
  });
});
