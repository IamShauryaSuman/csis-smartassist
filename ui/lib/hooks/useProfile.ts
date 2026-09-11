/**
 * useProfile — Profile state management hook.
 *
 * Handles profile loading, creation (onboarding), and updates.
 * Detects whether the user needs onboarding.
 */

"use client";

import { useCallback, useEffect, useState, useMemo } from "react";
import { createClient } from "@/lib/supabase/client";
import type { OnboardingFormData, Profile } from "@/lib/types";

const IS_LOCAL_AUTH = process.env.NEXT_PUBLIC_LOCAL_AUTH === "true";
const LOCAL_USER_ID = process.env.NEXT_PUBLIC_LOCAL_USER_ID || "1cce9d10-6970-4c9f-9f5e-39bc6b6c6671";

const DEFAULT_LOCAL_PROFILE: Profile = {
  id: LOCAL_USER_ID,
  email: "smartassist-admin@goa.bits-pilani.ac.in",
  full_name: "SmartAssist Admin",
  academic_role: "faculty",
  department: "CSIS",
  year: null,
  interests: ["Systems", "AI", "Administration"],
  synthesized_memory: "",
  is_admin: true,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

export function useProfile(userId: string | undefined) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [needsOnboarding, setNeedsOnboarding] = useState(false);
  const supabase = useMemo(() => createClient(), []);

  useEffect(() => {
    // ── Local Auth Mode ───────────────────────────────────────────────
    if (IS_LOCAL_AUTH) {
      let activeProfile = DEFAULT_LOCAL_PROFILE;
      try {
        const cached = localStorage.getItem("smartassist_local_profile");
        if (cached) {
          activeProfile = { ...DEFAULT_LOCAL_PROFILE, ...JSON.parse(cached) };
        }
      } catch {}
      setProfile(activeProfile);
      setNeedsOnboarding(false);
      setLoading(false);
      return;
    }

    if (!userId) {
      setLoading(false);
      return;
    }

    const loadProfile = async () => {
      setLoading(true);
      try {
        const { data, error } = await supabase
          .from("profiles")
          .select("*")
          .eq("id", userId)
          .single();

        if (error || !data) {
          setNeedsOnboarding(true);
          setProfile(null);
        } else if (!data.academic_role) {
          // Profile exists but onboarding incomplete
          setNeedsOnboarding(true);
          setProfile(data as Profile);
        } else {
          setProfile(data as Profile);
          setNeedsOnboarding(false);
        }
      } catch {
        setNeedsOnboarding(true);
      } finally {
        setLoading(false);
      }
    };

    loadProfile();
  }, [userId, supabase]);

  const createProfile = useCallback(
    async (formData: OnboardingFormData) => {
      if (IS_LOCAL_AUTH) {
        const newProfile: Profile = {
          ...DEFAULT_LOCAL_PROFILE,
          full_name: formData.full_name,
          academic_role: formData.academic_role,
          department: formData.department,
          year: formData.year,
          interests: formData.interests,
        };
        try {
          localStorage.setItem("smartassist_local_profile", JSON.stringify(newProfile));
        } catch {}
        setProfile(newProfile);
        setNeedsOnboarding(false);
        return newProfile;
      }

      if (!userId) throw new Error("No user ID");

      const { data: userData } = await supabase.auth.getUser();
      const email = userData.user?.email || "";

      const profileData = {
        id: userId,
        email,
        full_name: formData.full_name,
        academic_role: formData.academic_role,
        department: formData.department,
        year: formData.year,
        interests: formData.interests,
        synthesized_memory: "",
        is_admin: false,
      };

      const { data, error } = await supabase
        .from("profiles")
        .upsert(profileData, { onConflict: 'id' })
        .select()
        .single();

      if (error) throw error;
      setProfile(data as Profile);
      setNeedsOnboarding(false);
      return data as Profile;
    },
    [userId, supabase]
  );

  const updateProfile = useCallback(
    async (updates: Partial<Profile>) => {
      if (IS_LOCAL_AUTH) {
        const updated = { ...(profile || DEFAULT_LOCAL_PROFILE), ...updates };
        try {
          localStorage.setItem("smartassist_local_profile", JSON.stringify(updated));
        } catch {}
        setProfile(updated);
        return updated as Profile;
      }

      if (!userId) throw new Error("No user ID");

      const { data, error } = await supabase
        .from("profiles")
        .update(updates)
        .eq("id", userId)
        .select()
        .single();

      if (error) throw error;
      setProfile(data as Profile);
      return data as Profile;
    },
    [userId, supabase, profile]
  );

  return { profile, loading, needsOnboarding, createProfile, updateProfile };
}
