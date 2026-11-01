"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/lib/contexts/permissions-context";
import type { AdminRole, AdminRoleName, AdminUser } from "@/lib/types";
import styles from "./role-manager.module.scss";

export default function RoleManager() {
  const { access, refresh } = usePermissions();
  const [email, setEmail] = useState("");
  const [user, setUser] = useState<AdminUser | null>(null);
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [selected, setSelected] = useState<AdminRoleName[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const findUser = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setUser(null);
    try {
      const [found, availableRoles] = await Promise.all([
        api.findAdminUser(email.trim()), api.listAdminRoles(),
      ]);
      setUser(found);
      setSelected(found.roles);
      setRoles(availableRoles);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not find user.");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!user) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.setUserRoles(user.id, selected);
      setUser({ ...user, roles: result.roles });
      if (user.id === access?.user_id) await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save roles.");
    } finally {
      setBusy(false);
    }
  };

  const changed = user && [...selected].sort().join() !== [...user.roles].sort().join();
  return (
    <div className={styles.manager}>
      <h2>Administrator roles</h2>
      <p>Find a user by email. Assign multiple roles, or clear all roles to restore regular user access.</p>
      <form className={styles.search} onSubmit={findUser}>
        <label htmlFor="admin-email">User email</label>
        <input id="admin-email" type="email" required value={email} disabled={busy}
          onChange={event => setEmail(event.target.value)} />
        <button type="submit" disabled={busy}>Find user</button>
      </form>
      {busy && <p role="status">Working...</p>}
      {error && <p role="alert">{error}</p>}
      {user && (
        <fieldset className={styles.user} disabled={busy}>
          <legend>{user.email}</legend>
          {user.full_name && <p>{user.full_name}</p>}
          {roles.map(role => (
            <label key={role.id}>
              <input type="checkbox" checked={selected.includes(role.id)} onChange={() => {
                setSelected(current => current.includes(role.id)
                  ? current.filter(id => id !== role.id) : [...current, role.id]);
              }} />
              {role.name}
            </label>
          ))}
          <button type="button" onClick={save} disabled={busy || !changed}>Save roles</button>
        </fieldset>
      )}
    </div>
  );
}
