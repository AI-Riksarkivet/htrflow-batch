import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { watchOnScreen } from "./onscreen.js";

// Every card watched itself with an IntersectionObserver of its own, for as
// long as it lived (review of this change). One observer watches them all.
describe("watchOnScreen", () => {
  class FakeObserver {
    static all: FakeObserver[] = [];
    observed = new Set<Element>();
    disconnected = false;
    constructor(readonly callback: IntersectionObserverCallback) {
      FakeObserver.all.push(this);
    }
    observe(el: Element): void {
      this.observed.add(el);
    }
    unobserve(el: Element): void {
      this.observed.delete(el);
    }
    disconnect(): void {
      this.disconnected = true;
    }
    say(target: Element, isIntersecting: boolean): void {
      this.callback(
        [{ target, isIntersecting } as IntersectionObserverEntry],
        this as unknown as IntersectionObserver,
      );
    }
  }

  beforeEach(() => {
    FakeObserver.all = [];
    vi.stubGlobal("IntersectionObserver", FakeObserver);
  });
  afterEach(() => vi.unstubAllGlobals());

  test("one observer for every node, each told only about itself", () => {
    const a = document.createElement("div");
    const b = document.createElement("div");
    const heard: string[] = [];
    const stopA = watchOnScreen(a, (on) => heard.push(`a ${on}`));
    const stopB = watchOnScreen(b, (on) => heard.push(`b ${on}`));
    expect(FakeObserver.all).toHaveLength(1);
    FakeObserver.all[0]?.say(b, true);
    FakeObserver.all[0]?.say(a, false);
    expect(heard).toEqual(["b true", "a false"]);
    stopA();
    expect(FakeObserver.all[0]?.disconnected).toBe(false);
    stopB();
    // Nothing left to watch: the observer goes, and the next watch starts
    // a fresh one.
    expect(FakeObserver.all[0]?.disconnected).toBe(true);
    watchOnScreen(a, () => {})();
    expect(FakeObserver.all).toHaveLength(2);
  });

  test("with no IntersectionObserver, a node counts as on screen", () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    const heard: boolean[] = [];
    watchOnScreen(document.createElement("div"), (on) => heard.push(on))();
    expect(heard).toEqual([true]);
  });
});
