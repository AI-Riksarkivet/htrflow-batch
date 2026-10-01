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

test.each([413, 422])(
  "a %i says the user name or password is too long or malformed",
  async (status) => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(new Response(null, { status }));
    render(Page);
    await fireEvent.input(screen.getByLabelText(/user/i), {
      target: { value: "a" },
    });
    await fireEvent.input(screen.getByLabelText(/password/i), {
      target: { value: "b" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /log in/i }));
    expect(
      await screen.findByText(
        "The user name or password is too long or malformed.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/could not be reached/)).toBeNull();
  },
);

test("a per-user limit says how long to wait, as the proxy does", async () => {
  globalThis.fetch = vi.fn().mockResolvedValue(
    Response.json(
      {
        detail:
          "too many failed logins for this user: wait five minutes and try again",
      },
      { status: 429 },
    ),
  );
  render(Page);
  await fireEvent.input(screen.getByLabelText(/user/i), {
    target: { value: "a" },
  });
  await fireEvent.input(screen.getByLabelText(/password/i), {
    target: { value: "b" },
  });
  await fireEvent.click(screen.getByRole("button", { name: /log in/i }));
  expect(await screen.findByText(/wait five minutes/)).toBeTruthy();
  expect(screen.queryByText(/wait a minute/i)).toBeNull();
});
