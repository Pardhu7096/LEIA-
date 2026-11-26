const byId = (id) => document.getElementById(id);
const workspace = byId("workspace");
const login = byId("login");
const jobs = byId("jobs");
const details = byId("job-details");
const drafts = byId("draft");
const upload = byId("upload");
const contact = byId("contact");
const marketing = "/staff/marketing";
const csrf = { "X-LEIA-CSRF": "1" };
const pageSize = 20;
let token = null;
let nextPage = null;
let busy = false;

function message(text, isError = false) {
  const notice = byId("notice");
  notice.textContent = text;
  notice.dataset.error = String(isError);
  notice.hidden = !text;
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers);
  if (options.auth !== false && token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && options.auth !== false) {
      showLogin();
      throw new Error("Your session expired. Sign in again.");
    }
    throw new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${response.status}).`);
  }
  return body;
}

function postJson(path, data, options = {}) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    ...options,
  });
}

function showLogin() {
  token = null;
  nextPage = null;
  jobs.replaceChildren();
  details.replaceChildren();
  details.hidden = true;
  drafts.options.length = 1;
  drafts.value = "";
  upload.reset();
  contact.reset();
  workspace.hidden = true;
  byId("sign-out").hidden = true;
  login.hidden = false;
}

async function showWorkspace(session) {
  token = session.access_token;
  const user = await request("/auth/staff/me");
  if (user.role !== "admin") {
    await request("/auth/staff/logout", { method: "POST", headers: csrf });
    showLogin();
    throw new Error("A LEIA admin account is required to open Digital Studio.");
  }
  login.hidden = true;
  workspace.hidden = false;
  byId("sign-out").hidden = false;
  await refreshWorkspace();
}

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

const labelStatus = (status) => status.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
const jobTitle = (job) => job.property_title || "Untitled listing";

async function loadJobs(append = false) {
  const query = new URLSearchParams({ limit: String(pageSize) });
  if (append && nextPage) query.set("before", nextPage);
  const page = await request(`${marketing}/jobs?${query}`);
  if (!append) jobs.replaceChildren();
  for (const job of page) {
    const item = element("li");
    const button = element("button", jobTitle(job));
    button.type = "button";
    button.className = "secondary";
    button.append(element("span", labelStatus(job.status)));
    button.addEventListener("click", () => run(button, async () => {
      renderJob(await request(`${marketing}/jobs/${encodeURIComponent(job.id)}`));
    }));
    item.append(button);
    jobs.append(item);
  }
  byId("empty").hidden = jobs.children.length > 0;
  nextPage = page.length === pageSize ? page.at(-1).id : null;
  byId("more").hidden = !nextPage;
}

function renderJob(job) {
  const snapshot = job.property_snapshot;
  const attached = snapshot.media?.some((file) => file.kind === "walkthrough");
  details.replaceChildren(
    element("h3", jobTitle(job)),
    element("p", `${labelStatus(job.status)} · Listing version ${job.property_version}`),
    element("p", `Walkthrough: ${attached ? "Attached" : "Missing"}`),
    element("p", `Verified agent phone: ${snapshot.agent?.phone || "Missing"}`),
  );
  if (job.last_error) details.append(element("p", job.last_error));
  const action = job.status === "failed" ? "retry"
    : ["queued", "awaiting_review", "approved"].includes(job.status) ? "cancel" : null;
  if (action) {
    const button = element("button", action === "retry" ? "Retry job" : "Cancel job");
    button.type = "button";
    button.addEventListener("click", () => run(button, async () => {
      if (action === "cancel" && !window.confirm("Cancel this marketing job?")) return;
      const updated = await postJson(`${marketing}/jobs/${encodeURIComponent(job.id)}/${action}`, { version: job.version });
      renderJob(updated);
      await loadJobs();
    }));
    details.append(button);
  }
  details.hidden = false;
}

function syncDraftSelection() {
  upload.querySelector("button").disabled = !drafts.value;
  contact.querySelector("button").disabled = !drafts.value;
}

async function loadDrafts() {
  const selected = drafts.value;
  const properties = await request("/staff/properties");
  drafts.options.length = 1;
  for (const property of properties.filter((item) => item.status === "draft")) {
    const option = element("option", [property.title, property.locality].filter(Boolean).join(" · "));
    option.value = property.id;
    option.dataset.version = property.version;
    option.dataset.agent = property.created_by;
    drafts.append(option);
  }
  drafts.value = [...drafts.options].some((item) => item.value === selected) ? selected : "";
  syncDraftSelection();
}

async function refreshWorkspace() {
  details.hidden = true;
  await Promise.all([loadJobs(), loadDrafts()]);
}

async function run(button, work) {
  if (busy) return;
  busy = true;
  workspace.inert = true;
  button.disabled = true;
  message("");
  try {
    await work();
  } catch (error) {
    message(error.message, true);
  } finally {
    busy = false;
    workspace.inert = false;
    button.disabled = false;
    syncDraftSelection();
  }
}

byId("sign-in").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  void run(form.querySelector("button"), async () => {
    try {
      const session = await postJson("/auth/staff/login",
        { email: values.get("email"), password: values.get("password") }, { auth: false });
      await showWorkspace(session);
    } finally {
      form.reset();
    }
  });
});
byId("sign-out").addEventListener("click", (event) => run(event.currentTarget, async () => {
  await request("/auth/staff/logout", { method: "POST", headers: csrf });
  showLogin();
}));
byId("refresh").addEventListener("click", (event) => run(event.currentTarget, refreshWorkspace));
byId("more").addEventListener("click", (event) => run(event.currentTarget, () => loadJobs(true)));
drafts.addEventListener("change", () => {
  contact.reset();
  upload.reset();
  syncDraftSelection();
});

upload.addEventListener("submit", (event) => {
  event.preventDefault();
  const draft = drafts.selectedOptions[0];
  const file = new FormData(upload).get("walkthrough");
  void run(upload.querySelector("button"), async () => {
    if (!draft?.value || !file?.size) throw new Error("Choose a draft and an MP4 file.");
    if (file.size > 100 * 1024 * 1024) throw new Error("Choose an MP4 under 100 MiB.");
    const query = new URLSearchParams({ version: draft.dataset.version });
    const saved = await request(`${marketing}/properties/${encodeURIComponent(draft.value)}/walkthrough?${query}`, {
      method: "PUT", headers: { "Content-Type": "video/mp4" }, body: file,
    });
    draft.dataset.version = saved.version;
    upload.reset();
    message("Walkthrough uploaded.");
  });
});

contact.addEventListener("submit", (event) => {
  event.preventDefault();
  const agent = drafts.selectedOptions[0]?.dataset.agent;
  const values = new FormData(contact);
  void run(contact.querySelector("button"), async () => {
    if (!agent || !values.has("confirmed")) throw new Error("Choose the listing and confirm the agent’s consent.");
    await request(`${marketing}/agents/${encodeURIComponent(agent)}/verified-contact`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: values.get("phone").trim() }),
    });
    contact.reset();
    message("Verified agent phone saved.");
  });
});

showLogin();
