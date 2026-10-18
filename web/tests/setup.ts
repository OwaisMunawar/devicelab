import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

// jsdom does not implement scrolling; components call it when following a log.
window.scrollTo = () => undefined;
Element.prototype.scrollIntoView = () => undefined;
