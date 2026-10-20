// The teacher dashboard: sign in, pick a classroom, then watch mastery, a student x standard
// heatmap and recent activity, refreshed every 15 seconds. Also releases/hides exit tickets
// and shows student login codes on request.
// Text from the API is always set with textContent (never innerHTML).

import { createClient, isApiError, pathId } from "./api.js";
import { $, el, setStatus, showScreen } from "./dom.js";

const api = createClient("exit-ticket-teacher-session");

const REFRESH_MS = 15000;
const CLASSROOM_KEY = "exit-ticket-teacher-classroom";

const SUBJECTS = [
  { key: "math", name: "Math" },
  { key: "science", name: "Science" },
];
const DOMAIN_NAMES = {
  "NY-5.NF": "Fractions",
  "NY-5.NBT": "Place value & decimals",
  "5-PS1": "Matter and its interactions",
};
// Every level has a symbol and a word as well as a color, so it never depends on color alone.
const LEVELS = {
  mastered: { label: "Mastered", symbol: "✓", className: "level-mastered" },
  approaching: { label: "Approaching", symbol: "◐", className: "level-approaching" },
  "needs support": { label: "Needs support", symbol: "!", className: "level-needs-support" },
};

const state = {
  standards: new Map(), // code -> { code, domain, subject, description }
  classroomId: null,
  paused: false,
  timer: null,
  lastUpdated: null,
  seenSubmissions: null, // Set of "student:ticket" keys; null until the first load
  studentMastery: null, // last /mastery/students response, re-drawn when the filter changes
};

// --- Formatting ---

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" });
const dateTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });
const relativeFormat = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/** 75 -> "75%", 66.7 -> "66.7%". Never rounded further, so the % always agrees with the level. */
function formatPercent(value) {
  return value === null ? "—" : `${value}%`;
}

function timeAgo(date) {
  const seconds = Math.round((date - Date.now()) / 1000); // negative = in the past
  if (seconds > -60) return "just now";
  if (seconds > -3600) return relativeFormat.format(Math.round(seconds / 60), "minute");
  if (seconds > -86400) return relativeFormat.format(Math.round(seconds / 3600), "hour");
  return relativeFormat.format(Math.round(seconds / 86400), "day");
}

/** "NY-5.NF.1" -> "NF.1", "5-PS1-2" -> "PS1-2": short column headers for the heatmap. */
function shortCode(code) {
  return code.replace(/^NY-5\./, "").replace(/^5-/, "");
}

function domainOf(code) {
  return state.standards.get(code)?.domain ?? code;
}

function badge(level) {
  if (!level) return el("span", "badge level-none", "Not started");
  const { label, symbol, className } = LEVELS[level];
  const node = el("span", `badge ${className}`);
  const icon = el("span", null, symbol);
  icon.setAttribute("aria-hidden", "true");
  node.append(icon, ` ${label}`);
  return node;
}

function friendlyMessage(error) {
  if (isApiError(error, "unreachable")) {
    return "Can't reach the Exit Ticket server. Check that the API is running, then try again.";
  }
  return "Something went wrong. Try again.";
}

// --- Sign in and out ---

function showSignIn(message = "") {
  $("signed-in").hidden = true;
  showScreen("sign-in");
  $("sign-in-message").hidden = !message;
  $("sign-in-message").textContent = message;
}

function showPasscodeError(message) {
  $("passcode-error").textContent = message;
  $("passcode-error").hidden = false;
  $("passcode").setAttribute("aria-invalid", "true");
  $("passcode").focus();
  $("passcode").select();
}

$("passcode").addEventListener("input", () => {
  $("passcode-error").hidden = true;
  $("passcode").removeAttribute("aria-invalid");
});

$("sign-in-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const passcode = $("passcode").value;
  if (!passcode) {
    showPasscodeError("Enter the teacher passcode.");
    return;
  }
  const button = $("sign-in-button");
  button.disabled = true;
  setStatus("Signing in…");
  try {
    await api.signIn("/teacher-sessions", { passcode });
    $("passcode").value = "";
    startDashboard();
  } catch (error) {
    setStatus("");
    if (isApiError(error, "unauthorized")) {
      showPasscodeError("That passcode didn't work.");
    } else {
      showError(error, "Try again", () => showSignIn());
    }
  } finally {
    button.disabled = false;
  }
});

function signOut(message = "") {
  api.signOut();
  stopRefreshing();
  // Clear everything, so nothing is left on screen for the next person.
  for (const id of ["mastery-tables", "heatmap", "activity-feed", "ticket-releases", "codes-body"]) {
    $(id).replaceChildren();
  }
  $("codes").open = false;
  state.studentMastery = null;
  showSignIn(message);
}

$("sign-out").addEventListener("click", () => signOut("You're signed out."));

// --- Errors ---

let errorAction = null;

function showError(error, actionLabel, action) {
  console.error(error);
  $("error-message").textContent = friendlyMessage(error);
  $("error-action").textContent = actionLabel;
  errorAction = action;
  showScreen("error");
}

$("error-action").addEventListener("click", () => errorAction?.());

/** A lost sign-in goes back to the passcode screen; anything else shows the error screen. */
function handleError(error, actionLabel, action) {
  if (isApiError(error, "unauthorized")) {
    signOut("Your sign-in expired. Enter the passcode again.");
  } else {
    showError(error, actionLabel, action);
  }
}

// --- Start: classrooms and standards (these rarely change, so they're loaded once) ---

async function startDashboard() {
  setStatus("Loading…");
  try {
    const [classrooms, standards] = await Promise.all([api.get("/classrooms"), api.get("/standards")]);
    if (classrooms.length === 0) throw new Error("No classrooms yet");
    state.standards = new Map(standards.map((s) => [s.code, s]));

    const select = $("classroom-select");
    select.replaceChildren(
      ...classrooms.map((c) => {
        const option = el("option", null, c.name);
        option.value = c.id;
        return option;
      }),
    );
    let saved = null;
    try {
      saved = localStorage.getItem(CLASSROOM_KEY); // just a convenience: fine if storage is blocked
    } catch {}
    state.classroomId = classrooms.some((c) => c.id === saved) ? saved : classrooms[0].id;
    select.value = state.classroomId;

    $("signed-in").hidden = false;
    showScreen("dashboard");
    refresh();
  } catch (error) {
    handleError(error, "Try again", startDashboard);
  }
}

$("classroom-select").addEventListener("change", (event) => {
  state.classroomId = event.target.value;
  try {
    localStorage.setItem(CLASSROOM_KEY, state.classroomId);
  } catch {}
  state.seenSubmissions = null; // a different class: don't flag its whole feed as "new"
  state.lastUpdated = null;
  if ($("codes").open) loadCodes();
  else $("codes-body").replaceChildren();
  refresh();
});

// --- Auto-refresh ---
// Every 15 seconds: fetch everything that changes during class, in parallel, then draw it.
// The next refresh is scheduled only after this one finishes (setTimeout, not setInterval),
// so a slow network can never pile up overlapping requests.

let refreshNumber = 0; // lets an older, slower refresh notice it's been overtaken

async function refresh() {
  clearTimeout(state.timer);
  const thisRefresh = ++refreshNumber;
  const base = `/classrooms/${pathId(state.classroomId)}`;
  try {
    const [mastery, studentMastery, recent, tickets] = await Promise.all([
      api.get(`${base}/mastery`),
      api.get(`${base}/mastery/students`),
      api.get(`${base}/recent-submissions?limit=10`),
      api.get(`${base}/exit-tickets`),
    ]);
    if (thisRefresh !== refreshNumber) return; // a newer refresh started (e.g. classroom changed)

    keepingFocus(() => {
      renderMastery(mastery);
      state.studentMastery = studentMastery;
      renderHeatmap();
      renderFeed(recent);
      renderReleases(tickets);
    });
    state.lastUpdated = new Date();
    $("refresh-alert").hidden = true;
  } catch (error) {
    if (thisRefresh !== refreshNumber) return;
    if (isApiError(error, "unauthorized")) {
      signOut("Your sign-in expired. Enter the passcode again.");
      return;
    }
    // Keep showing the last good data, say how old it is, and try again on schedule.
    console.error(error);
    const since = state.lastUpdated ? ` Showing data from ${timeFormat.format(state.lastUpdated)}.` : "";
    showRefreshAlert(`Couldn't refresh.${since} Trying again in 15 seconds…`);
  }
  updateRefreshStatus();
  scheduleNextRefresh();
}

function scheduleNextRefresh() {
  clearTimeout(state.timer);
  if (!state.paused && !document.hidden) {
    state.timer = setTimeout(refresh, REFRESH_MS);
  }
}

function stopRefreshing() {
  clearTimeout(state.timer);
  refreshNumber += 1; // anything still in flight is ignored when it lands
}

function updateRefreshStatus() {
  const updated = state.lastUpdated ? `Last updated ${timeFormat.format(state.lastUpdated)}` : "Not loaded yet";
  const mode = state.paused ? "Auto-refresh paused" : "Updates every 15 seconds";
  $("refresh-status").textContent = `${updated} · ${mode}`;
}

function showRefreshAlert(message) {
  $("refresh-alert").textContent = message;
  $("refresh-alert").hidden = false;
}

$("refresh-now").addEventListener("click", refresh);

$("pause-refresh").addEventListener("click", (event) => {
  state.paused = !state.paused;
  event.currentTarget.setAttribute("aria-pressed", String(state.paused));
  event.currentTarget.textContent = state.paused ? "Resume auto-refresh" : "Pause auto-refresh";
  if (state.paused) {
    clearTimeout(state.timer);
    updateRefreshStatus();
  } else {
    refresh();
  }
});

// No point refreshing a tab nobody is looking at; catch up as soon as it's visible again.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearTimeout(state.timer);
  } else if (!state.paused && !$("screen-dashboard").hidden) {
    refresh();
  }
});

/** Redraws without losing the keyboard focus (e.g. on a "Release" button that gets rebuilt). */
function keepingFocus(redraw) {
  const key = document.activeElement?.dataset?.focusKey;
  redraw();
  if (key) document.querySelector(`[data-focus-key="${CSS.escape(key)}"]`)?.focus();
}

// --- Class mastery by standard ---

function renderMastery(mastery) {
  const container = $("mastery-tables");
  container.replaceChildren();
  for (const subject of SUBJECTS) {
    const rows = mastery.standards.filter((s) => s.subject === subject.key);
    if (rows.length === 0) continue;

    const table = el("table", "data-table mastery-table");
    const caption = el("caption", null, subject.name);
    const head = el("thead");
    const headRow = el("tr");
    for (const [text, className] of [["Standard"], ["Answers", "num"], ["Correct", "num"], ["Level"]]) {
      const th = el("th", className, text);
      th.scope = "col";
      headRow.append(th);
    }
    head.append(headRow);
    table.append(caption, head);

    // One <tbody> per domain, with the domain's name as its first row.
    for (const domain of [...new Set(rows.map((s) => domainOf(s.standard_code)))]) {
      const body = el("tbody");
      const domainRow = el("tr", "domain-row");
      const domainCell = el("th", null, DOMAIN_NAMES[domain] ? `${domain} · ${DOMAIN_NAMES[domain]}` : domain);
      domainCell.colSpan = 4;
      domainCell.scope = "rowgroup";
      domainRow.append(domainCell);
      body.append(domainRow);

      for (const s of rows.filter((r) => domainOf(r.standard_code) === domain)) {
        const row = el("tr");
        const name = el("th");
        name.scope = "row";
        name.append(el("span", "standard-code", s.standard_code), el("span", "standard-description", s.description));
        const level = el("td");
        level.append(badge(s.level));
        row.append(name, el("td", "num", String(s.answer_count)), el("td", "num", formatPercent(s.percent_correct)), level);
        body.append(row);
      }
      table.append(body);
    }
    container.append(table);
  }
}

// --- Student x standard heatmap ---

/** Standards in display order: Math first, then Science, by code within each. */
function heatmapColumns() {
  return SUBJECTS.map((subject) => ({
    subject,
    codes: [...state.standards.values()]
      .filter((s) => s.subject === subject.key)
      .map((s) => s.code)
      .sort(),
  })).filter((group) => group.codes.length > 0);
}

function renderHeatmap() {
  const data = state.studentMastery;
  if (!data) return;
  const groups = heatmapColumns();
  const codes = groups.flatMap((g) => g.codes);
  const needsSupportOnly = $("needs-support-only").checked;

  const students = data.students.map((student) => {
    const byCode = new Map(student.standards.map((r) => [r.standard_code, r]));
    const needs = student.standards.filter((r) => r.level === "needs support").length;
    return { ...student, byCode, needs };
  });
  const shown = needsSupportOnly ? students.filter((s) => s.needs > 0) : students;

  const table = $("heatmap");
  table.replaceChildren();
  table.append(el("caption", "sr-only", "Percent correct for each student on each standard"));

  // Header: subject groups on the first row, standard codes on the second.
  const head = el("thead");
  const groupRow = el("tr");
  const studentHeader = el("th", "sticky-col", "Student");
  studentHeader.scope = "col";
  studentHeader.rowSpan = 2;
  groupRow.append(studentHeader);
  for (const group of groups) {
    const th = el("th", "group-header", group.subject.name);
    th.scope = "colgroup";
    th.colSpan = group.codes.length;
    groupRow.append(th);
  }
  const needsHeader = el("th", "num", "Needs support");
  needsHeader.scope = "col";
  needsHeader.rowSpan = 2;
  groupRow.append(needsHeader);

  const codeRow = el("tr");
  for (const code of codes) {
    const th = el("th", "code-header");
    th.scope = "col";
    th.title = `${code}: ${state.standards.get(code)?.description ?? ""}`;
    const visible = el("span", null, shortCode(code));
    visible.setAttribute("aria-hidden", "true");
    th.append(visible, el("span", "sr-only", code));
    codeRow.append(th);
  }
  head.append(groupRow, codeRow);
  table.append(head);

  // One row per student.
  const body = el("tbody");
  for (const student of shown) {
    const row = el("tr");
    const name = el("th", "sticky-col", student.display_name);
    name.scope = "row";
    row.append(name);
    for (const code of codes) {
      row.append(heatmapCell(student.display_name, code, student.byCode.get(code)));
    }
    row.append(el("td", `num${student.needs ? " needs-count" : ""}`, String(student.needs)));
    body.append(row);
  }
  table.append(body);

  // Footer: how many students need support on each standard.
  const foot = el("tfoot");
  const footRow = el("tr");
  const footLabel = el("th", "sticky-col", "Students needing support");
  footLabel.scope = "row";
  footRow.append(footLabel);
  for (const code of codes) {
    const count = students.filter((s) => s.byCode.get(code)?.level === "needs support").length;
    footRow.append(el("td", `num${count ? " needs-count" : ""}`, String(count)));
  }
  footRow.append(el("td"));
  foot.append(footRow);
  table.append(foot);

  $("heatmap-empty").hidden = shown.length > 0;
}

function heatmapCell(studentName, code, result) {
  if (!result) {
    const cell = el("td", "cell empty");
    cell.title = `${studentName} · ${code}: not attempted yet`;
    cell.append(el("span", "sr-only", "Not attempted"));
    return cell;
  }
  const { label, symbol, className } = LEVELS[result.level];
  const cell = el("td", `cell ${className}`);
  cell.title =
    `${studentName} · ${code}: ${formatPercent(result.percent_correct)} correct ` +
    `(${result.correct_count} of ${result.answer_count}) · ${label}`;
  const icon = el("span", "cell-symbol", symbol);
  icon.setAttribute("aria-hidden", "true");
  cell.append(icon, el("span", null, formatPercent(result.percent_correct)), el("span", "sr-only", `, ${label}`));
  return cell;
}

$("needs-support-only").addEventListener("change", renderHeatmap);

// --- Recent activity ---

function submissionKey(s) {
  return `${s.student_id}:${s.ticket_id}`;
}

function renderFeed(submissions) {
  const firstLoad = state.seenSubmissions === null;
  const seen = state.seenSubmissions ?? new Set();
  const list = $("activity-feed");
  list.replaceChildren();
  for (const s of submissions) {
    const isNew = !firstLoad && !seen.has(submissionKey(s));
    const item = el("li", `activity-item${isNew ? " is-new" : ""}`);

    const line = el("p", "activity-line");
    line.append(el("strong", null, s.student_name), " finished ", el("span", "activity-ticket", s.ticket_title));

    const submittedAt = new Date(s.submitted_at);
    const time = el("time", null, timeAgo(submittedAt));
    time.dateTime = s.submitted_at;
    time.title = dateTimeFormat.format(submittedAt);
    const meta = el("p", "activity-meta");
    meta.append(`${s.score.correct}/${s.score.total} correct (${formatPercent(s.score.percent)}) · `, time);

    item.append(line, meta);
    list.append(item);
  }
  state.seenSubmissions = new Set([...seen, ...submissions.map(submissionKey)]);
  $("activity-empty").hidden = submissions.length > 0;
}

// --- Releasing exit tickets ---

function renderReleases(tickets) {
  const list = $("ticket-releases");
  list.replaceChildren();
  for (const ticket of tickets) {
    const item = el("li", "release-item");

    const info = el("div", "release-info");
    const subject = SUBJECTS.find((s) => s.key === ticket.subject)?.name ?? ticket.subject;
    info.append(
      el("span", "release-title", ticket.title),
      el("span", "release-meta", `${subject} · ${ticket.question_count} questions`),
    );

    const status = ticket.released
      ? el("span", "pill released", `Released ${timeFormat.format(new Date(ticket.released_at))}`)
      : el("span", "pill hidden-pill", "Hidden");

    const button = el(
      "button",
      `button small ${ticket.released ? "secondary" : "primary"}`,
      ticket.released ? "Hide" : "Release",
    );
    button.type = "button";
    button.dataset.focusKey = `release-${ticket.id}`;
    button.setAttribute("aria-label", `${ticket.released ? "Hide" : "Release"} ${ticket.title}`);
    button.addEventListener("click", () => toggleRelease(ticket, button));

    item.append(info, status, button);
    list.append(item);
  }
}

async function toggleRelease(ticket, button) {
  if (
    ticket.released &&
    !confirm(`Hide "${ticket.title}"? Students who are still answering won't be able to turn it in.`)
  ) {
    return;
  }
  button.disabled = true;
  const url = `/classrooms/${pathId(state.classroomId)}/exit-tickets/${pathId(ticket.id)}/release`;
  try {
    if (ticket.released) await api.delete(url);
    else await api.put(url);
    await refresh(); // redraw right away instead of waiting for the next 15-second tick
  } catch (error) {
    if (isApiError(error, "unauthorized")) {
      signOut("Your sign-in expired. Enter the passcode again.");
      return;
    }
    console.error(error);
    showRefreshAlert(`Couldn't ${ticket.released ? "hide" : "release"} "${ticket.title}". Try again.`);
    button.disabled = false;
  }
}

// --- Student codes (loaded only when opened, and not auto-refreshed) ---

async function loadCodes() {
  const body = $("codes-body");
  const message = (text) => {
    const row = el("tr");
    const cell = el("td", null, text);
    cell.colSpan = 2;
    row.append(cell);
    body.replaceChildren(row);
  };
  message("Loading…");
  try {
    const students = await api.get(`/classrooms/${pathId(state.classroomId)}/students`);
    body.replaceChildren(
      ...students.map((s) => {
        const row = el("tr");
        const name = el("th", null, s.display_name);
        name.scope = "row";
        row.append(name, el("td", "login-code", s.login_code));
        return row;
      }),
    );
  } catch (error) {
    if (isApiError(error, "unauthorized")) {
      signOut("Your sign-in expired. Enter the passcode again.");
      return;
    }
    console.error(error);
    message("Couldn't load the codes. Close and reopen this section to try again.");
  }
}

$("codes").addEventListener("toggle", (event) => {
  if (event.target.open) loadCodes();
});

// --- Start ---

if (api.getSession()) {
  startDashboard();
} else {
  showSignIn();
}
