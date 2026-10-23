import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Permission } from "@/lib/types";

const state = vi.hoisted(() => ({
  permissions: [] as string[], loading: false, error: "", refresh: vi.fn(),
}));
vi.mock("@/lib/hooks/useAuth", () => ({ useAuth: () => ({ user: { id: "user" } }) }));
vi.mock("@/lib/contexts/permissions-context", () => ({
  usePermissions: () => ({ ...state, access: null, can: (permission: string) => state.permissions.includes(permission) }),
}));
vi.mock("@/components/bookings/admin-booking-table", () => ({ default: () => <p>Booking panel</p> }));
vi.mock("@/components/dashboard/rag-dashboard", () => ({ default: () => <p>Knowledge panel</p> }));
vi.mock("@/components/admin/role-manager", () => ({ default: () => <p>Roles panel</p> }));

import AdminPage from "@/app/(auth)/admin/page";

beforeEach(() => {
  state.permissions = [];
  state.loading = false;
  state.error = "";
  state.refresh.mockReset();
});

describe("permission-based admin navigation", () => {
  it.each([
    [[], []],
    [["bookings.manage"], ["Booking Approvals"]],
    [["knowledge.manage"], ["Knowledge Base"]],
    [["bookings.manage", "knowledge.manage"], ["Booking Approvals", "Knowledge Base"]],
    [["bookings.manage", "knowledge.manage", "roles.manage"], ["Booking Approvals", "Knowledge Base", "Administrator Roles"]],
  ])("shows only the tabs allowed by %j", (permissions, tabs) => {
    state.permissions = permissions as Permission[];
    render(<AdminPage />);
    expect(screen.queryAllByRole("tab").map(tab => tab.textContent)).toEqual(tabs);
    if (!tabs.length) expect(screen.getByText("You do not have administrator access.")).toBeVisible();
  });

  it("opens the knowledge panel directly for knowledge-only admins", () => {
    state.permissions = ["knowledge.manage"];
    render(<AdminPage />);
    expect(screen.getByText("Knowledge panel")).toBeVisible();
    expect(screen.queryByText("Booking panel")).not.toBeInTheDocument();
  });

  it("switches panels and removes a panel when its permission is revoked", () => {
    state.permissions = ["bookings.manage", "roles.manage"];
    const { rerender } = render(<AdminPage />);
    fireEvent.click(screen.getByRole("tab", { name: "Administrator Roles" }));
    expect(screen.getByText("Roles panel")).toBeVisible();
    state.permissions = ["bookings.manage"];
    rerender(<AdminPage />);
    expect(screen.queryByText("Roles panel")).not.toBeInTheDocument();
    expect(screen.getByText("Booking panel")).toBeVisible();
  });

  it("does not show privileged panels while permissions load or fail", () => {
    state.loading = true;
    const { rerender } = render(<AdminPage />);
    expect(screen.getByRole("status")).toBeVisible();
    expect(screen.queryByRole("tabpanel")).not.toBeInTheDocument();
    state.loading = false;
    state.error = "Unavailable";
    rerender(<AdminPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("Unavailable");
    fireEvent.click(screen.getByText("Retry"));
    expect(state.refresh).toHaveBeenCalledOnce();
  });
});
