import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { AdminUser } from "@/lib/types";

const mocks = vi.hoisted(() => ({
  listAdminRoles: vi.fn(), findAdminUser: vi.fn(), setUserRoles: vi.fn(), refresh: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: mocks }));
vi.mock("@/lib/contexts/permissions-context", () => ({
  usePermissions: () => ({ access: { user_id: "self" }, refresh: mocks.refresh }),
}));
import RoleManager from "@/components/admin/role-manager";

const user: AdminUser = { id: "target", email: "student@goa.bits-pilani.ac.in", full_name: "Student", roles: ["booking_admin"] };
beforeEach(() => {
  vi.resetAllMocks();
  mocks.listAdminRoles.mockResolvedValue([
    { id: "booking_admin", name: "Booking Admin" }, { id: "knowledge_admin", name: "Knowledge Admin" },
  ]);
  mocks.findAdminUser.mockResolvedValue(user);
  mocks.setUserRoles.mockImplementation(async (id, roles) => ({ id, roles }));
});

async function findUser() {
  render(<RoleManager />);
  fireEvent.change(screen.getByLabelText("User email"), { target: { value: user.email } });
  fireEvent.click(screen.getByRole("button", { name: "Find user" }));
  return within(await screen.findByRole("group", { name: user.email }));
}

it.each([
  ["Knowledge Admin", ["booking_admin", "knowledge_admin"]], ["Booking Admin", []],
])("saves the role selection after toggling %s", async (label, expected) => {
  const row = await findUser();
  expect(mocks.findAdminUser).toHaveBeenCalledWith(user.email);
  expect(row.getByRole("button", { name: "Save roles" })).toBeDisabled();
  fireEvent.click(row.getByLabelText(label));
  fireEvent.click(row.getByRole("button", { name: "Save roles" }));
  await waitFor(() => expect(mocks.setUserRoles).toHaveBeenCalledWith("target", expected));
  await waitFor(() => expect(row.getByRole("button", { name: "Save roles" })).toBeDisabled());
  expect(mocks.refresh).not.toHaveBeenCalled();
});

it("preserves rejected edits so they can be corrected", async () => {
  mocks.setUserRoles.mockRejectedValue(new Error("Cannot remove the last super admin."));
  const row = await findUser();
  fireEvent.click(row.getByLabelText("Booking Admin"));
  fireEvent.click(row.getByRole("button", { name: "Save roles" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Cannot remove the last super admin.");
  expect(row.getByRole("button", { name: "Save roles" })).toBeEnabled();
});

it("refreshes permissions after editing the current user", async () => {
  mocks.findAdminUser.mockResolvedValue({ ...user, id: "self" });
  const row = await findUser();
  fireEvent.click(row.getByLabelText("Knowledge Admin"));
  fireEvent.click(row.getByRole("button", { name: "Save roles" }));
  await waitFor(() => expect(mocks.refresh).toHaveBeenCalledOnce());
});

it("clears the previous user when another lookup fails", async () => {
  await findUser();
  mocks.findAdminUser.mockRejectedValue(new Error("User not found."));
  fireEvent.change(screen.getByLabelText("User email"), { target: { value: "unknown@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Find user" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("User not found.");
  expect(screen.queryByRole("group")).not.toBeInTheDocument();
});
