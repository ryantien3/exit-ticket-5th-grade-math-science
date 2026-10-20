// Small page helpers shared by the student and teacher pages.
// Both pages have a #status line and <section class="screen" id="screen-..."> screens.

export const $ = (id) => document.getElementById(id);

/** Creates an element. Text is set with textContent, so it's never run as HTML. */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function setStatus(message) {
  $("status").textContent = message;
}

export function showScreen(name) {
  for (const screen of document.querySelectorAll(".screen")) {
    screen.hidden = screen.id !== `screen-${name}`;
  }
  setStatus("");
  window.scrollTo(0, 0);
  // Move keyboard and screen-reader focus to the new screen's heading,
  // so it's clear the page changed.
  $(`screen-${name}`).querySelector("h1").focus();
}
