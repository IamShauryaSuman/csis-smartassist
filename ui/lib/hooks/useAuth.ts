/**
 * useAuth — Authentication state hook.
 *
 * Manages Supabase auth state, Google OAuth sign-in/out,
 * and synchronizes the API client token.
 *
 * In LOCAL_AUTH mode, creates a fake local admin session
 * so the app works on intranet servers without Google OAuth.
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import type { User } from "@supabase/supabase-js";
import { createClient } from "@/lib/supabase/client";
import { api } from "@/lib/api";

const IS_LOCAL_AUTH = process.env.NEXT_PUBLIC_LOCAL_AUTH === "true";
const LOCAL_USER_ID = process.env.NEXT_PUBLIC_LOCAL_USER_ID || "1cce9d10-6970-4c9f-9f5e-39bc6b6c6671";

// A fixed user object for local auth mode
const LOCAL_USER: User = {
  id: LOCAL_USER_ID,
  aud: "authenticated",
  role: "authenticated",
  email: "smartassist-admin@goa.bits-pilani.ac.in",
  email_confirmed_at: new Date().toISOString(),
  app_metadata: { provider: "local" },
  user_metadata: { full_name: "SmartAssist Admin", avatar_url: "" },
  identities: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
} as unknown as User;

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const supabase = createClient();

  useEffect(() => {
    // ── Local Auth Mode ───────────────────────────────────────────────
    if (IS_LOCAL_AUTH) {
      const isLogged =
        typeof window !== "undefined" &&
        localStorage.getItem("smartassist_local_auth") === "true";
      if (isLogged) {
        setUser(LOCAL_USER);
        api.setToken("local-auth-token");
      } else {
        setUser(null);
      }
      setLoading(false);
      return;
    }

    // ── Supabase Auth Mode ────────────────────────────────────────────
    const initAuth = async () => {
      try {
        const {
          data: { session },
        } = await supabase.auth.getSession();

        if (session) {
          setUser(session.user);
          api.setToken(session.access_token);
        }
      } finally {
        setLoading(false);
      }
    };

    initAuth();

    // Listen for auth state changes
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null);
      if (session) {
        api.setToken(session.access_token);
      }
      setLoading(false);
    });

    return () => subscription.unsubscribe();
  }, [supabase.auth]);

  const signInWithGoogle = useCallback(async () => {
    // ── Local Auth: skip OAuth, just set user directly ────────────────
    if (IS_LOCAL_AUTH) {
      if (typeof window !== "undefined") {
        localStorage.setItem("smartassist_local_auth", "true");
      }
      setUser(LOCAL_USER);
      api.setToken("local-auth-token");
      setLoading(false);
      window.location.href = "/chat";
      return;
    }

    // ── Cloud Auth: redirect to Google OAuth ──────────────────────────
    const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
    const redirectUri = `${window.location.origin}/auth-callback`;
    
    if (!clientId) {
      console.error("Missing NEXT_PUBLIC_GOOGLE_CLIENT_ID");
      return;
    }

    // Generate a secure raw nonce
    const rawNonce = crypto.randomUUID().replace(/-/g, '') + crypto.randomUUID().replace(/-/g, '');
    
    // Hash the nonce for Google.
    // crypto.subtle is only available in secure contexts (HTTPS/localhost).
    // For plain HTTP deployments, fall back to a simple hash.
    let hashedNonce: string;
    if (typeof crypto !== "undefined" && crypto.subtle) {
      const encoder = new TextEncoder();
      const encodedNonce = encoder.encode(rawNonce);
      const hashBuffer = await crypto.subtle.digest('SHA-256', encodedNonce);
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      hashedNonce = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
    } else {
      let hash = 0;
      for (let i = 0; i < rawNonce.length; i++) {
        const char = rawNonce.charCodeAt(i);
        hash = ((hash << 5) - hash + char) | 0;
      }
      hashedNonce = Math.abs(hash).toString(16).padStart(8, '0') + rawNonce.slice(0, 24);
    }

    // Store raw nonce in localStorage for Supabase verification
    if (typeof window !== "undefined") {
      localStorage.setItem("supabase-auth-nonce", rawNonce);
    }
    
    const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?client_id=${clientId}&redirect_uri=${encodeURIComponent(redirectUri)}&response_type=id_token&scope=openid%20email%20profile&prompt=select_account&nonce=${hashedNonce}`;
    
    window.location.href = authUrl;
  }, []);

  const signInWithIdToken = useCallback(async (token: string, nonce?: string) => {
    if (IS_LOCAL_AUTH) return; // No-op in local mode

    setLoading(true);
    const { error } = await supabase.auth.signInWithIdToken({
      provider: "google",
      token,
      nonce,
    });
    if (error) {
      setLoading(false);
      console.error("Sign-in error:", error.message);
      throw error;
    }
    // Loading stays true until onAuthStateChange picks up the session
  }, [supabase.auth]);

  const signOut = useCallback(async () => {
    if (IS_LOCAL_AUTH) {
      // In local mode, clear localStorage flag and redirect back to landing
      if (typeof window !== "undefined") {
        localStorage.removeItem("smartassist_local_auth");
      }
      setUser(null);
      api.setToken("");
      window.location.href = "/";
      return;
    }

    await supabase.auth.signOut();
    setUser(null);
    api.setToken("");
  }, [supabase.auth]);

  return { user, loading, signInWithGoogle, signInWithIdToken, signOut };
}
