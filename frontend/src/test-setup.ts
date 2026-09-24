// Vitest setup: jest-dom matchers (toBeInTheDocument, toHaveAttribute, ...).
import "@testing-library/jest-dom/vitest";

// jsdom has no ResizeObserver, which Svelte's bind:clientHeight uses. A
// stand-in that never reports: a measured height is 0 there, as jsdom lays
// nothing out anyway.
globalThis.ResizeObserver ??= class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
};
