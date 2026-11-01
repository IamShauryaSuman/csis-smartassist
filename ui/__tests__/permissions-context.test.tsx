import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { UserAccess } from "@/lib/types";

const state = vi.hoisted(() => ({ getPermissions: vi.fn(), pathname: "/admin" }));
vi.mock("@/lib/api", () => ({ api: { getPermissions: state.getPermissions } }));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));

import { PermissionsProvider, usePermissions } from "@/lib/contexts/permissions-context";

const booking: UserAccess = { user_id: "user", roles: ["booking_admin"], permissions: ["bookings.manage"] };
function Consumer() {
  const { can, loading, error, refresh } = usePermissions();
  return <>
    <span>{loading ? "loading" : "loaded"}</span>
    <span>{can("bookings.manage") ? "allowed" : "denied"}</span>
    {error && <p role="alert">{error}</p>}
    <button onClick={() => void refresh()}>Refresh</button>
  </>;
}
const view = (userId = "user") => <PermissionsProvider userId={userId}><Consumer /></PermissionsProvider>;

beforeEach(() => {
  state.getPermissions.mockReset();
  state.pathname = "/admin";
});

describe("permissions state", () => {
  it("starts without access, then uses the server response", async () => {
    state.getPermissions.mockResolvedValue(booking);
    render(view());
    expect(screen.getByText("denied")).toBeVisible();
    await screen.findByText("allowed");
    expect(state.getPermissions).toHaveBeenCalledOnce();
  });

  it("revokes UI access after a focus refresh", async () => {
    state.getPermissions.mockResolvedValueOnce(booking).mockResolvedValueOnce({ user_id: "user", roles: [], permissions: [] });
    render(view());
    await screen.findByText("allowed");
    fireEvent(window, new Event("focus"));
    await screen.findByText("denied");
  });

  it("fails closed and supports retry", async () => {
    state.getPermissions.mockRejectedValueOnce(new Error("Unavailable")).mockResolvedValueOnce(booking);
    render(view());
    await screen.findByRole("alert");
    expect(screen.getByText("denied")).toBeVisible();
    fireEvent.click(screen.getByText("Refresh"));
    await screen.findByText("allowed");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("refreshes on navigation", async () => {
    state.getPermissions.mockResolvedValue(booking);
    const { rerender } = render(view());
    await screen.findByText("allowed");
    state.pathname = "/profile";
    rerender(view());
    await waitFor(() => expect(state.getPermissions).toHaveBeenCalledTimes(2));
  });

  it("ignores an earlier account's response after switching users", async () => {
    let resolveFirst!: (access: UserAccess) => void;
    state.getPermissions.mockReturnValueOnce(new Promise<UserAccess>(resolve => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ user_id: "other", roles: [], permissions: [] });
    const { rerender } = render(view());
    rerender(view("other"));
    await screen.findByText("loaded");
    await act(async () => { resolveFirst(booking); });
    expect(screen.getByText("denied")).toBeVisible();
  });

  it("rejects permissions returned for a different user", async () => {
    state.getPermissions.mockResolvedValue({ ...booking, user_id: "other" });
    render(view());
    await screen.findByText("loaded");
    expect(screen.getByText("denied")).toBeVisible();
  });
});
