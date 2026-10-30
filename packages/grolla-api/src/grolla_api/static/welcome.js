// Boas-vindas: o Grolla se apresenta na primeira visita. A fala é digitada com a boca mexendo;
// o clique completa a fala e o seguinte avança. O "já vi" fica no navegador.
(() => {
  const KEY = "grolla.boas-vindas";
  const dialog = document.getElementById("boas-vindas");
  if (!dialog) return;

  const $ = (id) => document.getElementById(id);
  const grolla = $("welcome-grolla");
  const fala = $("welcome-fala");
  const bubble = $("welcome-bubble");
  const scene = $("welcome-scene");
  const next = $("welcome-proximo");
  const dots = $("welcome-dots");
  const steps = [...dialog.querySelectorAll("template[data-fala]")];
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let step = 0;
  let timer = null;
  let typing = false;
  let opener = null;

  const seen = () => {
    try { return localStorage.getItem(KEY) === "vista"; } catch { return false; }
  };
  const remember = () => {
    try { localStorage.setItem(KEY, "vista"); } catch { /* sem armazenamento: aparece de novo */ }
  };

  steps.forEach((_, i) => {
    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = "welcome-dot";
    dot.setAttribute("aria-label", `Etapa ${i + 1} de ${steps.length}`);
    dot.addEventListener("click", (event) => { event.stopPropagation(); show(i); });
    dots.appendChild(dot);
  });

  const textOf = (i) => steps[i].content.querySelector("[data-texto]").textContent.trim();

  // Vogal abre a boca, consoante abre pouco, espaço e pontuação fecham.
  const mouth = (ch) => {
    const open = /[aáâãeéêoóôõ]/i.test(ch) ? 1 : /[iíuú]/i.test(ch) ? 0.7 : /[\s.,:!?]/.test(ch) ? 0.3 : 0.5;
    grolla.style.setProperty("--abre", open);
  };

  const finish = () => {
    clearInterval(timer);
    typing = false;
    fala.textContent = textOf(step);
    bubble.classList.add("done");
    grolla.classList.remove("talking");
    scene.classList.add("show");
  };

  function show(i) {
    clearInterval(timer);
    step = i;
    const template = steps[i];
    [...dots.children].forEach((dot, j) => dot.setAttribute("aria-current", j === i ? "step" : "false"));
    next.textContent = i === steps.length - 1 ? "Começar" : "Próximo";
    scene.classList.remove("show");
    scene.replaceChildren(...[...template.content.children].filter((el) => !el.hasAttribute("data-texto")).map((el) => el.cloneNode(true)));
    grolla.classList.toggle("waving", template.hasAttribute("data-acena") && !reduce);
    bubble.classList.remove("done");
    if (reduce) { finish(); return; }
    const text = textOf(i);
    let n = 0;
    fala.textContent = "";
    typing = true;
    grolla.classList.add("talking");
    timer = setInterval(() => {
      n += 2; // duas letras por tique: ~2 s por fala
      fala.textContent = text.slice(0, n);
      mouth(text[n - 1] || " ");
      if (n >= text.length) finish();
    }, 32);
  }

  const close = () => {
    clearInterval(timer);
    remember();
    if (dialog.open) dialog.close();
  };
  const advance = () => {
    if (typing) finish();
    else if (step < steps.length - 1) show(step + 1);
    else close();
  };
  const open = () => {
    opener = document.activeElement;
    if (!dialog.open) dialog.showModal();
    show(0);
    next.focus();
  };

  $("welcome-stage").addEventListener("click", advance);
  next.addEventListener("click", advance);
  $("welcome-pular").addEventListener("click", close);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); }); // Esc
  dialog.addEventListener("close", () => { if (opener && opener.focus) opener.focus(); });
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" && step > 0) show(step - 1);
    else if (event.key === "ArrowRight") advance();
    // Enter num botão já vira clique; fora dele, avança.
    else if (event.key === "Enter" && event.target.tagName !== "BUTTON") advance();
  });

  const again = $("conhecer-grolla");
  if (again) again.addEventListener("click", open);

  // No war room de um incidente aberto, só pelo link: quem chega numa crise vê o incidente.
  if (document.body.dataset.welcome === "auto" && !seen()) open();
})();
