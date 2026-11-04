// Componenti del CRM nuovo per una pagina senza React: Select e finestra di conferma.

const CHEVRON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
const SPUNTA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
let aperto = null;

// Il <select> nativo resta nella pagina, nascosto, e tiene il valore: chi legge .value non cambia.
export function menus(radice = document) {
  for (const sel of radice.querySelectorAll("select")) {
    if (!sel.classList.contains("ui-select-native")) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ui-select-trigger" + (sel.classList.contains("compatto") ? " compatto" : "");
      b.setAttribute("aria-haspopup", "listbox");
      b.setAttribute("aria-expanded", "false");
      if (sel.id) {
        b.id = sel.id + "-menu";
        document.querySelector(`label[for="${sel.id}"]`)?.setAttribute("for", b.id);
      }
      b.setAttribute("aria-label", sel.getAttribute("aria-label") || sel.closest("label")?.querySelector("span")?.textContent || "");
      b.innerHTML = `<span class="ui-select-value"></span><span class="ui-select-chevron">${CHEVRON}</span>`;
      b.onclick = () => (aperto?.sel === sel ? chiudi() : apri(sel, b));
      b.onkeydown = (e) => {
        if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
          e.preventDefault();
          apri(sel, b);
        }
      };
      sel.classList.add("ui-select-native");
      sel.after(b);
      sel.addEventListener("change", () => etichetta(sel));
    }
    etichetta(sel);
  }
}

function etichetta(sel) {
  const b = sel.nextElementSibling;
  if (!b?.classList.contains("ui-select-trigger")) return;
  b.querySelector(".ui-select-value").textContent = sel.selectedOptions[0]?.textContent ?? "";
  b.disabled = sel.disabled;
}

function apri(sel, b) {
  chiudi();
  const lista = document.createElement("div");
  lista.className = "ui-select-content";
  lista.setAttribute("role", "listbox");
  lista.tabIndex = -1;
  lista.innerHTML = [...sel.options].map((o, i) =>
    `<div class="ui-select-item" role="option" data-i="${i}" aria-selected="${o.selected}" ${o.selected ? 'data-current="true"' : ""}>` +
    `<span></span><span class="ui-select-check">${o.selected ? SPUNTA : ""}</span></div>`).join("");
  [...sel.options].forEach((o, i) => (lista.children[i].firstChild.textContent = o.textContent));
  document.body.append(lista);
  const r = b.getBoundingClientRect(), h = lista.offsetHeight;
  lista.style.minWidth = r.width + "px";
  lista.style.left = Math.min(r.left, innerWidth - lista.offsetWidth - 12) + "px";
  lista.style.top = (r.bottom + 6 + h > innerHeight && r.top - 6 - h > 0 ? r.top - 6 - h : r.bottom + 6) + "px";
  aperto = {sel, b, lista, i: sel.selectedIndex};
  b.dataset.state = "open";
  b.setAttribute("aria-expanded", "true");
  evidenzia(Math.max(0, sel.selectedIndex));
  lista.focus();
  lista.onpointermove = (e) => { const it = e.target.closest(".ui-select-item"); if (it) evidenzia(+it.dataset.i); };
  lista.onclick = (e) => { const it = e.target.closest(".ui-select-item"); if (it) scegli(+it.dataset.i); };
  lista.onkeydown = (e) => {
    const n = sel.options.length, k = e.key;
    if (k === "ArrowDown") evidenzia((aperto.i + 1) % n);
    else if (k === "ArrowUp") evidenzia((aperto.i - 1 + n) % n);
    else if (k === "Home") evidenzia(0);
    else if (k === "End") evidenzia(n - 1);
    else if (k === "Enter" || k === " ") scegli(aperto.i);
    else if (k === "Escape" || k === "Tab") { chiudi(); if (k === "Escape") b.focus(); }
    else if (k.length === 1) {
      const j = [...sel.options].findIndex((o) => o.textContent.trim().toLowerCase().startsWith(k.toLowerCase()));
      if (j >= 0) evidenzia(j);
    } else return;
    e.preventDefault();
  };
}

function evidenzia(i) {
  aperto.i = i;
  [...aperto.lista.children].forEach((it, j) => it.toggleAttribute("data-highlighted", j === i));
  aperto.lista.children[i]?.scrollIntoView({block: "nearest"});
}

function scegli(i) {
  const {sel, b} = aperto;
  chiudi();
  b.focus();
  if (sel.selectedIndex !== i) {
    sel.selectedIndex = i;
    sel.dispatchEvent(new Event("change", {bubbles: true}));
  }
}

function chiudi() {
  if (!aperto) return;
  aperto.lista.remove();
  aperto.b.dataset.state = "closed";
  aperto.b.setAttribute("aria-expanded", "false");
  aperto = null;
}

addEventListener("pointerdown", (e) => {
  if (aperto && !aperto.lista.contains(e.target) && !aperto.b.contains(e.target)) chiudi();
}, true);
addEventListener("resize", chiudi);
addEventListener("scroll", (e) => { if (aperto && !aperto.lista.contains(e.target)) chiudi(); }, true);

// Finestra di conferma al posto di confirm(): risolve true solo con il pulsante di conferma.
export function conferma(titolo, testo, azione = "Conferma", tono = "primary") {
  const d = document.createElement("dialog");
  d.className = "ui-dialog";
  d.innerHTML = `<h2></h2><p></p><form method="dialog" class="actions"><button class="btn soft md" value="no">Annulla</button><button class="btn ${tono} md" value="si"></button></form>`;
  d.querySelector("h2").textContent = titolo;
  d.querySelector("p").textContent = testo;
  d.querySelector("[value=si]").textContent = azione;
  document.body.append(d);
  d.showModal();
  return new Promise((ok) => d.addEventListener("close", () => { ok(d.returnValue === "si"); d.remove(); }));
}
