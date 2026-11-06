// The account window: the operator's own profile, in the same card the Agents page uses for an agent's.
//
// Nothing here is prefilled and nothing is defaulted: the fields show what is in ~/.athena/user.json and nothing else,
// so an empty install shows an empty card. Read from GET /user/profile, written with POST /user/profile.
(function () {
  var veil, body, status;


  function field(label, key, value, opts) {
    opts = opts || {};
    var wrap = el("div", "lic-field" + (opts.wide ? " wide" : ""));
    wrap.appendChild(el("span", "lic-label", label));
    var input = document.createElement(opts.area ? "textarea" : "input");
    if (!opts.area) input.type = key === "email" ? "email" : "text";
    else input.rows = opts.rows || 3;
    input.className = "lic-input";
    input.dataset.key = key;
    input.value = value || "";
    if (opts.placeholder) input.placeholder = opts.placeholder;
    wrap.appendChild(input);
    return wrap;
  }

  function initials(user) {
    var name = (user.name || user.handle || "").trim();
    if (!name) return "—";
    return name.split(/\s+/).slice(0, 2).map(function (part) { return part[0]; }).join("").toUpperCase();
  }

  function paint(user) {
    body.replaceChildren();
    var card = el("div", "licence");
    card.style.height = "auto";
    card.style.minHeight = "0";

    var top = el("div", "lic-top");
    top.appendChild(el("span", "lic-brand", "ATHENA"));
    top.appendChild(el("span", "lic-kind", "user profile"));
    top.appendChild(el("span", "lic-id", "#you"));
    card.appendChild(top);

    var main = el("div", "lic-main");
    var photo = el("div", "lic-photo");
    var portrait = el("span", "lic-initials", initials(user));
    portrait.setAttribute("role", "button");
    portrait.tabIndex = 0;
    portrait.title = "choose an avatar image";
    portrait.style.cursor = "pointer";
    portrait.addEventListener("click", pickAvatar);
    photo.appendChild(portrait);

    // the accent, stored with the rest of the user's details
    var accentBox = el("div", "lic-accent-under");
    accentBox.appendChild(el("span", "lic-accent-label", "accent"));
    var accentBtn = el("button", "lic-accent-btn");
    accentBtn.type = "button";
    accentBtn.title = "pick the accent colour";
    accentBtn.style.background = user.accent || "var(--panel-2)";
    accentBtn.addEventListener("click", function () { pickAccent(accentBtn, user); });
    accentBox.appendChild(accentBtn);
    photo.appendChild(accentBox);
    main.appendChild(photo);

    var fields = el("div", "lic-fields");
    fields.appendChild(field("name", "name", user.name));
    fields.appendChild(field("handle", "handle", user.handle));
    fields.appendChild(field("email", "email", user.email));
    fields.appendChild(field("timezone", "timezone", user.timezone, { placeholder: "IANA name, e.g. Europe/London" }));
    fields.appendChild(field("location", "location", user.location));
    fields.appendChild(field("pronouns", "pronouns", user.pronouns));
    fields.appendChild(field("notes", "notes", user.notes, { area: true, wide: true }));
    main.appendChild(fields);
    card.appendChild(main);

    var foot = el("div", "lic-foot");
    status = el("span", "lic-status", "stored in ~/.athena/user.json");
    var save = el("button", "btn btn-send", "Save details");
    save.type = "button";
    save.addEventListener("click", function () { saveUser(save); });
    foot.appendChild(status);
    foot.appendChild(save);
    card.appendChild(foot);

    body.appendChild(card);
  }

  var SWATCHES = ["#eb4a3c", "#e0862a", "#e8c341", "#67e4a0", "#3fb6a8", "#4a9ee8", "#7c6cf0", "#c865c8",
                  "#da9a7f", "#b9c3d0", "#8f9aa8", "#5f6674", "#2f3540", "#1b2027", "#ffffff", "#000000"];

  function pickAccent(button, user) {
    var pop = document.querySelector(".colour-pop");
    if (pop) { pop.remove(); return; }
    pop = el("div", "colour-pop");
    var grid = el("div", "colour-grid");
    SWATCHES.forEach(function (hex) {
      var sw = el("button", "colour-swatch");
      sw.type = "button";
      sw.style.background = hex;
      sw.title = hex;
      sw.addEventListener("click", async function () {
        user.accent = hex;
        button.style.background = hex;
        document.documentElement.style.setProperty("--accent", hex);
        pop.remove();
        try {
          await fetch("/user/profile?channel=" + encodeURIComponent(lane()),
                      { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(user) });
        } catch (e) { }
      });
      grid.appendChild(sw);
    });
    pop.appendChild(grid);
    var rect = button.getBoundingClientRect();
    pop.style.top = Math.min(rect.bottom + 6, window.innerHeight - 210) + "px";
    pop.style.left = Math.min(rect.left, window.innerWidth - 200) + "px";
    document.body.appendChild(pop);
  }

  function pickAvatar() {
    var existing = document.querySelector(".avatar-pop");
    if (existing) { existing.remove(); return; }
    var menu = el("div", "avatar-pop");
    var file = document.createElement("input");
    file.type = "file";
    file.accept = "image/*";
    file.hidden = true;
    var choose = el("button", "avatar-item", "Choose image");
    choose.type = "button";
    choose.addEventListener("click", function () { file.click(); });
    file.addEventListener("change", uploadAvatar);

    var reset = el("button", "avatar-item", "Reset");
    reset.type = "button";
    reset.addEventListener("click", function () {
      var portrait = document.querySelector("#acct-body .lic-initials");
      if (portrait) {
        portrait.style.backgroundImage = "";
        portrait.textContent = "—";
      }
      menu.remove();
    });

    menu.appendChild(choose);
    menu.appendChild(reset);
    menu.appendChild(file);
    var anchor = document.querySelector("#acct-body .lic-initials");
    var rect = anchor ? anchor.getBoundingClientRect() : { bottom: 100, left: 100 };
    menu.style.top = Math.min(rect.bottom + 6, window.innerHeight - 90) + "px";
    menu.style.left = Math.min(rect.left, window.innerWidth - 170) + "px";
    document.body.appendChild(menu);
  }

  async function uploadAvatar(event) {
    var file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      var bitmap = await createImageBitmap(file);
      var side = Math.min(bitmap.width, bitmap.height);
      var canvas = document.createElement("canvas");
      canvas.width = 1024; canvas.height = 1024;
      canvas.getContext("2d").drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2,
                                        side, side, 0, 0, 1024, 1024);
      var res = await fetch("/user/avatar", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ data: canvas.toDataURL("image/png") }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      var portrait = document.querySelector("#acct-body .lic-initials");
      if (portrait) {
        portrait.style.backgroundImage = "url(/user/avatar?v=" + Date.now() + ")";
        portrait.style.backgroundSize = "cover";
        portrait.style.backgroundPosition = "center";
        portrait.textContent = "";
      }
      var menu = document.querySelector(".avatar-pop");
      if (menu) menu.remove();
    } catch (e) {
      if (status) status.textContent = "avatar not saved: " + (e.message || e);
    }
  }

  async function saveUser(button) {
    button.disabled = true;
    if (status) status.textContent = "saving…";
    var payload = {};
    Array.prototype.forEach.call(body.querySelectorAll(".lic-input"), function (input) {
      payload[input.dataset.key] = input.value;
    });
    try {
      var res = await fetch("/user/profile?channel=" + encodeURIComponent(lane()), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      if (status) status.textContent = "saved";
      var portrait = body.querySelector(".lic-initials");
      if (portrait) portrait.textContent = initials(payload);
    } catch (e) {
      if (status) status.textContent = "not saved: " + (e.message || e);
    } finally {
      button.disabled = false;
    }
  }

  function lane() {
    try { return localStorage.getItem("athena.lane") || "default"; } catch (e) { return "default"; }
  }

  async function render() {
    var user = {};
    try {
      var data = await (await fetch("/user/profile?channel=" + encodeURIComponent(lane()),
                                    { headers: { accept: "application/json" } })).json();
      user = data.user || {};
    } catch (e) { }
    paint(user);
  }

  function open() { veil.hidden = false; render(); }
  function close() { veil.hidden = true; }

  function wire() {
    var avatar = document.getElementById("avatar");
    veil = document.getElementById("acct-veil");
    body = document.getElementById("acct-body");
    if (!avatar || !veil) return;
    avatar.addEventListener("click", open);
    var closeBtn = document.getElementById("acct-close");
    if (closeBtn) closeBtn.addEventListener("click", close);
    veil.addEventListener("click", function (event) { if (event.target === veil) close(); });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !veil.hidden) close(); });
  }

  window.AthenaAccount = { open: open, close: close };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
