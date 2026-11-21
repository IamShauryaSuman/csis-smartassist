import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AdminBookingTable from "@/components/bookings/admin-booking-table";
import { mockBooking } from "./fixtures";

const actions = vi.hoisted(() => ({
  loadBookings: vi.fn(), updateBookingStatus: vi.fn(), unlockBooking: vi.fn(),
}));
vi.mock("@/lib/hooks/useBookings", () => ({
  useBookings: () => ({
    ...actions, loading: false,
    bookings: [{ ...mockBooking, is_locked: true, locked_at: "2030-05-01T10:00:00Z" }],
  }),
}));
afterEach(() => vi.useRealTimers());

it("allows a manager to unlock immediately and shows approval when the edit lease expires", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2030-05-01T10:14:59Z"));
  render(<AdminBookingTable userId="admin" />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Unlock" })); });
  expect(actions.unlockBooking).toHaveBeenCalledWith(mockBooking.id);
  act(() => { vi.advanceTimersByTime(1000); });
  expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
});
