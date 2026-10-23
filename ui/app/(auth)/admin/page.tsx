"use client";

import { useState } from "react";
import { useAuth } from "@/lib/hooks/useAuth";
import { usePermissions } from "@/lib/contexts/permissions-context";
import type { Permission } from "@/lib/types";
import AdminBookingTable from "@/components/bookings/admin-booking-table";
import RagDashboard from "@/components/dashboard/rag-dashboard";
import RoleManager from "@/components/admin/role-manager";
import styles from "./page.module.scss";

const TABS: { id: string; label: string; permission: Permission }[] = [
  { id: "bookings", label: "Booking Approvals", permission: "bookings.manage" },
  { id: "rag", label: "Knowledge Base", permission: "knowledge.manage" },
  { id: "roles", label: "Administrator Roles", permission: "roles.manage" },
];

export default function AdminPage() {
  const { user } = useAuth();
  const { access, can, loading, error, refresh } = usePermissions();
  const [selectedTab, setSelectedTab] = useState("bookings");
  const tabs = TABS.filter(tab => can(tab.permission));
  const activeTab = tabs.find(tab => tab.id === selectedTab)?.id ?? tabs[0]?.id;

  if (loading && !access) return <p role="status">Loading permissions…</p>;
  if (error) return <div role="alert">{error} <button onClick={() => void refresh()}>Retry</button></div>;
  if (!tabs.length) return <p className={styles.accessDenied}>You do not have administrator access.</p>;

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>Admin Dashboard</h1>
        <p className={styles.subtitle}>Manage your assigned responsibilities.</p>
      </div>
      <div className={styles.tabs} role="tablist" aria-label="Administration">
        {tabs.map(tab => (
          <button key={tab.id} id={`tab-${tab.id}`} role="tab" aria-selected={activeTab === tab.id}
            aria-controls="admin-panel" onClick={() => setSelectedTab(tab.id)}
            className={`${styles.tab} ${activeTab === tab.id ? styles.tabActive : ""}`}>
            {tab.label}
          </button>
        ))}
      </div>
      <div id="admin-panel" role="tabpanel" aria-labelledby={`tab-${activeTab}`} className={styles.section}>
        {activeTab === "bookings" && user && <AdminBookingTable userId={user.id} />}
        {activeTab === "rag" && <RagDashboard />}
        {activeTab === "roles" && <RoleManager />}
      </div>
    </div>
  );
}
