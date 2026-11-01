"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import type { Permission, UserAccess } from "@/lib/types";

interface PermissionsContextValue {
  access: UserAccess | null;
  loading: boolean;
  error: string;
  can: (permission: Permission) => boolean;
  refresh: () => Promise<void>;
}

const PermissionsContext = createContext<PermissionsContextValue | null>(null);

export function PermissionsProvider({ userId, children }: {
  userId: string;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [access, setAccess] = useState<UserAccess | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requests = useRef({ latest: 0 });

  const refresh = useCallback(async () => {
    const currentRequest = ++requests.current.latest;
    setLoading(true);
    setError("");
    try {
      const data = await api.getPermissions();
      if (data.user_id !== userId) throw new Error("Permissions belong to a different account.");
      if (currentRequest === requests.current.latest) setAccess(data);
    } catch (err) {
      if (currentRequest === requests.current.latest) {
        setAccess(null);
        setError(err instanceof Error ? err.message : "Could not load permissions.");
      }
    } finally {
      if (currentRequest === requests.current.latest) setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    const requestState = requests.current;
    void refresh();
    const onFocus = () => { void refresh(); };
    window.addEventListener("focus", onFocus);
    return () => {
      ++requestState.latest;
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh, pathname]);

  const currentAccess = access?.user_id === userId ? access : null;
  const can = (permission: Permission) => !!currentAccess?.permissions.includes(permission);

  return (
    <PermissionsContext.Provider value={{ access: currentAccess, loading, error, can, refresh }}>
      {children}
    </PermissionsContext.Provider>
  );
}

export function usePermissions() {
  const context = useContext(PermissionsContext);
  if (!context) throw new Error("usePermissions requires PermissionsProvider.");
  return context;
}
