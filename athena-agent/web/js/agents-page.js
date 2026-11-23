// The Agents page: one profile at a time, presented as a printed card.
//
// The bar lists every agent with the built-in default pinned first — it is the one profile that always exists, so
// it does not move when the rest re-sort. Read from GET /agents and GET /agents/<name>/profile; written with
// POST /agents/<name>/profile. The key is never displayed: the response carries its name and whether one is on file.
(function () {
  var bar, card, agents = [], selected = null, current = null, order = 1;
  var tabsBox, search, sortButton, query = "";


  function row(label, value, opts) {
    opts = opts || {};
    var wrap = el("label", "lic-field" + (opts.wide ? " wide" : ""));
    wrap.appendChild(el("span", "lic-label", label));
    var input = document.createElement(opts.area ? "textarea" : "input");
    if (!opts.area) input.type = "text";
    else { input.rows = 2; }
    input.value = value === undefined || value === null ? "" : String(value);
    input.className = "lic-input";
    input.dataset.field = opts.field || "";
    if (opts.placeholder) input.placeholder = opts.placeholder;
    if (opts.readonly) input.readOnly = true;
    wrap.appendChild(input);
    return wrap;
  }

  var registry = { providers: {} };

  // A vendor is a name, an endpoint and the wire format it speaks. DeepSeek speaks the OpenAI format, so the
  // profile is written with `openai` while the user picks "DeepSeek" — the field means format, the list means vendor.
  var PRESETS = {
    DeepSeek: { url: "https://api.deepseek.com/v1", format: "openai" },
    OpenAI: { url: "https://api.openai.com/v1", format: "openai" },
    Anthropic: { url: "https://api.anthropic.com/v1", format: "anthropic" },
    Custom: { url: "", format: "openai" },
  };

  function vendorFor(model) {
    var url = String((model || {}).base_url || "");
    for (var name in PRESETS) {
      if (PRESETS[name].url && url && url.indexOf(PRESETS[name].url.replace("/v1", "")) === 0) return name;
    }
    return "Custom";
  }

  function selectRow(label, options, value, opts) {
    var wrap = el("label", "lic-field" + (opts && opts.wide ? " wide" : ""));
    wrap.appendChild(el("span", "lic-label", label));
    var sel = document.createElement("select");
    sel.className = "lic-input lic-select";
    sel.dataset.field = (opts && opts.field) || "";
    (options || []).forEach(function (v) {
      var o = document.createElement("option");
      o.value = v;
      o.textContent = v;
      if (v === value) o.selected = true;
      sel.appendChild(o);
    });
    if (value && (options || []).indexOf(value) < 0) {
      var extra = document.createElement("option");
      extra.value = value;
      extra.textContent = value;
      extra.selected = true;
      sel.appendChild(extra);
    }
    wrap.appendChild(sel);
    return wrap;
  }

  function paint(p) {
    current = p;
    var persona = p.persona || {}, model = p.model || {};
    card.replaceChildren();

    var top = el("div", "lic-top");
    top.appendChild(el("span", "lic-brand", "ATHENA"));
    top.appendChild(el("span", "lic-kind", "agent profile"));
    top.appendChild(el("span", "lic-id", "#" + (p.channel || "")));
    card.appendChild(top);

    var main = el("div", "lic-main");
    var photo = el("div", "lic-photo");
    var portrait = el("span", "lic-initials", (persona.name || p.channel || "?").slice(0, 2).toUpperCase());
    portrait.style.background = persona.accent || "var(--panel-2)";   // the accent fills the portrait, not its column
    // the library: saved avatars for this profile, paged with the arrows above the portrait
    var libNumbers = [], libIndex = -1;
    var libRow = el("div", "lib-row");
    var libPrev = el("button", "lib-btn", "\u2039");
    libPrev.type = "button";
    libPrev.setAttribute("aria-label", "previous avatar");
    var libLabel = el("span", "lib-label", "Library");
    var libNext = el("button", "lib-btn", "\u203a");
    libNext.type = "button";
    libNext.setAttribute("aria-label", "next avatar");
    libRow.appendChild(libPrev);
    libRow.appendChild(libLabel);
    libRow.appendChild(libNext);

    function showLibrary(number) {
      if (!number) return;
      portrait.style.backgroundImage = "url(/agents/" + encodeURIComponent(p.channel)
        + "/avatar?n=" + number + "&v=" + Date.now() + ")";
      portrait.style.backgroundSize = "cover";
      portrait.style.backgroundPosition = "center";
      portrait.textContent = "";
      libLabel.textContent = "Library " + (libIndex + 1) + "/" + libNumbers.length;
    }
    async function loadLibrary() {
      try {
        var data = await (await fetch("/agents/" + encodeURIComponent(p.channel) + "/avatars",
                                      { headers: { accept: "application/json" } })).json();
        libNumbers = data.numbers || [];
        var chosen = data.selected || (libNumbers.length ? libNumbers[libNumbers.length - 1] : null);
        libIndex = Math.max(0, libNumbers.indexOf(chosen));
        if (libNumbers.length) {
          libLabel.textContent = "Library " + (libIndex + 1) + "/" + libNumbers.length;
          showLibrary(libNumbers[libIndex]);
        }
      } catch (e) { }
    }
    function step(delta) {
      if (!libNumbers.length) return;
      libIndex = (libIndex + delta + libNumbers.length) % libNumbers.length;
      showLibrary(libNumbers[libIndex]);
      // remembered per profile, so switching agents brings back each one's own choice
      fetch("/agents/" + encodeURIComponent(p.channel) + "/avatar/select", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ n: libNumbers[libIndex] }),
      }).catch(function () { });
    }
    libPrev.addEventListener("click", function () { step(-1); });
    libNext.addEventListener("click", function () { step(1); });
    loadLibrary();

    var AVATAR_KEY = "athena.avatar." + p.channel;
    function applyStored() {
      var stored = null;
      try { stored = localStorage.getItem(AVATAR_KEY); } catch (e) { }
      if (stored) {
        portrait.style.backgroundImage = "url(" + stored + ")";
        portrait.style.backgroundSize = "cover";
        portrait.style.backgroundPosition = "center";
        portrait.textContent = "";
      }
    }
    applyStored();
    portrait.style.cursor = "pointer";
    portrait.setAttribute("role", "button");
    portrait.tabIndex = 0;
    portrait.title = "change the avatar image";
    function avatarMenu() {
      var existing = document.querySelector(".avatar-pop");
      if (existing) { existing.remove(); return; }
      var menu = el("div", "avatar-pop");
      var file = document.createElement("input");
      file.type = "file";
      file.accept = "image/*";
      file.hidden = true;
      var choose = el("button", "avatar-item", "Choose image");
      choose.type = "button";
      var reset = el("button", "avatar-item", "Reset");
      reset.type = "button";
      choose.addEventListener("click", function () { file.click(); });
      // a 3x3 grid: the cell you pick is what the avatar will show
      file.addEventListener("change", function () {
        var picked = file.files && file.files[0];
        if (!picked) return;
        menu.remove();
        openCropper(picked);
      });

      function openCropper(picked) {
        var veil = el("div", "crop-veil");
        var box = el("div", "crop-box");
        var head = el("div", "crop-head");
        head.appendChild(el("span", "crop-title", "position the avatar"));
        var close = el("button", "confirm-x", "×");
        close.type = "button";
        close.addEventListener("click", function () { URL.revokeObjectURL(img.src); veil.remove(); });
        head.appendChild(close);
        box.appendChild(head);

        // the stage is the square that will become the avatar; the grid is a guide, not a menu
        var stage = el("div", "crop-stage");
        var img = new Image();
        img.src = URL.createObjectURL(picked);
        img.className = "crop-image";
        img.draggable = false;
        stage.appendChild(img);
        var grid = el("div", "crop-grid-guide");
        stage.appendChild(grid);
        var controls = el("div", "crop-controls");
        var xSlider = document.createElement("input");
        xSlider.type = "range"; xSlider.step = "1"; xSlider.className = "crop-slider crop-x";
        xSlider.setAttribute("aria-label", "move left and right");
        var ySlider = document.createElement("input");
        ySlider.type = "range"; ySlider.step = "1";
        ySlider.className = "crop-slider crop-y";
        ySlider.setAttribute("aria-label", "move up and down");
        var wrapX = el("div", "crop-slider-row");
        wrapX.appendChild(el("span", "crop-slider-name", "left/right"));
        wrapX.appendChild(xSlider);
        var wrapY = el("div", "crop-slider-row crop-slider-vertical");
        wrapY.appendChild(el("span", "crop-slider-name", "up/down"));
        wrapY.appendChild(ySlider);

        // vertical slider to the left of the image, horizontal one underneath it
        var cropBody = el("div", "crop-body");
        cropBody.appendChild(wrapY);
        cropBody.appendChild(stage);
        box.appendChild(cropBody);
        controls.appendChild(wrapX);
        box.appendChild(controls);

        // what the controls do, in the order a person needs them
        var hint = el("ul", "crop-hint");
        ["Drag the image to position it.",
         "Use the horizontal slider to move it left and right.",
         "Use the vertical slider to move it up and down.",
         "Scroll over the image to zoom in or out."].forEach(function (text) {
          hint.appendChild(el("li", "crop-hint-item", text));
        });
        box.appendChild(hint);

        var actions = el("div", "crop-actions");
        var use = el("button", "btn btn-send", "Use this crop");
        use.type = "button";
        actions.appendChild(use);
        box.appendChild(actions);
        veil.appendChild(box);
        document.body.appendChild(veil);

        var scale = 1, baseScale = 1, offX = 0, offY = 0, stageW = 0, stageH = 0;

        function limits() {
          return {
            maxX: Math.max(0, img.naturalWidth * scale - stageW),
            maxY: Math.max(0, img.naturalHeight * scale - stageH),
          };
        }
        function paint() {
          img.style.width = (img.naturalWidth * scale) + "px";
          img.style.height = (img.naturalHeight * scale) + "px";
          var l = limits();
          offX = Math.min(0, Math.max(-l.maxX, offX));
          offY = Math.min(0, Math.max(-l.maxY, offY));
          img.style.transform = "translate(" + offX + "px, " + offY + "px)";
          xSlider.min = String(-l.maxX); xSlider.max = "0";
          ySlider.min = String(-l.maxY); ySlider.max = "0";
          xSlider.value = String(offX);
          ySlider.value = String(offY);
        }
        function fit() {
          stageW = stage.clientWidth;
          stageH = stage.clientHeight;
          baseScale = Math.max(stageW / img.naturalWidth, stageH / img.naturalHeight);
          scale = baseScale;
          offX = (stageW - img.naturalWidth * scale) / 2;
          offY = (stageH - img.naturalHeight * scale) / 2;
          paint();
        }
        if (img.complete && img.naturalWidth) fit();
        else img.decode().then(fit).catch(fit);

        xSlider.addEventListener("input", function () { offX = Number(xSlider.value); paint(); });
        ySlider.addEventListener("input", function () { offY = Number(ySlider.value); paint(); });

        var dragging = false, startX = 0, startY = 0, baseX = 0, baseY = 0;
        stage.addEventListener("pointerdown", function (e) {
          dragging = true; startX = e.clientX; startY = e.clientY; baseX = offX; baseY = offY;
          if (stage.setPointerCapture) { try { stage.setPointerCapture(e.pointerId); } catch (err) { } }
        });
        stage.addEventListener("pointermove", function (e) {
          if (!dragging) return;
          offX = baseX + (e.clientX - startX);
          offY = baseY + (e.clientY - startY);
          paint();
        });
        ["pointerup", "pointercancel", "pointerleave"].forEach(function (ev) {
          stage.addEventListener(ev, function () { dragging = false; });
        });

        // wheel zooms, keeping whatever is under the pointer where it is
        stage.addEventListener("wheel", function (event) {
          event.preventDefault();
          var rect = stage.getBoundingClientRect();
          var cx = event.clientX - rect.left, cy = event.clientY - rect.top;
          var previous = scale;
          var next = Math.min(baseScale * 5, Math.max(baseScale, scale * (event.deltaY < 0 ? 1.12 : 0.89)));
          if (next === previous) return;
          offX = cx - (cx - offX) * (next / previous);
          offY = cy - (cy - offY) * (next / previous);
          scale = next;
          paint();
        }, { passive: false });

        use.addEventListener("click", async function () {
          try {
            var canvas = document.createElement("canvas");
            canvas.width = 1024; canvas.height = 1024;
            var srcX = (-offX) / scale, srcY = (-offY) / scale, srcSide = stageW / scale;
            canvas.getContext("2d").drawImage(img, srcX, srcY, srcSide, srcSide, 0, 0, 1024, 1024);
            var dataUrl = canvas.toDataURL("image/png");
            var res = await fetch("/agents/" + encodeURIComponent(p.channel) + "/avatar", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ data: dataUrl }),
            });
            var out = await res.json().catch(function () { return {}; });
            if (!res.ok) throw new Error(out.detail || ("HTTP " + res.status));
            try { localStorage.removeItem(AVATAR_KEY); } catch (e) { }
            portrait.style.backgroundImage = "url(/agents/" + encodeURIComponent(p.channel)
              + "/avatar?v=" + Date.now() + ")";
            portrait.style.backgroundSize = "cover";
            portrait.style.backgroundPosition = "center";
            portrait.textContent = "";
            URL.revokeObjectURL(img.src);
            veil.remove();
          } catch (e) {
            notice("could not save the avatar: " + (e.message || e));
          }
        });
      }

      reset.addEventListener("click", function () {
        try { localStorage.removeItem(AVATAR_KEY); } catch (e) { }
        portrait.style.backgroundImage = "";
        portrait.textContent = (persona.name || p.channel || "?").slice(0, 2).toUpperCase();
        menu.remove();
      });
      menu.appendChild(choose);
      menu.appendChild(reset);
      menu.appendChild(file);
      var rect = portrait.getBoundingClientRect();
      menu.style.top = Math.min(rect.bottom + 6, window.innerHeight - 90) + "px";
      menu.style.left = Math.min(rect.left, window.innerWidth - 170) + "px";
      document.body.appendChild(menu);
    }
    portrait.addEventListener("click", avatarMenu);
    portrait.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); avatarMenu(); }
    });
    photo.appendChild(libRow);
    photo.appendChild(portrait);
    // accent: a label and a rectangle of the colour itself, under the portrait
    var accentField = el("div", "lic-accent-under");
    accentField.appendChild(el("span", "lic-accent-label", "accent"));
    var accentRow = el("div", "lic-accent");
    var accentBtn = el("button", "lic-accent-btn");
    accentBtn.type = "button";
    accentBtn.title = "pick the accent colour";
    var hexMatch = /rgba?\(([^)]+)\)/.exec(persona.accent || "");
    var rgb = hexMatch ? hexMatch[1].split(",").map(Number) : [0xda, 0x9a, 0x7f];
    var alpha = hexMatch && hexMatch[1].split(",")[3] !== undefined ? Number(hexMatch[1].split(",")[3]) : 1;
    // a square palette, opened by the button rather than a native colour dialog
    var picker = { value: "#" + rgb.slice(0, 3).map(function (n) {
      return Math.max(0, Math.min(255, n)).toString(16).padStart(2, "0");
    }).join("") };
    var SWATCHES = ["#eb4a3c", "#e0862a", "#e8c341", "#67e4a0", "#3fb6a8", "#4a9ee8", "#7c6cf0", "#c865c8",
                    "#da9a7f", "#b9c3d0", "#8f9aa8", "#5f6674", "#2f3540", "#1b2027", "#ffffff", "#000000"];
    var pop = null;
    function closePop() {
      if (pop && pop.parentNode) pop.parentNode.removeChild(pop);
      pop = null;
      document.removeEventListener("keydown", escPop, true);
      document.removeEventListener("pointerdown", outside, true);
    }
    function escPop(e) { if (e.key === "Escape") closePop(); }
    function outside(e) { if (pop && !pop.contains(e.target) && !accentBtn.contains(e.target)) closePop(); }

    function hslToHex(h, sat, light) {
      var c = (1 - Math.abs(2 * light - 1)) * sat;
      var x = c * (1 - Math.abs(((h / 60) % 2) - 1));
      var m = light - c / 2;
      var rgb = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
              : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
      return "#" + rgb.map(function (v) {
        return Math.round((v + m) * 255).toString(16).padStart(2, "0");
      }).join("");
    }

    function openPop() {
      if (pop) { closePop(); return; }
      pop = el("div", "colour-pop");
      var current = hexMatch ? persona.accent : "#da9a7f";

      // top half: the spectrum. Hue runs left to right; lightness runs down to white and across to black.
      var canvas = document.createElement("canvas");
      canvas.width = 164; canvas.height = 84;
      canvas.className = "colour-field";
      var ctx = canvas.getContext("2d");
      for (var px = 0; px < canvas.width; px++) {
        for (var py = 0; py < canvas.height; py++) {
          var hue = (px / canvas.width) * 360;
          var sat = 1 - (py / canvas.height) * 0.35;
          var light = 0.5 + 0.5 * (1 - px / canvas.width) * (1 - py / canvas.height) * 0.9;
          ctx.fillStyle = hslToHex(hue, sat, light);
          ctx.fillRect(px, py, 1, 1);
        }
      }
      pop.appendChild(canvas);

      // picking from the graph: the same mapping that painted it, inverted
      function pickAt(event) {
        var rect = canvas.getBoundingClientRect();
        var fx = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
        var fy = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
        var hue = fx * 360;
        var sat = 1 - fy * 0.35;
        var light = 0.5 + 0.5 * (1 - fx) * (1 - fy) * 0.9;
        return { h: Math.round(hue), s: Math.round(sat * 100), l: Math.round(light * 100) };
      }

      var hexField = document.createElement("input");
      hexField.type = "text";
      hexField.className = "colour-hex";
      hexField.spellcheck = false;
      hexField.setAttribute("aria-label", "hex colour");

      var sliders = el("div", "colour-sliders");
      var state = { h: 20, s: 70, l: 60 };
      ["H", "S", "L"].forEach(function (name, i) {
        var rowEl = el("div", "colour-slider-row");
        rowEl.appendChild(el("span", "colour-slider-name", name));
        var range = document.createElement("input");
        range.type = "range"; range.min = "0"; range.max = i === 0 ? "360" : "100"; range.step = "1";
        range.value = String(i === 0 ? state.h : i === 1 ? state.s : state.l);
        range.className = "colour-range";
        var num = document.createElement("input");
        num.type = "number"; num.min = "0"; num.max = i === 0 ? "360" : "100";
        num.value = range.value; num.className = "colour-num";
        function sync(from, to) {
          var v = Number(from.value);
          to.value = String(v);
          state[name.toLowerCase()] = v;
          var hex = hslToHex(state.h, state.s / 100, state.l / 100);
          picker.value = hex;
          accentBtn.style.background = hex;
          accentOut.value = hex;
        }
        function refreshFields() {
          var hex = hslToHex(state.h, state.s / 100, state.l / 100);
          picker.value = hex;
          accentBtn.style.background = hex;
          accentOut.value = hex;
          hexField.value = hex;
          var ranges = sliders.querySelectorAll(".colour-range");
          var nums = sliders.querySelectorAll(".colour-num");
          [state.h, state.s, state.l].forEach(function (v, k) {
            if (ranges[k]) ranges[k].value = String(v);
            if (nums[k]) nums[k].value = String(v);
          });
        }
        range.addEventListener("input", function () { sync(range, num); refreshFields(); });
        num.addEventListener("input", function () { sync(num, range); refreshFields(); });
        rowEl.appendChild(range);
        rowEl.appendChild(num);
        sliders.appendChild(rowEl);
      });
      // press and drag: the value follows the pointer while the button is held
      var dragging = false;
      function applyPick(event) {
        var picked = pickAt(event);
        state.h = picked.h; state.s = picked.s; state.l = picked.l;
        var ranges = sliders.querySelectorAll(".colour-range");
        var nums = sliders.querySelectorAll(".colour-num");
        [state.h, state.s, state.l].forEach(function (v, k) {
          if (ranges[k]) ranges[k].value = String(v);
          if (nums[k]) nums[k].value = String(v);
        });
        var hex = hslToHex(state.h, state.s / 100, state.l / 100);
        picker.value = hex;
        accentBtn.style.background = hex;
        accentOut.value = hex;
        hexField.value = hex;
      }
      canvas.addEventListener("pointerdown", function (event) {
        dragging = true;
        if (canvas.setPointerCapture) { try { canvas.setPointerCapture(event.pointerId); } catch (e) { } }
        applyPick(event);
      });
      canvas.addEventListener("pointermove", function (event) {
        if (dragging) applyPick(event);
      });
      ["pointerup", "pointercancel", "pointerleave"].forEach(function (ev) {
        canvas.addEventListener(ev, function () { dragging = false; });
      });
      hexField.value = hslToHex(state.h, state.s / 100, state.l / 100);

      // eyedropper: takes the colour from anywhere on screen, when the browser offers the API
      var hexRow = el("div", "colour-hexrow");
      var dropper = el("button", "colour-dropper");
      dropper.type = "button";
      dropper.title = "pick a colour from the screen";
      dropper.setAttribute("aria-label", "eyedropper");
      dropper.innerHTML = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" '
        + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + '<path d="M2 22l1-1h3l9-9"/><path d="M3 21v-3l9-9"/>'
        + '<path d="M15 6l3.4-3.4a2.1 2.1 0 013 3L18 9l.4.4a2.1 2.1 0 11-3 3l-5.8-5.8a2.1 2.1 0 113-3l.4.4z"/></svg>';
      dropper.addEventListener("click", async function () {
        if (!window.EyeDropper) {
          notice("This browser does not offer an eyedropper. Use the graph, or type the hex value.");
          return;
        }
        try {
          var result = await new window.EyeDropper().open();
          var hex = result.sRGBHex;
          hexField.value = hex.toLowerCase();
          picker.value = hex;
          accentBtn.style.background = hex;
          accentOut.value = hex;
        } catch (e) { /* cancelled */ }
      });
      hexRow.appendChild(dropper);
      hexRow.appendChild(hexField);
      pop.appendChild(hexRow);
      pop.appendChild(sliders);

      var done = el("button", "colour-apply", "Use colour");
      done.type = "button";
      done.addEventListener("click", function () { readAccent(); closePop(); });
      pop.appendChild(done);

      var rect = accentBtn.getBoundingClientRect();
      pop.style.top = Math.min(rect.bottom + 6, window.innerHeight - 250) + "px";
      pop.style.left = Math.min(rect.left, window.innerWidth - 210) + "px";
      document.body.appendChild(pop);
      document.addEventListener("keydown", escPop, true);
      document.addEventListener("pointerdown", outside, true);
    }
    accentBtn.addEventListener("click", openPop);
    var opacity = document.createElement("input");
    opacity.type = "range";
    opacity.min = "0"; opacity.max = "100"; opacity.step = "1";
    opacity.value = String(Math.round(alpha * 100));
    opacity.className = "lic-alpha";
    opacity.title = "opacity";
    var accentOut = document.createElement("input");
    accentOut.type = "text";
    accentOut.className = "lic-input lic-accent-read";
    accentOut.dataset.field = "persona.accent";
    accentOut.readOnly = true;
    function readAccent() {
      var hex = picker.value.replace("#", "");
      var r = parseInt(hex.slice(0, 2), 16), g = parseInt(hex.slice(2, 4), 16), b = parseInt(hex.slice(4, 6), 16);
      accentOut.value = "rgba(" + r + ", " + g + ", " + b + ", " + (Number(opacity.value) / 100).toFixed(2) + ")";
      accentOut.style.background = accentOut.value;
      accentBtn.style.background = accentOut.value;
    }
    opacity.addEventListener("input", readAccent);
    readAccent();
    accentRow.appendChild(accentBtn);
    accentField.appendChild(accentRow);
    /* the slider and the value live inside the popup now; the card shows only the colour */
    photo.appendChild(accentField);

    main.appendChild(photo);

    var fields = el("div", "lic-fields");
    fields.appendChild(row("first name", persona.first_name || (persona.name || "").split(" ")[0],
                           { field: "persona.first_name" }));
    fields.appendChild(row("last name", persona.last_name || (persona.name || "").split(" ").slice(1).join(" "),
                           { field: "persona.last_name" }));
    fields.appendChild(row("nickname", persona.nickname, { field: "persona.nickname" }));
    fields.appendChild(row("role", persona.role, { field: "persona.role" }));
    fields.appendChild(row("tone", persona.tone, { field: "persona.tone" }));
    fields.appendChild(row("likes", persona.likes, { field: "persona.likes" }));
    fields.appendChild(row("dislikes", persona.dislikes, { field: "persona.dislikes" }));
    fields.appendChild(row("boundaries", persona.boundaries,
                           { field: "persona.boundaries", area: true, wide: true }));
    fields.appendChild(row("bio", persona.bio, { field: "persona.bio", area: true, wide: true, rows: 4 }));

    // birth date: three choices rather than a string nobody parses
    var birth = el("div", "lic-field wide");
    birth.appendChild(el("span", "lic-label", "birth date"));
    var dateRow = el("div", "lic-date");
    var parts = String(persona.birth_date || "").split("-");
    var months = ["January","February","March","April","May","June","July","August","September","October","November","December"];
    [["persona.birth_month", months.map(function (m, i) { return [i + 1, m]; }),
      parts[1] ? Number(parts[1]) : 0, "month"],
     ["persona.birth_day", Array.from({ length: 31 }, function (_, i) { return [i + 1, String(i + 1)]; }),
      parts[2] ? Number(parts[2]) : 0, "day"],
     ["persona.birth_year", Array.from({ length: 80 }, function (_, i) { return [2009 - i, String(2009 - i)]; }),
      parts[0] ? Number(parts[0]) : 0, "year"]].forEach(function (spec) {
      var sel = document.createElement("select");
      sel.className = "lic-input lic-select";
      sel.dataset.field = spec[0];
      if (spec[3] === "month") {
        var blank = document.createElement("option"); blank.value = ""; blank.textContent = "—"; sel.appendChild(blank);
      }
      spec[1].forEach(function (pair) {
        var o = document.createElement("option");
        o.value = String(pair[0]); o.textContent = pair[1];
        if (Number(pair[0]) === spec[2]) o.selected = true;
        sel.appendChild(o);
      });
      dateRow.appendChild(sel);
    });
    birth.appendChild(dateRow);
    fields.appendChild(birth);



    main.appendChild(fields);
    card.appendChild(main);

    var second = el("div", "lic-fields lic-fields-2");
    // the provider block folds away; the card is short and this is the part a persona edit does not need
    var provHead = el("div", "lic-section-head");
    provHead.setAttribute("role", "button");
    provHead.tabIndex = 0;
    provHead.setAttribute("aria-expanded", "true");
    provHead.appendChild(el("span", "lic-chevron", "▾"));
    provHead.appendChild(el("span", "lic-section-title", "provider"));
    function toggleProvider() {
      var folded = second.classList.toggle("collapsed");
      provHead.setAttribute("aria-expanded", folded ? "false" : "true");
    }
    provHead.addEventListener("click", toggleProvider);
    provHead.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleProvider(); }
    });
    second.appendChild(provHead);
    var vendor = vendorFor(model);
    var vendorField = selectRow("provider", Object.keys(PRESETS), vendor, { field: "" });
    var vendorSel = vendorField.querySelector("select");
    vendorSel.id = "model-vendor";
    second.appendChild(vendorField);

    var entry = (registry.providers || {})[model.provider] || {};
    second.appendChild(selectRow("model", (entry.models || [model.name || ""]).filter(Boolean),
                                 model.name, { field: "model.name" }));

    var urlField = row("base url", model.base_url || entry.base_url, { field: "model.base_url", wide: true });
    var testBtn = el("button", "lic-test", "Test");
    testBtn.type = "button";
    testBtn.title = "fetch the model list from this endpoint";
    var testNote = el("span", "lic-test-note", "");
    var testWrap = el("div", "lic-test-row");
    testWrap.appendChild(testBtn);
    testWrap.appendChild(testNote);
    urlField.appendChild(testWrap);
    second.appendChild(urlField);
    // key type, then the key itself, then the control that reveals or masks it
    second.appendChild(selectRow("key type", ["Global", "Local"],
                                 (p.key_scope === "local" ? "Local" : "Global"),
                                 { field: "key_scope" }));

    var keyField = el("label", "lic-field");
    keyField.appendChild(el("span", "lic-label", "api key"));
    var keyInput = document.createElement("input");
    keyInput.type = "text";
    keyInput.className = "lic-input lic-secret";
    keyInput.dataset.field = "api_key";
    keyInput.id = "lic-key";
    keyInput.autocomplete = "off";
    keyInput.setAttribute("autocapitalize", "off");
    keyInput.setAttribute("autocorrect", "off");
    keyInput.name = "athena-agent-key";
    keyInput.spellcheck = false;
    keyInput.value = p.has_key ? "************" : "";
    keyInput.placeholder = p.has_key ? "a key is on file — type to replace it" : "paste the key";
    keyField.appendChild(keyInput);
    var keyBtn = el("button", "lic-test show-key", "Show key");
    keyBtn.type = "button";
    keyBtn.id = "lic-show-key";
    keyBtn.dataset.hold = "15000";
    var keyNote = el("span", "lic-test-note", p.has_key ? "hold " + 15 + "s to reveal" : "");
    var keyRow = el("div", "lic-test-row");
    keyRow.appendChild(keyBtn);
    keyRow.appendChild(keyNote);
    keyField.appendChild(keyRow);
    second.appendChild(keyField);
    card.appendChild(second);

    var foot = el("div", "lic-foot");
    var status = el("span", "lic-status", p.inherits_global ? "inherits the global model" : "own model");
    var save = el("button", "btn btn-send", "Save profile");
    save.type = "button";
    save.addEventListener("click", function () { saveProfile(save, status); });
    var note = el("span", "lic-note", "");
    foot.appendChild(status);
    foot.appendChild(note);
    foot.appendChild(save);
    card.appendChild(foot);
    card.dataset.status = "";
    card._note = note;

    // the reveal is a dialog that states the rule, then a button held for fifteen seconds
    if (keyBtn) {
      var shown = false;
      async function revealKey() {
        try {
          var res = await fetch("/agents/" + encodeURIComponent(p.channel) + "/secret-key",
                                { headers: { accept: "application/json" } });
          var data = await res.json().catch(function () { return {}; });
          if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
          keyInput.classList.add("revealed");
          keyInput.value = data.key;
          shown = true;
          keyBtn.textContent = "Hide key";
          keyNote.textContent = "revealed · " + (data.name || "");
        } catch (e) {
          notice("not revealed: " + (e.message || e));
        }
      }
      keyBtn.addEventListener("click", function () {
        if (shown) {
          shown = false;
          keyInput.classList.remove("revealed");
          keyInput.value = "************";
          keyBtn.textContent = "Show key";
          keyNote.textContent = "hold 15s to reveal";
          return;
        }
        if (!p.has_key) {
          notice("No API key is stored for this agent, so there is nothing to display.");
          return;
        }
        dialog("Display the saved API key?",
          'This key pays for every call this agent makes. Once shown, any script on this page, any screenshot or recording, and anyone watching your screen can read it, and whoever holds it can spend against your account. Reveal it only when you are alone.',
          { hold: true, center: true, x: true, noCancel: true, onYes: revealKey });
      });
    }

    var providerSel = card.querySelector("#model-vendor");
    var modelSel = card.querySelector('select[data-field="model.name"]');
    var urlInput = card.querySelector('input[data-field="model.base_url"]');
    if (providerSel) providerSel.addEventListener("change", function () {
      var preset = PRESETS[providerSel.value] || PRESETS.Custom;
      if (urlInput) { urlInput.value = preset.url; urlInput.readOnly = false; }
      if (preset.url) testNote.textContent = "endpoint set — press Test to fetch its models";
    });
    if (testBtn) testBtn.addEventListener("click", async function () {
      testBtn.disabled = true;
      testNote.textContent = "testing…";
      try {
        var provider = (PRESETS[providerSel ? providerSel.value : "Custom"] || PRESETS.Custom).format;
        var res = await fetch("/providers/" + encodeURIComponent(provider) + "/models", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ base_url: urlInput ? urlInput.value : "" }),
        });
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
        await loadRegistry();
        var list = ((registry.providers || {})[provider] || {}).models || [];
        if (modelSel) {
          modelSel.replaceChildren();
          list.forEach(function (m) {
            var o = document.createElement("option");
            o.value = m; o.textContent = m;
            modelSel.appendChild(o);
          });
        }
        testNote.textContent = list.length + " models";
        if (data.window || data.contexts) testNote.textContent += " · window " + (data.window || "known");
      } catch (e) {
        testNote.textContent = "failed: " + (e.message || e);
      } finally {
        testBtn.disabled = false;
      }
    });
  }

  function gather() {
    var out = { persona: {}, model: {}, api_key: "" };
    var vendorSel = card.querySelector("#model-vendor");
    if (vendorSel) out.model.provider = (PRESETS[vendorSel.value] || PRESETS.Custom).format;
    if (out.key_scope) out.key_scope = String(out.key_scope).toLowerCase();   // the contract is global|local
    var y = out.persona.birth_year, mo = out.persona.birth_month, d = out.persona.birth_day;
    if (y && mo) out.persona.birth_date = String(y) + "-" + String(mo).padStart(2, "0") + "-" + String(d || 1).padStart(2, "0");
    delete out.persona.birth_year; delete out.persona.birth_month; delete out.persona.birth_day;
    Array.prototype.forEach.call(card.querySelectorAll(".lic-input"), function (input) {
      if (!input.dataset.field) return;
      // the mask is not a key: it stands in for one already on file and must never be written back
      if (input.id === "lic-key" && /^\*+$/.test(input.value)) return;
      var path = input.dataset.field.split(".");
      if (path.length === 2) out[path[0]][path[1]] = input.value;
      else if (input.value) out[path[0]] = input.value;
    });
    return out;
  }

  async function saveProfile(button, status) {
    if (!current) return;
    button.disabled = true;
    if (card._note) card._note.textContent = "saving…";
    try {
      var res = await fetch("/agents/" + encodeURIComponent(current.channel) + "/profile", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify(gather()),
      });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
      var hadKey = !!(data && (data.has_key || data.api_key_name));
      if (card._note) card._note.textContent = hadKey ? "saved · key on file" : "saved · no key stored";
      await select(current.channel);
    } catch (e) {
      if (card._note) card._note.textContent = "not saved: " + (e.message || e);
    } finally {
      button.disabled = false;
    }
  }

  function sorted() {
    var rest = agents.filter(function (a) { return a.channel !== "default"; });
    rest.sort(function (a, b) {
      return order * String(a.channel).localeCompare(String(b.channel), undefined, { sensitivity: "base" });
    });
    var pinned = agents.filter(function (a) { return a.channel === "default"; });
    return pinned.concat(rest);
  }

  // the bar is built once: rebuilding it on every selection would take the focus out of the search box mid-word
  function buildBar() {
    if (bar.dataset.built) return;
    bar.dataset.built = "1";
    // left column: the agents themselves, scrolling sideways when there are more than fit
    var left = el("div", "page-nav-left");
    left.appendChild(el("span", "page-nav-title", "Agents"));
    tabsBox = el("span", "page-tabs");
    left.appendChild(tabsBox);
    bar.appendChild(left);

    // right column: what you can do to them
    var right = el("div", "page-nav-right");
    bar.appendChild(right);
    var createBtn = el("button", "page-action", "Create");
    createBtn.type = "button";
    createBtn.id = "agent-create";
    createBtn.addEventListener("click", function () { askCreate(); });
    right.appendChild(createBtn);

    var deleteBtn = el("button", "page-action danger", "Delete");
    deleteBtn.type = "button";
    deleteBtn.id = "agent-delete";
    deleteBtn.addEventListener("click", function () { askDelete(); });
    right.appendChild(deleteBtn);

    search = document.createElement("input");
    search.type = "search";
    search.className = "page-search";
    search.id = "agent-search";
    search.placeholder = "search agents…";
    search.setAttribute("aria-label", "search agents");
    search.addEventListener("input", function () { query = search.value.trim().toLowerCase(); paintTabs(); });
    right.appendChild(search);
    sortButton = el("button", "page-sort", "A–Z");
    sortButton.type = "button";
    sortButton.setAttribute("aria-label", "sort agents");
    sortButton.addEventListener("click", function () {
      order = -order;
      sortButton.textContent = order === 1 ? "A–Z" : "Z–A";
      paintTabs();
    });
    right.appendChild(sortButton);
  }

  function paintBar() { buildBar(); paintTabs(); }

  function paintTabs() {
    tabsBox.replaceChildren();
    sorted()
      .filter(function (a) { return !query || String(a.channel).toLowerCase().indexOf(query) >= 0; })
      .forEach(function (a) {
        var tab = el("button", "page-tab" + (a.channel === selected ? " active" : ""), a.channel);
        tab.type = "button";
        tab.dataset.channel = a.channel;
        if (a.channel === "default") tab.classList.add("pinned");
        tab.addEventListener("click", function () { select(a.channel); });
        tabsBox.appendChild(tab);
      });
    if (!tabsBox.children.length) tabsBox.appendChild(el("span", "muted small", "no match"));
  }

  async function select(channel) {
    selected = channel;
    paintTabs();
    card.replaceChildren(el("p", "muted small", "loading…"));
    try {
      var p = await (await fetch("/agents/" + encodeURIComponent(channel) + "/profile",
                                 { headers: { accept: "application/json" } })).json();
      paint(p);
    } catch (e) {
      card.replaceChildren(el("p", "small", "could not read the profile: " + (e.message || e)));
    }
  }

  async function loadRegistry() {
    try {
      var data = await (await fetch("/providers", { headers: { accept: "application/json" } })).json();
      registry = { providers: data.providers || {} };
    } catch (e) { registry = { providers: {} }; }
  }

  async function start() {
    bar = document.getElementById("agent-bar");
    card = document.getElementById("agent-editor");
    if (!bar || !card) return;
    await loadRegistry();
    try {
      agents = ((await (await fetch("/agents", { headers: { accept: "application/json" } })).json()).agents) || [];
      paintBar();
      if (agents.length) {
        // open on the agent last chosen anywhere in the site, so the page keeps the colour you were using
        var remembered = null;
        try { remembered = localStorage.getItem("athena.lane"); } catch (e) { }
        var match = agents.filter(function (a) { return a.channel === remembered; })[0];
        select((match || agents[0]).channel);
      }
      else card.replaceChildren(el("p", "muted small", "no agents configured"));
    } catch (e) {
      card.replaceChildren(el("p", "small", "could not read the agent list: " + (e.message || e)));
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  // ---- confirmation -------------------------------------------------------------------------------------------
  // Creating is reversible by deleting; deleting takes a profile and its sessions with it, so the yes has to be
  // held rather than clicked. The hold is real time: releasing early cancels, because a stray click that destroys a
  // profile is not a mistake anyone can undo.
  var HOLD_MS = 15000;

  function dialog(title, message, opts) {
    opts = opts || {};
    var veil = el("div", "confirm-veil");
    var box = el("div", "confirm-box" + (opts.center ? " centered" : ""));
    if (opts.x) {
      var x = el("button", "confirm-x", "×");
      x.type = "button";
      x.setAttribute("aria-label", "close");
      x.addEventListener("click", function () { veil.remove(); opts.onNo && opts.onNo(); });
      box.appendChild(x);
    }
    box.appendChild(el("h2", "confirm-title", title));
    box.appendChild(el("p", "confirm-text", message));
    var input = null;
    if (opts.field) {
      input = document.createElement("input");
      input.type = "text";
      input.className = "confirm-input";
      input.placeholder = opts.field;
      input.setAttribute("aria-label", opts.field);
      box.appendChild(input);
    }
    var row = el("div", "confirm-actions");
    // the X (and Escape, and the backdrop) close the dialog; a labelled cancel is only added when asked for
    var no = null;
    if (!opts.noCancel) {
      no = el("button", "btn", opts.ok ? "Close" : "Cancel");
      no.type = "button";
    }
    var yes = null;
    if (!opts.ok) {
      yes = el("button", "btn btn-send" + (opts.hold ? " hold" : ""), opts.hold ? "Hold to confirm" : "Yes");
      yes.type = "button";
    }
    if (no) row.appendChild(no);
    if (yes) row.appendChild(yes);
    box.appendChild(row);
    veil.appendChild(box);
    document.body.appendChild(veil);
    if (input) input.focus();

    function close() { veil.remove(); }

    if (no) no.addEventListener("click", function () { close(); opts.onNo && opts.onNo(); });
    veil.addEventListener("click", function (e) { if (e.target === veil) close(); });
    document.addEventListener("keydown", function esc(e) {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
    });

    if (!yes) return { close: close };
    if (opts.hold) {
      // a timer rather than animation frames: a background or occluded tab stops painting, and the countdown must
      // still be measured by the clock rather than by how often the browser chooses to draw
      var started = null, ticker = null, done = false;
      function paint() {
        var p = Math.min(1, (performance.now() - started) / HOLD_MS);
        yes.style.setProperty("--hold", (p * 100) + "%");
        yes.textContent = p < 1 ? "Hold to confirm · " + Math.ceil((1 - p) * (HOLD_MS / 1000)) + "s" : "Confirmed";
        if (p >= 1 && !done) { done = true; cancel(); close(); opts.onYes(); }
      }
      function begin(e) {
        e.preventDefault();
        if (done) return;
        started = performance.now();
        yes.classList.add("holding");
        clearInterval(ticker);
        ticker = setInterval(paint, 100);
        paint();
      }
      function cancel() {
        clearInterval(ticker);
        ticker = null;
        yes.classList.remove("holding");
        if (!done) { yes.style.setProperty("--hold", "0%"); yes.textContent = "Hold to confirm"; }
      }
      yes.addEventListener("pointerdown", begin);
      yes.addEventListener("pointerup", cancel);
      yes.addEventListener("pointerleave", cancel);
      yes.addEventListener("pointercancel", cancel);
    } else {
      yes.addEventListener("click", function () {
        var name = input ? input.value.trim() : "";
        if (input && !name) { input.focus(); return; }
        close(); opts.onYes(name);
      });
    }
    return { close: close };
  }

  function notice(message) { dialog("Notice", message, { ok: true }); }

  function askCreate() {
    dialog("Create a new agent?",
      "A new profile is scaffolded with its own persona, model and workspace. Nothing is shared with the existing agents.",
      { field: "agent name", onYes: async function (name) {
        try {
          var res = await fetch("/agents", { method: "POST",
            headers: { "content-type": "application/json" }, body: JSON.stringify({ name: name }) });
          if (!res.ok) throw new Error((await res.json().catch(function () { return {}; })).detail || ("HTTP " + res.status));
          await refresh();
          select(name);        // a new agent is the one you want to look at
        } catch (e) {
          notice("not created: " + (e.message || e));
        }
      } });
  }

  function askDelete() {
    if (!selected) return;
    if (selected === "default") {
      notice("The default agent is built in. It cannot be deleted, because every install needs one profile that always exists.");
      return;
    }
    dialog("Delete " + selected + "?",
      "Its profile, its sessions and its workspace are removed. This cannot be undone.",
      { hold: true, onYes: async function () {
        try {
          var res = await fetch("/agents/" + encodeURIComponent(selected), { method: "DELETE" });
          if (!res.ok) throw new Error((await res.json().catch(function () { return {}; })).detail || ("HTTP " + res.status));
          await refresh();
          select("default");
        } catch (e) {
          notice("not deleted: " + (e.message || e));
        }
      } });
  }

  async function refresh() {
    agents = ((await (await fetch("/agents", { headers: { accept: "application/json" } })).json()).agents) || [];
    paintBar();
  }
})();
