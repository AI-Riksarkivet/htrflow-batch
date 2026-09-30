import { fireEvent, render, screen } from "@testing-library/svelte";
import { expect, test, vi } from "vitest";
import Page from "./+page.svelte";

test("a wrong password says so and stays on the page", async () => {
  globalThis.fetch = vi
    .fn()
    .mockResolvedValue(new Response(null, { status: 401 }));
  render(Page);
  await fireEvent.input(screen.getByLabelText(/user/i), {
    target: { value: "a" },
  });
  await fireEvent.input(screen.getByLabelText(/password/i), {
    target: { value: "b" },
  });
  await fireEvent.click(screen.getByRole("button", { name: /log in/i }));
  expect(await screen.findByText(/did not accept/i)).toBeTruthy();
});
