"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATUS = {
  clean: { label: "Чистая", short: "" },
  dirty: { label: "Грязная", short: "в стирку" },
  washing: { label: "В стирке", short: "стирается" },
};
const FORMALITY = [
  { k: null, t: "Любой" }, { k: 1, t: "Кэжуал" }, { k: 2, t: "Смарт" },
  { k: 3, t: "Офис" }, { k: 0, t: "Спорт" }, { k: 4, t: "Выход" },
];
const MAIN = ["top", "dress", "bottom", "shoes"];
const SIDE = ["outer", "layer", "bag", "accessory"];

const state = {
  tab: "looks",
  catalog: null,
  me: null,
  items: [],
  outfits: [],
  outfitsLoaded: false,
  weatherSummary: null,
  missing: [],
  lock: null,
  formality: null,
  catFilter: "all",
  worn: new Set(),
  liked: new Set(),
  disliked: new Set(),
};

// ------------------------------------------------------------------ platform
function haptic(kind = "light") {
  try {
    if (!tg || !tg.HapticFeedback) return;
    if (kind === "success" || kind === "error" || kind === "warning") tg.HapticFeedback.notificationOccurred(kind);
    else tg.HapticFeedback.impactOccurred(kind);
  } catch (_) { /* старые клиенты */ }
}

function applyTheme() {
  const dark = tg ? tg.colorScheme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
  try { tg && tg.setHeaderColor(bg); tg && tg.setBackgroundColor(bg); tg && tg.setBottomBarColor && tg.setBottomBarColor(bg); } catch (_) {}
}

async function api(path, { method = "GET", json, form } = {}) {
  const headers = { "X-Init-Data": tg ? tg.initData : "" };
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = form;
  const r = await fetch(path, { method, headers, body });
  let data = null;
  try { data = await r.json(); } catch (_) {}
  if (!r.ok) throw new Error((data && data.error) || `Ошибка ${r.status}`);
  return data;
}

let toastTimer;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2600);
}

// -------------------------------------------------------------------- helpers
const catOf = (key) => state.catalog.categories.find((c) => c.key === key);
const subsOf = (key) => (catOf(key) || { subs: [] }).subs;
const itemById = (id) => state.items.find((i) => i.id === id);

function swatches(item, n = 3) {
  return (item.colors || []).slice(0, n).map((c) => `<i style="background:${esc(c.hex)}" title="${esc(c.name)}"></i>`).join("");
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Доброй ночи";
  if (h < 12) return "Доброе утро";
  if (h < 18) return "Добрый день";
  return "Добрый вечер";
}

function updateBadge() {
  const n = state.items.filter((i) => i.status === "dirty").length;
  const b = $("#laundryBadge");
  b.hidden = n === 0;
  b.textContent = n;
}

// ------------------------------------------------------------------- data
async function loadItems() {
  const { items } = await api("/api/items");
  state.items = items;
  updateBadge();
}

async function loadOutfits({ reshuffle = false } = {}) {
  const p = new URLSearchParams({ count: "6" });
  if (state.lock) p.set("lock", state.lock);
  if (state.formality !== null) p.set("formality", state.formality);
  if (reshuffle) p.set("seed", String(Math.floor(Math.random() * 65535)));
  const data = await api("/api/outfits/suggest?" + p);
  state.outfits = data.outfits;
  state.missing = data.missing;
  state.weatherSummary = data.weather_summary;
  state.outfitsLoaded = true;
}

// ------------------------------------------------------------------ render
function render() {
  document.querySelectorAll(".tabbar [data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.tab));
  const screen = $("#screen");
  screen.style.animation = "none";
  void screen.offsetHeight;
  screen.style.animation = "";
  ({ looks: renderLooks, wardrobe: renderWardrobe, laundry: renderLaundry, profile: renderProfile })[state.tab]();
}

function flatlay(items) {
  const main = items.filter((i) => MAIN.includes(i.category));
  const side = items.filter((i) => SIDE.includes(i.category))
    .sort((a, b) => SIDE.indexOf(a.category) - SIDE.indexOf(b.category));
  const piece = (i) => `<div class="piece" data-c="${i.category}"><img src="${esc(i.image_url)}" alt="${esc(i.label)}" loading="lazy"></div>`;
  return `<div class="flatlay ${side.length ? "has-side" : ""}">
    <div class="col">${main.map(piece).join("")}</div>
    ${side.length ? `<div class="col side">${side.map(piece).join("")}</div>` : ""}
  </div>`;
}

function lookCard(o, idx) {
  const worn = state.worn.has(o.id);
  const liked = state.liked.has(o.id);
  return `<article class="look" data-id="${o.id}">
    <span class="num">№ ${idx + 1}</span>
    ${flatlay(o.items)}
    <div class="look-info">
      <div class="reasons">${o.reasons.slice(0, 3).map((r) => `<span class="reason">${esc(r)}</span>`).join("")}</div>
      <div class="look-actions">
        ${worn ? `<span class="worn-badge">✓ Сегодня в этом</span>`
               : `<button class="btn primary" data-act="wear">Надену сегодня</button>`}
        <button class="btn ghost ${liked ? "on" : ""}" data-act="like" aria-label="Нравится">${liked ? "♥" : "♡"}</button>
        <button class="btn ghost" data-act="dislike" aria-label="Не нравится">✕</button>
      </div>
    </div>
  </article>`;
}

function renderLooks() {
  const name = (state.me && state.me.user && state.me.user.first_name) || "";
  const date = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  const weather = state.weatherSummary
    ? `<div class="weather">${esc(state.weatherSummary)}</div>`
    : `<button class="weather muted" data-act="city">📍 Город</button>`;
  const lockItem = state.lock && itemById(state.lock);
  let body;
  if (!state.outfitsLoaded) {
    body = `<div class="carousel"><div class="skeleton"></div><div class="skeleton"></div></div>`;
  } else if (state.missing.length) {
    body = `<div class="empty"><div class="big">🧺</div><b>Соберём первый образ</b>
      Добавьте хотя бы ${esc(state.missing.join(" и "))} — и обувь, чтобы образы были законченными.
      <br><button class="btn primary" data-act="add">Добавить вещь</button></div>`;
  } else if (!state.outfits.length) {
    body = `<div class="empty"><div class="big">🫧</div><b>Всё в стирке</b>
      Из чистых вещей сейчас не собрать образ. Загляните во вкладку «Стирка».</div>`;
  } else {
    body = `<div class="carousel" id="carousel">${state.outfits.map(lookCard).join("")}</div>
      <div class="dots" id="dots">${state.outfits.map((_, i) => `<i class="${i ? "" : "on"}"></i>`).join("")}</div>
      <button class="btn ghost wide" data-act="more" style="margin-top:8px">↻ Другие варианты</button>`;
  }
  $("#screen").innerHTML = `
    <header class="hello">
      <div><div class="eyebrow">${esc(date)}</div><h1>${esc(greeting())}${name ? ",<br>" + esc(name) : ""}</h1></div>
      ${weather}
    </header>
    ${lockItem ? `<div class="locked"><img src="${esc(lockItem.thumb_url)}"><div class="t"><b>Образы вокруг вещи</b>${esc(lockItem.label)}</div>
      <button class="btn ghost small" data-act="unlock">Сбросить</button></div>` : ""}
    <div class="chips" id="formality">${FORMALITY.map((f) =>
      `<button class="chip ${state.formality === f.k ? "on" : ""}" data-f="${f.k}">${f.t}</button>`).join("")}</div>
    ${body}`;

  const car = $("#carousel");
  if (car) {
    car.addEventListener("scroll", () => {
      const w = car.firstElementChild.getBoundingClientRect().width + 12;
      const idx = Math.round(car.scrollLeft / w);
      document.querySelectorAll("#dots i").forEach((d, i) => d.classList.toggle("on", i === idx));
    }, { passive: true });
  }
}

function renderWardrobe() {
  const counts = {};
  state.items.forEach((i) => (counts[i.category] = (counts[i.category] || 0) + 1));
  const cats = state.catalog.categories.filter((c) => counts[c.key]);
  const filtered = state.items.filter((i) =>
    state.catFilter === "all" ? true : state.catFilter === "fav" ? i.favorite : i.category === state.catFilter);
  const n = state.items.length;
  $("#screen").innerHTML = `
    <header class="hello"><div><div class="eyebrow">${n} ${plural(n, "вещь", "вещи", "вещей")}</div><h1>Гардероб</h1></div></header>
    ${n ? `<div class="chips" id="cats">
      <button class="chip ${state.catFilter === "all" ? "on" : ""}" data-c="all">Все<span class="n">${n}</span></button>
      ${state.items.some((i) => i.favorite) ? `<button class="chip ${state.catFilter === "fav" ? "on" : ""}" data-c="fav">♥ Любимое</button>` : ""}
      ${cats.map((c) => `<button class="chip ${state.catFilter === c.key ? "on" : ""}" data-c="${c.key}">${c.emoji} ${esc(c.name)}<span class="n">${counts[c.key]}</span></button>`).join("")}
    </div>
    <div class="grid">${filtered.map((i) => `
      <button class="card ${i.status !== "clean" ? "dim" : ""}" data-item="${i.id}">
        ${i.favorite ? `<span class="fav">♥</span>` : ""}
        ${i.status !== "clean" ? `<span class="st ${i.status}">${STATUS[i.status].short}</span>` : ""}
        <div class="ph"><img src="${esc(i.thumb_url)}" loading="lazy" alt=""></div>
        <div class="meta"><b>${esc(i.label)}</b><div class="sw">${swatches(i)}</div></div>
      </button>`).join("")}</div>`
    : `<div class="empty"><div class="big">📸</div><b>Здесь будет ваш гардероб</b>
       Сфотографируйте вещь на ровном фоне — фон уберётся автоматически.
       <br><button class="btn primary" data-act="add">Добавить первую вещь</button></div>`}`;
}

function renderLaundry() {
  const dirty = state.items.filter((i) => i.status === "dirty");
  const washing = state.items.filter((i) => i.status === "washing");
  const clean = state.items.filter((i) => i.status === "clean").length;
  const strip = (arr) => `<div class="strip">${arr.map((i) =>
    `<button data-item="${i.id}"><img src="${esc(i.thumb_url)}" alt=""></button>`).join("")}</div>`;
  $("#screen").innerHTML = `
    <header class="hello"><div><div class="eyebrow">уход за вещами</div><h1>Стирка</h1></div></header>
    <div class="stats">
      <div class="stat"><b>${clean}</b><span>чистых</span></div>
      <div class="stat"><b style="color:var(--dirty)">${dirty.length}</b><span>в корзине</span></div>
      <div class="stat"><b style="color:var(--wash)">${washing.length}</b><span>стирается</span></div>
    </div>
    ${dirty.length ? `<section class="lane">
      <div class="lane-h"><b>Корзина</b><span>${dirty.length} ${plural(dirty.length, "вещь", "вещи", "вещей")}</span></div>
      ${strip(dirty)}
      <div class="actions" style="margin-top:0">
        <button class="btn primary" data-act="wash-start">🫧 Стирать</button>
        <button class="btn ghost" data-act="wash-all">Уже чистое</button>
      </div></section>` : ""}
    ${washing.length ? `<section class="lane">
      <div class="lane-h"><b>В машинке</b><span>${washing.length} ${plural(washing.length, "вещь", "вещи", "вещей")}</span></div>
      ${strip(washing)}
      <button class="btn primary wide" data-act="wash-finish">Постирано ✓</button></section>` : ""}
    ${!dirty.length && !washing.length ? `<div class="empty"><div class="big">✨</div><b>Всё чистое</b>
      Отмечайте «Надену сегодня» в образах — я сам посчитаю, когда вещи пора стирать.
      Количество носок до стирки можно настроить в карточке вещи.</div>` : ""}`;
}

function renderProfile() {
  const u = (state.me && state.me.user) || {};
  const hour = u.notify_hour;
  $("#screen").innerHTML = `
    <header class="hello"><div><div class="eyebrow">настройки</div><h1>Профиль</h1></div></header>
    <div class="rows">
      <button class="row" data-act="city"><span>🏙</span><span class="k">Город</span><span class="v">${esc(u.city || "не задан")}</span></button>
      <button class="row" data-act="morning"><span>⏰</span><span class="k">Образ каждое утро</span>
        <span class="v">${hour === null || hour === undefined ? "выкл" : String(hour).padStart(2, "0") + ":00"}</span></button>
    </div>
    <h2>Недавно носили</h2>
    <div id="history"><div class="hist">${"<div class='h'><div class='mini'></div></div>".repeat(3)}</div></div>`;
  api("/api/outfits/history").then(({ history }) => {
    const el = $("#history");
    if (!el) return;
    if (!history.length) {
      el.innerHTML = `<div class="empty" style="padding:24px">Здесь появятся образы, которые вы надели.</div>`;
      return;
    }
    el.innerHTML = `<div class="hist">${history.map((h) => `<div class="h">
      <div class="mini">${h.items.slice(0, 4).map((i) => `<img src="${esc(i.thumb_url)}" alt="">`).join("")}</div>
      <div class="d">${h.worn_on ? new Date(h.worn_on).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "♥ нравится"}</div>
    </div>`).join("")}</div>`;
  }).catch(() => {});
}

// ------------------------------------------------------------------- sheet
let sheetOnClose = null;
function openSheet(html, onClose) {
  $("#sheetBody").innerHTML = html;
  $("#sheet").hidden = false;
  $("#backdrop").hidden = false;
  sheetOnClose = onClose || null;
  if (tg) tg.BackButton.show();
}
function closeSheet() {
  if ($("#sheet").hidden) return;
  $("#sheet").hidden = true;
  $("#backdrop").hidden = true;
  if (tg) tg.BackButton.hide();
  const cb = sheetOnClose;
  sheetOnClose = null;
  if (cb) cb();
}

function categoryPicker(cat, sub) {
  return `<div class="label">Категория</div>
    <div class="chips" data-role="cat">${state.catalog.categories.map((c) =>
      `<button class="chip ${c.key === cat ? "on" : ""}" data-cat="${c.key}">${c.emoji} ${esc(c.name)}</button>`).join("")}</div>
    <div data-role="subs">${cat ? subPicker(cat, sub) : ""}</div>`;
}
function subPicker(cat, sub) {
  return `<div class="label">Тип</div><div class="chips" style="flex-wrap:wrap">${subsOf(cat).map((s) =>
    `<button class="chip ${s.key === sub ? "on" : ""}" data-sub="${s.key}">${esc(s.name)}</button>`).join("")}</div>`;
}

function itemSheet(item) {
  const cat = catOf(item.category);
  const draw = () => {
    const it = itemById(item.id) || item;
    openSheet(`
      <div class="hero"><img src="${esc(it.image_url)}" alt=""></div>
      <h3>${esc(it.label)}</h3>
      <p class="sub">${cat ? cat.emoji + " " + esc(cat.name) : ""} · надевали ${it.times_worn} ${plural(it.times_worn, "раз", "раза", "раз")}${it.last_worn ? ", последний — " + new Date(it.last_worn).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) : ""}</p>
      <div class="colors">${(it.colors || []).map((c) => `<span class="color"><i style="background:${esc(c.hex)}"></i>${esc(c.name)}</span>`).join("")}</div>
      <div class="label">Состояние</div>
      <div class="seg" data-role="status">${["clean", "dirty", "washing"].map((s) =>
        `<button class="${it.status === s ? "on" : ""}" data-status="${s}">${STATUS[s].label}</button>`).join("")}</div>
      <div class="label">Стирка</div>
      <div class="stepper"><span class="t">${it.wash_after ? `В стирку после <b>${it.wash_after}</b> ${plural(it.wash_after, "носки", "носок", "носок")}` : "Не отслеживать"}
        <br><small style="color:var(--ink-2)">сейчас: ${it.wears_since_wash}</small></span>
        <button data-wash="-1">−</button><button data-wash="1">+</button></div>
      <div class="toggle-row"><span>С принтом / узором</span><button class="switch ${it.pattern ? "on" : ""}" data-toggle="pattern"></button></div>
      <div class="toggle-row"><span>Любимая вещь</span><button class="switch ${it.favorite ? "on" : ""}" data-toggle="favorite"></button></div>
      <details style="margin-top:8px"><summary class="label" style="cursor:pointer">Изменить категорию</summary>${categoryPicker(it.category, it.sub)}</details>
      <div class="actions">
        <button class="btn primary" data-act="style-with">✦ Образ с этой вещью</button>
        <button class="btn ghost danger" data-act="delete">Удалить</button>
      </div>`);
    const body = $("#sheetBody");
    let pickedCat = it.category;
    body.onclick = async (e) => {
      const t = e.target.closest("button");
      if (!t) return;
      const patch = async (fields, msg) => {
        try {
          const { item: upd } = await api(`/api/items/${it.id}`, { method: "PATCH", json: fields });
          Object.assign(itemById(it.id), upd);
          haptic("light");
          if (msg) toast(msg);
          updateBadge();
          state.outfitsLoaded = false;
          render();
          draw();
        } catch (err) { toast(err.message); }
      };
      if (t.dataset.status) return patch({ status: t.dataset.status });
      if (t.dataset.wash) return patch({ wash_after: Math.max(0, it.wash_after + Number(t.dataset.wash)) });
      if (t.dataset.toggle) return patch({ [t.dataset.toggle]: !it[t.dataset.toggle] });
      if (t.dataset.cat) {
        pickedCat = t.dataset.cat;
        body.querySelectorAll("[data-cat]").forEach((b) => b.classList.toggle("on", b === t));
        body.querySelector("[data-role=subs]").innerHTML = subPicker(pickedCat, null);
        return;
      }
      if (t.dataset.sub) return patch({ category: pickedCat, sub: t.dataset.sub }, "Категория обновлена");
      if (t.dataset.act === "delete") {
        const ok = await confirmDialog("Удалить вещь из гардероба?");
        if (!ok) return;
        try {
          await api(`/api/items/${it.id}`, { method: "DELETE" });
          state.items = state.items.filter((x) => x.id !== it.id);
          state.outfitsLoaded = false;
          haptic("warning");
          closeSheet();
          updateBadge();
          render();
        } catch (err) { toast(err.message); }
      }
      if (t.dataset.act === "style-with") {
        state.lock = it.id;
        state.tab = "looks";
        state.outfitsLoaded = false;
        closeSheet();
        render();
        refreshLooks();
      }
    };
  };
  draw();
}

function confirmDialog(text) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm) {
      try { return tg.showConfirm(text, resolve); } catch (_) {}
    }
    resolve(window.confirm(text));
  });
}

// ------------------------------------------------------------------ upload
async function uploadQueue(files) {
  for (let i = 0; i < files.length; i++) {
    const done = await uploadOne(files[i], i + 1, files.length);
    if (done === "cancel") break;
  }
  state.outfitsLoaded = false;
  if (state.tab === "looks") refreshLooks();
}

function uploadOne(file, n, total) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    let cat = null, sub = null, busy = false, finished = false;
    const finish = (v) => { if (!finished) { finished = true; URL.revokeObjectURL(url); resolve(v); } };

    const draw = () => {
      openSheet(`
        <div class="hero small" id="upHero"><img class="orig" src="${url}" alt=""></div>
        <h3>Что это за вещь?</h3>
        <p class="sub">${total > 1 ? `Фото ${n} из ${total} · ` : ""}фон уберу автоматически</p>
        ${categoryPicker(cat, sub)}
        <div class="actions"><button class="btn primary" data-act="save" ${sub ? "" : "disabled"}>Добавить в гардероб</button></div>`,
        () => finish(busy ? "ok" : "cancel"));
      $("#sheetBody").onclick = async (e) => {
        const t = e.target.closest("button");
        if (!t || busy) return;
        if (t.dataset.cat) { cat = t.dataset.cat; sub = null; haptic("light"); return redraw(); }
        if (t.dataset.sub) { sub = t.dataset.sub; haptic("light"); return redraw(); }
        if (t.dataset.act === "save") await save();
      };
    };
    const redraw = () => {
      const body = $("#sheetBody");
      const scroll = body.scrollTop;
      body.querySelectorAll("[data-cat]").forEach((b) => b.classList.toggle("on", b.dataset.cat === cat));
      body.querySelector("[data-role=subs]").innerHTML = cat ? subPicker(cat, sub) : "";
      body.querySelector("[data-act=save]").disabled = !sub;
      body.scrollTop = scroll;
    };
    const save = async () => {
      busy = true;
      const hero = $("#upHero");
      hero.insertAdjacentHTML("beforeend", `<div class="processing"><span>✂️ Вырезаю фон…</span></div>`);
      $("#sheetBody [data-act=save]").disabled = true;
      const form = new FormData();
      form.append("file", await shrink(file));
      form.append("category", cat);
      form.append("sub", sub);
      try {
        const { item } = await api("/api/items", { method: "POST", form });
        state.items.unshift(item);
        haptic("success");
        hero.innerHTML = `<img src="${esc(item.image_url)}" alt="">`;
        hero.classList.remove("small");
        $("#sheetBody .sub").textContent = (item.colors || []).map((c) => c.name).slice(0, 3).join(" · ") || "Готово";
        $("#sheetBody h3").textContent = "Готово ✨";
        if (state.tab === "wardrobe") renderWardrobe();
        setTimeout(() => { closeSheet(); }, n < total ? 700 : 1300);
      } catch (err) {
        busy = false;
        haptic("error");
        hero.querySelector(".processing")?.remove();
        $("#sheetBody [data-act=save]").disabled = false;
        toast(err.message);
      }
    };
    draw();
  });
}

// Уменьшаем огромные фото с камеры до 2048px — быстрее загрузка, качество модели то же.
function shrink(file) {
  return new Promise((resolve) => {
    if (!/^image\/(jpeg|png|webp)$/.test(file.type) || file.size < 1.5e6) return resolve(file);
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, 2048 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k);
      c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(img.src);
      c.toBlob((b) => resolve(b ? new File([b], "photo.jpg", { type: "image/jpeg" }) : file), "image/jpeg", 0.92);
    };
    img.onerror = () => resolve(file);
    img.src = URL.createObjectURL(file);
  });
}

// --------------------------------------------------------------- settings
function citySheet() {
  openSheet(`<h3>Ваш город</h3><p class="sub">Подберу образы по погоде на сегодня</p>
    <input class="input" id="cityInput" placeholder="Например, Москва" value="${esc((state.me.user || {}).city || "")}" autocomplete="off">
    <div class="actions"><button class="btn primary" data-act="save-city">Сохранить</button></div>`);
  const input = $("#cityInput");
  setTimeout(() => input.focus(), 250);
  const save = async () => {
    const city = input.value.trim();
    if (!city) return;
    try {
      const { user } = await api("/api/settings", { method: "POST", json: { city } });
      state.me.user = user;
      haptic("success");
      closeSheet();
      toast("Город: " + user.city);
      state.outfitsLoaded = false;
      if (state.tab === "looks") refreshLooks(); else render();
    } catch (err) { toast(err.message); haptic("error"); }
  };
  $("#sheetBody").onclick = (e) => { if (e.target.closest("[data-act=save-city]")) save(); };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
}

function morningSheet() {
  const cur = state.me.user.notify_hour;
  const hours = [6, 7, 8, 9, 10, 11];
  openSheet(`<h3>Образ каждое утро</h3><p class="sub">Пришлю в чат образ дня с учётом погоды</p>
    <div class="hours">${hours.map((h) => `<button class="${cur === h ? "on" : ""}" data-h="${h}">${String(h).padStart(2, "0")}:00</button>`).join("")}</div>
    <div class="actions"><button class="btn ghost wide" data-h="off">Не присылать</button></div>`);
  $("#sheetBody").onclick = async (e) => {
    const t = e.target.closest("[data-h]");
    if (!t) return;
    const h = t.dataset.h === "off" ? null : Number(t.dataset.h);
    try {
      const { user } = await api("/api/settings", { method: "POST", json: { notify_hour: h, tz_offset: -new Date().getTimezoneOffset() } });
      state.me.user = user;
      haptic("success");
      closeSheet();
      render();
      toast(h === null ? "Рассылка выключена" : `Жду вас в ${String(h).padStart(2, "0")}:00 ☀️`);
    } catch (err) { toast(err.message); }
  };
}

// ----------------------------------------------------------------- actions
async function refreshLooks(opts) {
  try {
    await loadOutfits(opts);
  } catch (err) {
    toast(err.message);
    state.outfitsLoaded = true;
    state.outfits = [];
  }
  if (state.tab === "looks") render();
}

async function onLookAction(card, act) {
  const id = Number(card.dataset.id);
  const outfit = state.outfits.find((o) => o.id === id);
  if (!outfit) return;
  try {
    if (act === "wear") {
      const res = await api("/api/outfits/wear", { method: "POST", json: { outfit_id: id, item_ids: outfit.items.map((i) => i.id) } });
      state.worn.add(id);
      haptic("success");
      await loadItems();
      toast(res.to_wash.length
        ? `Отличный выбор! После сегодня в стирку: ${res.to_wash.map((i) => i.label.toLowerCase()).join(", ")}`
        : "Отличный выбор! Записал ✨");
      card.outerHTML = lookCard(outfit, state.outfits.indexOf(outfit));
    } else if (act === "like") {
      if (state.liked.has(id)) return;
      await api("/api/outfits/feedback", { method: "POST", json: { outfit_id: id, verdict: "like" } });
      state.liked.add(id);
      haptic("light");
      card.querySelector("[data-act=like]").classList.add("on");
      card.querySelector("[data-act=like]").textContent = "♥";
    } else if (act === "dislike") {
      await api("/api/outfits/feedback", { method: "POST", json: { outfit_id: id, verdict: "dislike" } });
      haptic("warning");
      state.outfits = state.outfits.filter((o) => o.id !== id);
      card.style.transition = "transform .25s, opacity .25s";
      card.style.transform = "translateY(-20px) scale(.96)";
      card.style.opacity = "0";
      toast("Понял, такое больше не предложу");
      setTimeout(() => { if (state.tab === "looks") render(); }, 250);
    }
  } catch (err) { toast(err.message); }
}

async function laundry(action, msg) {
  try {
    await api("/api/laundry", { method: "POST", json: { action } });
    await loadItems();
    haptic("success");
    toast(msg);
    state.outfitsLoaded = false;
    render();
  } catch (err) { toast(err.message); }
}

// ------------------------------------------------------------------- events
function bind() {
  $("#tabbar").addEventListener("click", (e) => {
    const t = e.target.closest("[data-tab]");
    if (!t) return;
    state.tab = t.dataset.tab;
    haptic("light");
    window.scrollTo(0, 0);
    render();
    if (state.tab === "looks" && !state.outfitsLoaded) refreshLooks();
  });
  $("#addBtn").addEventListener("click", () => { haptic("medium"); $("#fileInput").click(); });
  $("#fileInput").addEventListener("change", (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (files.length) uploadQueue(files);
  });
  $("#backdrop").addEventListener("click", closeSheet);
  if (tg) tg.BackButton.onClick(closeSheet);

  // Свайп вниз по шторке — закрыть
  let startY = null;
  const sheet = $("#sheet");
  sheet.addEventListener("touchstart", (e) => { if ($("#sheetBody").scrollTop <= 0) startY = e.touches[0].clientY; }, { passive: true });
  sheet.addEventListener("touchmove", (e) => {
    if (startY === null) return;
    const dy = e.touches[0].clientY - startY;
    if (dy > 0) sheet.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  sheet.addEventListener("touchend", (e) => {
    if (startY === null) return;
    const dy = e.changedTouches[0].clientY - startY;
    sheet.style.transform = "";
    startY = null;
    if (dy > 110) closeSheet();
  });

  $("#screen").addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    const card = t.closest(".look");
    if (card && t.dataset.act) return onLookAction(card, t.dataset.act);
    if (t.dataset.item) return itemSheet(itemById(Number(t.dataset.item)));
    if (t.dataset.f !== undefined) {
      state.formality = t.dataset.f === "null" ? null : Number(t.dataset.f);
      haptic("light");
      state.outfitsLoaded = false;
      render();
      return refreshLooks();
    }
    if (t.dataset.c) { state.catFilter = t.dataset.c; haptic("light"); return renderWardrobe(); }
    switch (t.dataset.act) {
      case "add": return $("#fileInput").click();
      case "more": haptic("light"); state.outfitsLoaded = false; render(); return refreshLooks({ reshuffle: true });
      case "unlock": state.lock = null; state.outfitsLoaded = false; render(); return refreshLooks();
      case "city": return citySheet();
      case "morning": return morningSheet();
      case "wash-start": return laundry("start", "Запустили стирку 🫧");
      case "wash-finish": return laundry("finish", "Всё чистое ✨");
      case "wash-all": return laundry("all_clean", "Отметил как чистое ✨");
    }
  });
}

// --------------------------------------------------------------------- init
async function init() {
  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.disableVerticalSwipes && tg.disableVerticalSwipes(); } catch (_) {}
    tg.onEvent("themeChanged", applyTheme);
  }
  applyTheme();
  bind();
  $("#screen").innerHTML = `<div class="carousel" style="margin-top:80px"><div class="skeleton"></div></div>`;
  try {
    const [catalog, me] = await Promise.all([api("/api/catalog"), api("/api/me")]);
    state.catalog = catalog;
    state.me = me;
    state.weatherSummary = me.weather_summary;
    await loadItems();
    const start = new URLSearchParams(location.search).get("tab");
    if (start && ["looks", "wardrobe", "laundry", "profile"].includes(start)) state.tab = start;
    render();
    if (state.tab === "looks") refreshLooks();
  } catch (err) {
    $("#screen").innerHTML = `<div class="empty"><div class="big">🔒</div><b>Не получилось открыть гардероб</b>
      ${esc(err.message)}<br>Откройте приложение из чата с ботом.</div>`;
  }
}

init();
