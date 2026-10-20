// The student flow: secret code -> exit ticket -> one question per screen -> review -> results.
// Text from the API is always set with textContent (never innerHTML), so even a
// question containing "<script>" would just be shown as text, not run.

import { createClient, isApiError, pathId } from "./api.js";
import { $, el, setStatus, showScreen } from "./dom.js";

const api = createClient("exit-ticket-student-session");

const SUBJECT_NAMES = { math: "Math", science: "Science" };

function friendlyMessage(error) {
  const messages = {
    unreachable: "We can't reach the exit ticket server right now. Ask your teacher for help, then try again.",
    unauthorized: "Please type your secret code again.",
    not_found: "This exit ticket isn't open right now. Your teacher may have closed it.",
    conflict: "You already turned in this exit ticket!",
  };
  return messages[error?.kind] ?? "Something went wrong on our end. Try again, or ask your teacher for help.";
}

const state = {
  ticket: null, // full ticket with questions
  answers: new Map(), // question id -> chosen letter
  index: 0, // which question is on screen
  returnToReview: false, // true after "Change answer", so Next goes back to review
};

// --- Small helpers ---

let errorAction = null;

function showError(error, actionLabel, action) {
  console.error(error);
  $("error-message").textContent = friendlyMessage(error);
  $("error-action").textContent = actionLabel;
  errorAction = action;
  showScreen("error");
}

/** Shows an error, except a lost sign-in, which goes straight back to the code screen. */
function handleError(error, actionLabel, action) {
  if (isApiError(error, "unauthorized")) {
    signOut(friendlyMessage(error));
  } else {
    showError(error, actionLabel, action);
  }
}

function choiceText(question, letter) {
  const choice = question.choices.find((c) => c.letter === letter);
  return `${letter}. ${choice.text}`;
}

// --- Screen 1: sign in with your secret code ---

function showSignIn(message = "") {
  $("signed-in").hidden = true;
  showScreen("sign-in");
  const notice = $("sign-in-message");
  notice.hidden = !message;
  notice.textContent = message;
}

function showCodeError(message) {
  const input = $("login-code");
  const error = $("login-code-error");
  error.textContent = message;
  error.hidden = false;
  input.setAttribute("aria-invalid", "true");
  input.focus();
  input.select();
}

function clearCodeError() {
  $("login-code-error").hidden = true;
  $("login-code").removeAttribute("aria-invalid");
}

$("login-code").addEventListener("input", clearCodeError);

$("sign-in-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const code = $("login-code").value.trim();
  if (!code) {
    showCodeError("Type your code first.");
    return;
  }

  const button = $("sign-in-button");
  button.disabled = true; // one sign-in at a time
  setStatus("Checking your code…");
  try {
    await api.signIn("/student-sessions", { login_code: code });
    $("login-code").value = "";
    clearCodeError();
    showSignedIn();
  } catch (error) {
    setStatus("");
    if (isApiError(error, "unauthorized")) {
      showCodeError("Hmm, that code didn't work. Check it and try again.");
    } else {
      // The typed code stays in the box, so "Try again" just means pressing the button again.
      showError(error, "Try again", () => showSignIn());
    }
  } finally {
    button.disabled = false;
  }
});

function showSignedIn() {
  $("who").textContent = `Hi, ${api.getSession().display_name}!`;
  $("signed-in").hidden = false;
  loadTickets();
}

function signOut(message = "") {
  api.signOut();
  state.ticket = null;
  state.answers = new Map();
  $("login-code").value = "";
  clearCodeError();
  showSignIn(message);
}

$("sign-out").addEventListener("click", () => signOut("You're signed out. See you next time!"));
$("done-button").addEventListener("click", () => signOut("You're signed out. Great work today!"));

// --- Screen 2: your exit tickets ---

function ticketMeta(ticket) {
  return `${SUBJECT_NAMES[ticket.subject] ?? ticket.subject} · ${ticket.question_count} questions`;
}

async function loadTickets() {
  setStatus("Loading your exit tickets…");
  try {
    const tickets = await api.get("/me/exit-tickets");
    const list = $("ticket-list");
    list.replaceChildren();
    for (const ticket of tickets) {
      const item = el("li");
      if (ticket.submitted) {
        // Finished tickets are shown, but aren't buttons: there's nothing to do with them.
        const done = el("div", "ticket done");
        done.append(
          el("span", "ticket-title", ticket.title),
          el("span", "ticket-meta", ticketMeta(ticket)),
          el("span", "ticket-done", "✓ Done! You turned this in."),
        );
        item.append(done);
      } else {
        const button = el("button", "ticket");
        button.type = "button";
        button.append(el("span", "ticket-title", ticket.title), el("span", "ticket-meta", ticketMeta(ticket)));
        button.addEventListener("click", () => startTicket(ticket.id));
        item.append(button);
      }
      list.append(item);
    }
    $("no-tickets").hidden = tickets.length > 0;
    showScreen("ticket");
  } catch (error) {
    handleError(error, "Try again", loadTickets);
  }
}

$("check-again").addEventListener("click", loadTickets);
$("another-ticket").addEventListener("click", loadTickets);

async function startTicket(ticketId) {
  setStatus("Getting your questions ready…");
  try {
    state.ticket = await api.get(`/exit-tickets/${pathId(ticketId)}`);
    state.answers = new Map();
    state.index = 0;
    state.returnToReview = false;
    renderQuestion();
  } catch (error) {
    // 404 here means the teacher closed it after the list loaded.
    const backToList = isApiError(error, "not_found");
    handleError(error, backToList ? "Back to my exit tickets" : "Try again", backToList ? loadTickets : () => startTicket(ticketId));
  }
}

// --- Screen 3: one question at a time ---

function renderQuestion() {
  const { questions } = state.ticket;
  const question = questions[state.index];
  const isLast = state.index === questions.length - 1;

  $("progress-text").textContent = `Question ${state.index + 1} of ${questions.length}`;
  $("progress-fill").style.width = `${((state.index + 1) / questions.length) * 100}%`;
  $("question-prompt").textContent = question.prompt;

  const choices = $("choices");
  choices.replaceChildren();
  for (const choice of question.choices) {
    const inputId = `choice-${choice.letter}`;
    const input = el("input");
    input.type = "radio";
    input.name = "choice";
    input.id = inputId;
    input.value = choice.letter;
    input.checked = state.answers.get(question.id) === choice.letter;
    input.addEventListener("change", () => {
      state.answers.set(question.id, choice.letter);
      $("next-button").disabled = false;
    });

    const label = el("label");
    label.htmlFor = inputId;
    label.append(el("span", "letter", choice.letter), el("span", "choice-text", choice.text));

    const wrapper = el("div", "choice");
    wrapper.append(input, label);
    choices.append(wrapper);
  }

  const next = $("next-button");
  next.disabled = !state.answers.has(question.id);
  next.textContent = state.returnToReview ? "Back to review" : isLast ? "Review my answers" : "Next";
  $("back-button").textContent = state.index === 0 ? "Back to my exit tickets" : "Back";

  showScreen("question");
}

// Next is the form's submit button, so pressing Enter after picking an answer also works.
$("question-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const isLast = state.index === state.ticket.questions.length - 1;
  if (state.returnToReview || isLast) {
    state.returnToReview = false;
    renderReview();
  } else {
    state.index += 1;
    renderQuestion();
  }
});

$("back-button").addEventListener("click", () => {
  state.returnToReview = false;
  if (state.index === 0) {
    loadTickets();
  } else {
    state.index -= 1;
    renderQuestion();
  }
});

// --- Screen 4: review before turning it in ---

function renderReview() {
  const list = $("review-list");
  list.replaceChildren();
  state.ticket.questions.forEach((question, index) => {
    const change = el("button", "button secondary small", "Change answer");
    change.type = "button";
    change.setAttribute("aria-label", `Change answer to question ${index + 1}`);
    change.addEventListener("click", () => {
      state.index = index;
      state.returnToReview = true;
      renderQuestion();
    });

    const item = el("li", "card");
    item.append(
      el("p", "card-label", `Question ${index + 1}`),
      el("p", "card-prompt", question.prompt),
      el("p", null, `Your answer: ${choiceText(question, state.answers.get(question.id))}`),
      change,
    );
    list.append(item);
  });
  showScreen("review");
}

$("review-back").addEventListener("click", () => {
  state.index = state.ticket.questions.length - 1;
  renderQuestion();
});

// --- Submit, then Screen 5: results ---

async function submitAnswers() {
  const button = $("submit-button");
  button.disabled = true; // stops a double-tap from sending twice
  setStatus("Turning in your answers…");
  try {
    const answers = state.ticket.questions.map((q) => ({
      question_id: q.id,
      chosen_choice: state.answers.get(q.id),
    }));
    const result = await api.post(`/exit-tickets/${pathId(state.ticket.id)}/submissions`, { answers });
    renderResults(result);
  } catch (error) {
    if (isApiError(error, "not_found") || isApiError(error, "conflict")) {
      // Closed by the teacher, or already turned in: nothing to retry.
      handleError(error, "Back to my exit tickets", loadTickets);
    } else {
      // Answers are still saved in memory, so "Try again" resends them.
      handleError(error, "Try again", submitAnswers);
    }
  } finally {
    button.disabled = false;
  }
}

$("submit-button").addEventListener("click", submitAnswers);

function scoreMessage({ correct, total }) {
  if (correct === total) return "Perfect score! Amazing work!";
  if (correct * 2 >= total) return "Great job! Read the explanations below to learn even more.";
  return "Thanks for trying! Every mistake helps your brain grow. Read the explanations below.";
}

function renderResults(result) {
  const { correct, total } = result.score;
  $("score-heading").textContent = `You got ${correct} out of ${total}!`;
  $("score-message").textContent = scoreMessage(result.score);

  const questions = new Map(state.ticket.questions.map((q) => [q.id, q]));
  const list = $("results-list");
  list.replaceChildren();
  result.results.forEach((r, index) => {
    const question = questions.get(r.question_id);
    // The mark is words plus a symbol, not just a color.
    const item = el("li", `card ${r.is_correct ? "correct" : "incorrect"}`);
    item.append(
      el("p", "card-label", `Question ${index + 1}`),
      el("p", "mark", r.is_correct ? "✓ Correct!" : "✗ Not quite"),
      el("p", "card-prompt", question.prompt),
      el("p", null, `Your answer: ${choiceText(question, r.chosen_choice)}`),
    );
    if (!r.is_correct) {
      item.append(el("p", null, `Correct answer: ${choiceText(question, r.correct_choice)}`));
    }
    item.append(el("p", "explanation", `Why: ${r.explanation}`));
    list.append(item);
  });
  showScreen("results");
}

// --- Start: signed in already (e.g. after a refresh)? Go straight to the tickets. ---

$("error-action").addEventListener("click", () => errorAction?.());

if (api.getSession()) {
  showSignedIn();
} else {
  showSignIn();
}
