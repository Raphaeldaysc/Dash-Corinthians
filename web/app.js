const DATA_URL = "data/dashboard.json";
const RESULT_LABEL = { W: "V", D: "E", L: "D" };
const RESULT_NAME = { W: "Vitórias", D: "Empates", L: "Derrotas" };
const VENUE_LABEL = { H: "Casa", A: "Fora", N: "Neutro" };

const state = {
  data: null,
  season: null,
  comp: "all",
  venue: "all",
  seasonsMode: "all",
  scorerMode: "scorers",
  analysisTab: "coaches",
};
const charts = new Map();
let countdownTimer = null;

/* ------------------------------------------------------------------ helpers */
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const num = (v, d = 0) =>
  isNum(v) ? v.toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }) : "—";
const pct = (v, d = 1) => (isNum(v) ? `${num(v * 100, d)}%` : "—");
const signed = (v, d = 0) => (isNum(v) ? `${v > 0 ? "+" : ""}${num(v, d)}` : "—");
const fmtDate = (iso, opts = { day: "2-digit", month: "short" }) =>
  iso ? new Date(iso).toLocaleDateString("pt-BR", opts).replace(".", "") : "—";
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const POSITION_PT = { G: "GOL", D: "DEF", M: "MEI", F: "ATA" };
const img = (src, cls = "") => (src ? `<img src="${esc(src)}" alt="" loading="lazy" class="${cls}" />` : "");
const money = (value, currency = "BRL") =>
  isNum(value) ? value.toLocaleString("pt-BR", { style: "currency", currency, maximumFractionDigits: 0 }) : "—";

function palette() {
  return {
    ink: css("--ink"),
    muted: css("--muted"),
    line: css("--line"),
    card: css("--card"),
    bg2: css("--bg-2"),
    accent: css("--accent"),
    gold: css("--gold"),
    W: css("--win"),
    D: css("--draw"),
    L: css("--loss"),
  };
}

function baseOption() {
  const p = palette();
  return {
    animationDuration: 900,
    animationEasing: "cubicOut",
    textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: p.muted },
    tooltip: {
      backgroundColor: p.card,
      borderColor: p.line,
      textStyle: { color: p.ink, fontSize: 12 },
      extraCssText: "border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.15);",
    },
  };
}

function axisStyle() {
  const p = palette();
  return {
    axisLine: { lineStyle: { color: p.line } },
    axisTick: { show: false },
    axisLabel: { color: p.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: p.line, type: "dashed" } },
  };
}

function chart(id) {
  const el = $(id);
  if (!el || !window.echarts) return null;
  let instance = charts.get(id);
  // Painéis dinâmicos recriam o elemento com o mesmo id ao trocar de aba.
  // Uma instância ligada ao elemento antigo não pode ser reutilizada.
  if (instance && (instance.isDisposed?.() || instance.getDom() !== el)) {
    if (!instance.isDisposed?.()) instance.dispose();
    charts.delete(id);
    instance = null;
  }
  if (!instance) {
    instance = window.echarts.init(el, null, { renderer: "canvas" });
    charts.set(id, instance);
  }
  return instance;
}

function setChart(id, option) {
  const instance = chart(id);
  if (instance) instance.setOption({ ...baseOption(), ...option }, true);
}

function animateNumber(el, to, { decimals = 0, sign = false } = {}) {
  if (!el) return;
  if (!isNum(to)) {
    el.textContent = "—";
    return;
  }
  const from = Number(el.dataset.v ?? 0);
  el.dataset.v = String(to);
  const start = performance.now();
  const duration = 900;
  const step = (now) => {
    const k = Math.min(1, (now - start) / duration);
    const value = from + (to - from) * (1 - Math.pow(1 - k, 3));
    el.textContent = `${sign && value > 0.0001 ? "+" : ""}${num(value, decimals)}`;
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

const resultDot = (g, small = false) =>
  `<span class="res res--${esc(g.result)}${small ? " res--sm" : ""}" title="${esc(
    `${fmtDate(g.date)} · ${g.comp} · ${VENUE_LABEL[g.venue] ?? ""} · ${g.opponent} ${g.score}`
  )}">${RESULT_LABEL[g.result] ?? "?"}</span>`;

const empty = (msg) => `<div class="empty">${esc(msg)}</div>`;

/* ------------------------------------------------------------------ data access */
const key = () => `${state.season}|${state.comp}|${state.venue}`;
const currentView = () => state.data.views[key()] ?? null;
const leagueView = () => state.data.views[`${state.data.meta.current_season}|brasileirao|all`] ?? null;
const compsForSeason = () => state.data.meta.comps_by_season[String(state.season)] ?? [];
const compMeta = (k) => state.data.meta.competitions.find((c) => c.key === k);

/* ------------------------------------------------------------------ filtros */
function renderSegment(containerId, items, active, onPick) {
  const el = $(containerId);
  el.innerHTML = items
    .map(
      (it) =>
        `<button type="button" role="radio" aria-checked="${it.value === active}" data-value="${esc(it.value)}" class="${
          it.value === active ? "is-active" : ""
        }">${it.swatch ? `<span class="sw" style="background:${esc(it.swatch)}"></span>` : ""}${esc(it.label)}</button>`
    )
    .join("");
  el.onclick = (ev) => {
    const btn = ev.target.closest("button[data-value]");
    if (btn) onPick(btn.dataset.value);
  };
}

function bindStaticSegment(containerId, onPick) {
  const el = $(containerId);
  el.onclick = (ev) => {
    const btn = ev.target.closest("button[data-value]");
    if (!btn) return;
    el.querySelectorAll("button").forEach((b) => {
      const active = b === btn;
      b.classList.toggle("is-active", active);
      if (b.getAttribute("role") === "tab") b.setAttribute("aria-selected", String(active));
      if (b.getAttribute("role") === "radio") b.setAttribute("aria-checked", String(active));
    });
    onPick(btn.dataset.value);
  };
}

function renderFilters() {
  const seasons = [...state.data.meta.seasons].sort((a, b) => b - a);
  renderSegment(
    "filter-season",
    seasons.map((s) => ({ value: String(s), label: String(s) })),
    String(state.season),
    (v) => {
      state.season = Number(v);
      if (state.comp !== "all" && !compsForSeason().includes(state.comp)) state.comp = "all";
      renderFilters();
      renderFiltered();
    }
  );
  const comps = compsForSeason().map((k) => compMeta(k)).filter(Boolean);
  renderSegment(
    "filter-comp",
    [{ value: "all", label: "Todas" }, ...comps.map((c) => ({ value: c.key, label: c.name, swatch: c.color }))],
    state.comp,
    (v) => {
      state.comp = v;
      renderFilters();
      renderFiltered();
    }
  );
}

/* ------------------------------------------------------------------ hero */
function renderHero(view) {
  const { meta } = state.data;
  const comp = compMeta(state.comp);
  $("brand-season").textContent = String(state.season);
  $("hero-eyebrow").textContent = `${comp ? comp.name : "Todas as competições"} · ${state.season}`;
  const series = view?.series ?? [];
  $("meta-period").textContent = series.length
    ? `${fmtDate(series[0].date)} — ${fmtDate(series[series.length - 1].date)}`
    : "—";
  $("meta-games").textContent = String(view?.kpis.games ?? 0);
  $("meta-comps").textContent = String((meta.comps_by_season[String(state.season)] ?? []).length);
}

function renderNextMatch() {
  const box = $("next-match");
  const next = state.data.upcoming?.[0];
  if (countdownTimer) clearInterval(countdownTimer);
  if (!next) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const home = next.venue !== "A";
  const cor = `<img src="${esc(state.data.meta.team_logo)}" alt="" />`;
  const opp = img(next.logo);
  box.innerHTML = `
    <div>
      <div class="next-match__label">Próximo jogo · ${esc(next.comp_name)}${next.stage ? ` · ${esc(next.stage)}` : ""}</div>
      <div class="next-match__teams">${home ? cor : opp}<span>${esc(home ? "Corinthians" : next.opponent)}</span>
        <span style="opacity:.5">x</span><span>${esc(home ? next.opponent : "Corinthians")}</span>${home ? opp : cor}</div>
    </div>
    <div class="next-match__info">${esc(
      new Date(next.date).toLocaleString("pt-BR", { weekday: "long", day: "2-digit", month: "long", hour: "2-digit", minute: "2-digit" })
    )}${next.stadium ? `<br />${esc(next.stadium)}` : ""}${
      isNum(next.opponent_rank) ? `<br />Adversário é o ${next.opponent_rank}º no Brasileirão` : ""
    }</div>
    <div class="countdown" id="countdown"></div>`;
  const tick = () => {
    const diff = Math.max(0, new Date(next.date).getTime() - Date.now());
    const parts = [
      [Math.floor(diff / 86_400_000), "dias"],
      [Math.floor(diff / 3_600_000) % 24, "horas"],
      [Math.floor(diff / 60_000) % 60, "min"],
      [Math.floor(diff / 1000) % 60, "seg"],
    ];
    $("countdown").innerHTML = parts
      .map(([v, l]) => `<div><strong>${String(v).padStart(2, "0")}</strong><span>${l}</span></div>`)
      .join("");
  };
  tick();
  countdownTimer = setInterval(tick, 1000);
}

/* ------------------------------------------------------------------ KPIs */
function renderKpis(view) {
  const k = view?.kpis;
  const target = state.data.meta.target_rate;
  if (!k || !k.games) {
    ["kpi-rate", "kpi-ppg", "kpi-gd", "kpi-w", "kpi-d", "kpi-l", "kpi-gf", "kpi-ga", "kpi-cs"].forEach((id) => {
      $(id).textContent = "—";
      $(id).dataset.v = "0";
    });
    $("kpi-rate-bar").style.width = "0";
    $("kpi-ppg-bar").style.width = "0";
    $("kpi-rate-foot").textContent = "Sem jogos para este filtro.";
    return;
  }
  animateNumber($("kpi-rate"), k.rate * 100, { decimals: 1 });
  $("kpi-rate-bar").style.width = `${k.rate * 100}%`;
  $("kpi-rate-target").style.left = `${target * 100}%`;
  const above = k.rate >= target;
  $("kpi-rate-pill").textContent = above ? "ACIMA DA META" : "ABAIXO DA META";
  $("kpi-rate-pill").classList.toggle("is-good", above);
  $("kpi-rate-foot").innerHTML = `<strong>${k.pts}</strong> de ${k.max_pts} pontos possíveis · meta ${pct(target, 0)}`;

  animateNumber($("kpi-w"), k.w);
  animateNumber($("kpi-d"), k.d);
  animateNumber($("kpi-l"), k.l);
  $("kpi-camp-pill").textContent = `${k.w}-${k.d}-${k.l}`;
  $("kpi-camp-foot").innerHTML = k.biggest_win
    ? `${k.games} jogos · maior vitória <strong>${esc(k.biggest_win.score)}</strong> x ${esc(k.biggest_win.opponent)}`
    : `${k.games} jogos disputados`;

  animateNumber($("kpi-ppg"), k.ppg, { decimals: 2 });
  $("kpi-ppg-bar").style.width = `${(k.ppg / 3) * 100}%`;
  const onPace = k.ppg >= target * 3;
  $("kpi-ppg-pill").textContent = onPace ? "RITMO DE META" : `META ${num(target * 3, 2)}`;
  $("kpi-ppg-pill").classList.toggle("is-good", onPace);
  $("kpi-ppg-foot").innerHTML =
    state.comp === "brasileirao"
      ? `Ritmo de <strong>${Math.round(k.ppg * 38)} pts</strong> em 38 rodadas`
      : `<strong>${num(k.avg_gf, 2)}</strong> gols marcados e <strong>${num(k.avg_ga, 2)}</strong> sofridos por jogo`;

  animateNumber($("kpi-gd"), k.gd, { sign: true });
  $("kpi-gd-pill").textContent = k.gd >= 0 ? "POSITIVO" : "NEGATIVO";
  $("kpi-gd-pill").classList.toggle("is-good", k.gd >= 0);
  animateNumber($("kpi-gf"), k.gf);
  animateNumber($("kpi-ga"), k.ga);
  animateNumber($("kpi-cs"), k.clean_sheets);
}

/* ------------------------------------------------------------------ cenários */
function renderScenarios() {
  const sim = state.data.simulation;
  const cards = $("scen-cards");
  if (!sim?.available) {
    cards.innerHTML = empty(sim?.reason ?? "Simulação indisponível");
    setChart("chart-position", {});
    renderStandingsFallback();
    return;
  }
  const p = sim.probs;
  const c = sim.cutoffs;
  const safety = c[`pos${sim.z4_from - 1}`];
  $("scen-title").textContent = `As ${sim.team.remaining} rodadas finais`;
  $("scen-aside").textContent = `${num(sim.n_sims)} simulações Monte Carlo dos ${sim.remaining_matches} jogos restantes, com forças de ataque e defesa via Poisson.`;
  $("scen-exp-pos").textContent = `posição esperada: ${num(sim.expected_position, 1)}º`;
  const pts = sim.points;
  const span = Math.max(1, pts.max - pts.min);
  const left = ((pts.p10 - pts.min) / span) * 100;
  const width = ((pts.p90 - pts.p10) / span) * 100;
  const mark = ((pts.p50 - pts.min) / span) * 100;
  cards.innerHTML = `
    <article class="card scen" style="--c:var(--gold)">
      <div class="scen__label"><span>Chance de G4</span><span class="pill">TOP 4</span></div>
      <div class="scen__value">${pct(p.g4)}</div>
      <div class="scen__sub"><div>Título<strong>${pct(p.title)}</strong></div><div>Corte G4<strong>~${num(c.pos4)} pts</strong></div></div>
      <p class="scen__foot">Mediana de pontos do 4º colocado nas simulações.</p>
    </article>
    <article class="card scen" style="--c:var(--ink)">
      <div class="scen__label"><span>Chance de G6</span><span class="pill pill--mono">LIBERTADORES</span></div>
      <div class="scen__value">${pct(p.g6)}</div>
      <div class="scen__sub"><div>Sul-Americana<strong>${pct(p.sula)}</strong></div><div>Corte G6<strong>~${num(c.pos6)} pts</strong></div></div>
      <p class="scen__foot">Vagas podem mudar conforme campeões de copa.</p>
    </article>
    <article class="card scen" style="--c:var(--accent)">
      <div class="scen__label"><span>Risco de rebaixamento</span><span class="pill">Z4</span></div>
      <div class="scen__value">${pct(p.z4)}</div>
      <div class="scen__sub"><div>Linha de segurança<strong>~${num(safety)} pts</strong></div><div>Posição atual<strong>${sim.team.rank}º</strong></div></div>
      <p class="scen__foot">Hoje com <strong>${num(sim.team.pts)} pts</strong> em ${num(sim.team.gp)} jogos.</p>
    </article>
    <article class="card scen" style="--c:var(--accent)">
      <div class="scen__label"><span>Faixa mais provável</span><span class="pill">80%</span></div>
      <div class="scen__value">${num(pts.p10)}–${num(pts.p90)}<small class="muted" style="font-size:1rem"> pts</small></div>
      <div class="range"><div class="range__fill" style="left:${left}%;width:${width}%"></div><span class="range__mark" style="left:${mark}%">${num(pts.p50)}</span></div>
      <div class="range__ends"><span>${num(pts.min)}</span><span>${num(pts.max)}</span></div>
      <p class="scen__foot">Em 80% das simulações o Timão termina nessa faixa. Mediana: <strong>${num(pts.p50)} pts</strong>.</p>
    </article>`;

  const pal = palette();
  const n = sim.position_dist.length;
  setChart("chart-position", {
    grid: { left: 36, right: 12, top: 16, bottom: 28 },
    tooltip: { ...baseOption().tooltip, trigger: "axis", formatter: (items) => `${items[0].name}º lugar: <b>${pct(items[0].value)}</b>` },
    xAxis: { type: "category", data: sim.position_dist.map((_, i) => String(i + 1)), ...axisStyle(), splitLine: { show: false } },
    yAxis: { type: "value", ...axisStyle(), axisLabel: { ...axisStyle().axisLabel, formatter: (v) => `${Math.round(v * 100)}%` } },
    series: [
      {
        type: "bar",
        barWidth: "62%",
        data: sim.position_dist.map((v, i) => ({
          value: v,
          itemStyle: {
            borderRadius: [5, 5, 0, 0],
            color: i < 4 ? pal.gold : i < 6 ? pal.ink : i >= n - 4 ? pal.accent : pal.muted,
          },
        })),
      },
    ],
  });

  $("proj-table").innerHTML = `
    <thead><tr><th>#</th><th>Time</th><th class="num">Pts</th><th class="num">Proj.</th><th>G6</th><th class="num">Z4</th></tr></thead>
    <tbody>${sim.table
      .map(
        (r, i) => `<tr class="${r.team_id === "874" || r.team === "Corinthians" ? "is-team" : ""}">
          <td>${i + 1}</td>
          <td><span class="team-cell">${img(r.logo)}${esc(r.short || r.team)}</span></td>
          <td class="num">${num(r.pts)}</td>
          <td class="num"><b>${num(r.exp_pts, 1)}</b></td>
          <td><span class="prob-bar" style="width:${Math.max(2, r.p_g6 * 60)}px"></span>${pct(r.p_g6, 0)}</td>
          <td class="num">${pct(r.p_z4, 0)}</td></tr>`
      )
      .join("")}</tbody>`;
}

function renderStandingsFallback() {
  const rows = state.data.standings?.rows ?? [];
  $("proj-table").innerHTML = rows.length
    ? `<thead><tr><th>#</th><th>Time</th><th class="num">J</th><th class="num">Pts</th><th class="num">SG</th></tr></thead>
       <tbody>${rows
         .map(
           (r) => `<tr class="${r.is_team ? "is-team" : ""}"><td>${num(r.rank)}</td>
             <td><span class="team-cell">${img(r.logo)}${esc(r.team)}</span></td>
             <td class="num">${num(r.gp)}</td><td class="num"><b>${num(r.pts)}</b></td><td class="num">${signed(r.gd)}</td></tr>`
         )
         .join("")}</tbody>`
    : `<tbody><tr><td>${esc("Tabela indisponível")}</td></tr></tbody>`;
}

/* ------------------------------------------------------------------ evolução */
function renderEvolution(view) {
  const series = view?.series ?? [];
  const target = state.data.meta.target_rate;
  if (!series.length) {
    setChart("chart-evolution", { title: { text: "Sem jogos", left: "center", top: "middle", textStyle: { color: palette().muted } } });
    ["evo-pts", "evo-target", "evo-diff", "evo-form"].forEach((id) => ($(id).textContent = "—"));
    $("evo-caption").textContent = "";
    return;
  }
  const last = series[series.length - 1];
  const diff = last.pts_cum - last.target_cum;
  $("evo-pts").textContent = `${last.pts_cum} pts`;
  $("evo-target").textContent = `${num(last.target_cum, 0)} pts`;
  $("evo-diff").textContent = `${signed(Math.round(diff))} pts`;
  $("evo-diff").className = diff >= 0 ? "pos" : "neg";
  $("evo-form").innerHTML = (view.streaks.form ?? []).slice(-5).map((r) => RESULT_LABEL[r]).join(" ");
  $("evo-caption").innerHTML = `Após ${last.n} jogos, o Corinthians está <strong>${Math.abs(Math.round(diff))} pontos ${
    diff >= 0 ? "acima" : "abaixo"
  }</strong> do ritmo de ${pct(target, 0)} de aproveitamento.`;

  const p = palette();
  setChart("chart-evolution", {
    grid: { left: 40, right: 20, top: 24, bottom: series.length > 30 ? 56 : 30 },
    tooltip: {
      ...baseOption().tooltip,
      trigger: "axis",
      formatter: (items) => {
        const g = series[items[0].dataIndex];
        return `<b>Jogo ${g.n}</b> · ${esc(fmtDate(g.date))}<br/>${esc(g.comp)} · ${esc(VENUE_LABEL[g.venue])} · ${esc(g.opponent)}<br/>
          <b>${esc(g.score)}</b> (${RESULT_LABEL[g.result]})<br/>Pontos: <b>${g.pts_cum}</b> · Meta: ${num(g.target_cum, 0)}<br/>Aproveitamento: ${pct(g.rate_cum)}`;
      },
    },
    xAxis: { type: "category", boundaryGap: false, data: series.map((g) => String(g.n)), ...axisStyle(), splitLine: { show: false } },
    yAxis: { type: "value", ...axisStyle() },
    dataZoom: series.length > 30 ? [{ type: "slider", height: 18, bottom: 8, borderColor: p.line }, { type: "inside" }] : [],
    series: [
      {
        name: "Pontos",
        type: "line",
        data: series.map((g) => g.pts_cum),
        smooth: 0.25,
        symbol: "circle",
        symbolSize: 6,
        lineStyle: { width: 3, color: p.accent },
        itemStyle: { color: p.accent },
        areaStyle: {
          color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(228,0,43,.28)" },
            { offset: 1, color: "rgba(228,0,43,0)" },
          ]),
        },
        markPoint: {
          symbol: "roundRect",
          symbolSize: [56, 24],
          data: [{ coord: [String(last.n), last.pts_cum], value: `${last.pts_cum} pts` }],
          label: { color: "#fff", fontWeight: 700, fontSize: 11 },
          itemStyle: { color: p.accent },
        },
      },
      {
        name: "Meta",
        type: "line",
        data: series.map((g) => g.target_cum),
        symbol: "none",
        lineStyle: { width: 2, type: "dashed", color: p.muted },
      },
    ],
  });
}

/* ------------------------------------------------------------------ resultados */
function renderResults(view) {
  const dist = view?.distribution ?? { W: 0, D: 0, L: 0 };
  const total = dist.W + dist.D + dist.L;
  const p = palette();
  const rate = view?.kpis.rate;
  setChart("chart-donut", {
    tooltip: { ...baseOption().tooltip, trigger: "item", formatter: (it) => `${esc(it.name)}: <b>${it.value}</b> (${num(it.percent, 1)}%)` },
    title: {
      text: pct(rate),
      subtext: "aproveitamento",
      left: "center",
      top: "38%",
      textStyle: { color: p.ink, fontSize: 24, fontWeight: 800, fontFamily: "Oswald, Inter" },
      subtextStyle: { color: p.muted, fontSize: 11 },
    },
    series: [
      {
        type: "pie",
        radius: ["64%", "84%"],
        avoidLabelOverlap: false,
        label: { show: false },
        itemStyle: { borderColor: p.card, borderWidth: 3, borderRadius: 6 },
        data: ["W", "D", "L"].map((r) => ({ name: RESULT_NAME[r], value: dist[r], itemStyle: { color: p[r] } })),
      },
    ],
  });
  $("donut-legend").innerHTML = ["W", "D", "L"]
    .map(
      (r) =>
        `<li><i style="background:${p[r]}"></i><span>${RESULT_NAME[r]}</span><em>${total ? pct(dist[r] / total, 0) : "—"}</em><b>${dist[r]}</b></li>`
    )
    .join("");
  $("res-winrate").textContent = total ? pct(dist.W / total) : "—";
  $("res-unbeaten").textContent = total ? pct((dist.W + dist.D) / total) : "—";
  $("last5").innerHTML = (view?.last5 ?? []).map((g) => resultDot(g)).join("");
}

/* ------------------------------------------------------------------ mando */
function renderHomeAway() {
  const ha = state.data.home_away[`${state.season}|${state.comp}`];
  if (!ha) {
    $("ha-cards").innerHTML = empty("Sem dados");
    $("ha-bars").innerHTML = "";
    $("ha-callout").textContent = "";
    return;
  }
  const card = (k, label, sub, cls, icon) => `
    <div class="ha">
      <div class="ha__head"><span class="ha__icon ${cls}">${icon}</span>
        <div class="ha__title">${label}<span>${sub}</span></div><span class="ha__rate">${pct(k.rate)}</span></div>
      <div class="ha__stats">
        <div>Pontos<strong>${k.pts}</strong></div><div>Por jogo<strong>${num(k.ppg, 2)}</strong></div><div>Saldo<strong>${signed(k.gd)}</strong></div>
      </div>
      <div class="ha__wdl"><span>${k.w} vitórias</span><span>${k.d} empates</span><span>${k.l} derrotas</span></div>
    </div>`;
  $("ha-cards").innerHTML =
    card(ha.home, "Em casa", "Como mandante", "", "C") + card(ha.away, "Como visitante", "Fora de casa", "ha__icon--away", "F");

  const bar = (label, a, b, fmt = (v) => num(v)) => {
    const total = (a ?? 0) + (b ?? 0);
    const share = total ? (a / total) * 100 : 50;
    return `<div><div class="cmp__label"><span>${label}</span><span>casa x fora</span></div>
      <div class="cmp__bar"><span style="width:${share}%"></span><span style="width:${100 - share}%"></span></div>
      <div class="cmp__vals"><b>${fmt(a)}</b><b>${fmt(b)}</b></div></div>`;
  };
  $("ha-bars").innerHTML =
    bar("Pontos conquistados", ha.home.pts, ha.away.pts) +
    bar("Gols marcados", ha.home.gf, ha.away.gf) +
    bar("Gols sofridos", ha.home.ga, ha.away.ga);

  const gap = ha.gap_pp;
  $("ha-callout").innerHTML = isNum(gap)
    ? `<strong>Leitura do mando:</strong> o Corinthians rende ${gap >= 0 ? "mais" : "menos"} em casa, com <strong>${num(
        Math.abs(gap),
        1
      )} p.p.</strong> de diferença no aproveitamento.${ha.neutral_games ? ` (${ha.neutral_games} jogo(s) em campo neutro fora da conta)` : ""}`
    : "Jogos insuficientes para comparar.";
}

/* ------------------------------------------------------------------ chegada */
function renderArrival() {
  const sim = state.data.simulation;
  const lv = leagueView();
  const current = state.data.meta.current_season;
  $("arr-eyebrow").textContent = `Projeção · Brasileirão ${current}`;
  if (!lv || !lv.kpis.games) {
    $("arr-text").textContent = "Sem jogos do Brasileirão na temporada atual.";
    $("arr-need").textContent = "";
    $("arr-next").innerHTML = "";
    $("arr-badge").textContent = "—";
    return;
  }
  const k = lv.kpis;
  const remaining = sim?.available ? sim.team.remaining : Math.max(0, 38 - k.games);
  const totalGames = k.games + remaining;
  const projected = Math.round(k.pts + k.ppg * remaining);
  $("arr-badge").textContent = `${projected} pts`;
  $("arr-text").innerHTML = `Mantendo a média atual de <strong>${num(k.ppg, 2)} pontos por jogo</strong>, a projeção é terminar as ${totalGames} rodadas com aproximadamente <strong>${projected} pontos</strong>. Restam <strong>${remaining}</strong> partidas.`;

  const slider = $("arr-slider");
  const min = k.pts;
  const max = k.pts + remaining * 3;
  slider.min = String(min);
  slider.max = String(max);
  if (!slider.dataset.touched) {
    const suggestion = sim?.available ? Math.round(sim.cutoffs.pos6) : Math.round(totalGames * 3 * 0.55);
    slider.value = String(Math.min(max, Math.max(min, suggestion)));
  }
  const update = () => {
    const target = Number(slider.value);
    $("arr-out").textContent = String(target);
    const need = target - k.pts;
    let text;
    if (need <= 0) text = `Meta de <strong>${target} pontos</strong> já alcançada.`;
    else if (need > remaining * 3) text = `<strong>${target} pontos</strong> já não é matematicamente possível.`;
    else {
      const minWins = Math.max(0, Math.ceil((need - remaining) / 2));
      const onlyWins = Math.ceil(need / 3);
      const probability = sim?.available ? sim.points.hist.filter((h) => h.pts >= target).reduce((acc, h) => acc + h.p, 0) : null;
      text = `Faltam <strong>${need} pontos</strong>. Caminho mínimo: <strong>${minWins} vitórias</strong> e empates no resto dos ${remaining} jogos (ou ${onlyWins} vitórias). Aproveitamento necessário: <strong>${pct(
        need / (remaining * 3),
        0
      )}</strong>.${isNum(probability) ? ` Chance estimada: <strong>${pct(probability, 0)}</strong>.` : ""}`;
    }
    $("arr-need").innerHTML = text;
  };
  slider.oninput = () => {
    slider.dataset.touched = "1";
    update();
  };
  update();

  const fixtures = sim?.available
    ? sim.fixtures
    : (state.data.upcoming ?? []).filter((u) => u.comp_key === "brasileirao").map((u) => ({ ...u, win: null }));
  $("arr-next").innerHTML = fixtures.length
    ? fixtures
        .slice(0, 8)
        .map(
          (f) => `<div class="chip" title="${esc(
            isNum(f.win) ? `Vitória ${pct(f.win, 0)} · Empate ${pct(f.draw, 0)} · Derrota ${pct(f.loss, 0)}` : f.opponent
          )}">${img(f.logo)}<div>${esc(f.opponent)} <small>${VENUE_LABEL[f.venue]}${
            isNum(f.opponent_rank) ? ` · ${f.opponent_rank}º` : ""
          } · ${esc(fmtDate(f.date))}</small>${
            isNum(f.win)
              ? `<div class="chip__prob"><span style="width:${f.win * 100}%"></span><span style="width:${f.draw * 100}%"></span><span style="width:${
                  f.loss * 100
                }%"></span></div>`
              : ""
          }</div></div>`
        )
        .join("")
    : `<span class="muted small">Sem jogos futuros cadastrados.</span>`;
}

/* ------------------------------------------------------------------ jogo a jogo */
function renderGames(view) {
  const games = view?.games ?? [];
  const series = view?.series ?? [];
  const p = palette();
  setChart("chart-games", {
    grid: { left: 36, right: 40, top: 20, bottom: games.length > 30 ? 56 : 30 },
    tooltip: {
      ...baseOption().tooltip,
      trigger: "axis",
      formatter: (items) => {
        const g = games[items[0].dataIndex];
        if (!g) return "";
        const extra = isNum(g.possession) ? `<br/>Posse ${num(g.possession, 0)}% · Finalizações ${num(g.shots)} (${num(g.shots_on_target)} no gol)` : "";
        return `<b>${esc(fmtDate(g.date))}</b> · ${esc(g.comp)} ${g.stage ? `· ${esc(g.stage)}` : ""}<br/>${esc(
          VENUE_LABEL[g.venue]
        )} x ${esc(g.opponent)}: <b>${esc(g.score)}</b>${extra}`;
      },
    },
    xAxis: {
      type: "category",
      data: games.map((g) => fmtDate(g.date)),
      ...axisStyle(),
      splitLine: { show: false },
      axisLabel: { ...axisStyle().axisLabel, interval: "auto", rotate: games.length > 20 ? 45 : 0 },
    },
    yAxis: [
      { type: "value", name: "saldo", nameTextStyle: { color: p.muted, fontSize: 10 }, ...axisStyle(), minInterval: 1 },
      { type: "value", name: "pts (5j)", min: 0, max: 3, nameTextStyle: { color: p.muted, fontSize: 10 }, ...axisStyle(), splitLine: { show: false } },
    ],
    dataZoom: games.length > 30 ? [{ type: "slider", height: 18, bottom: 8, borderColor: p.line }, { type: "inside" }] : [],
    series: [
      {
        type: "bar",
        barMaxWidth: 18,
        data: games.map((g) => ({
          value: g.result === "D" ? 0.15 : g.gf - g.ga,
          itemStyle: { color: p[g.result], borderRadius: g.gf >= g.ga ? [4, 4, 0, 0] : [0, 0, 4, 4] },
        })),
      },
      {
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        symbol: "none",
        data: series.map((s) => s.rolling5),
        lineStyle: { color: p.accent, width: 2 },
      },
    ],
  });

  $("games-table").innerHTML = games.length
    ? `<thead><tr><th>Data</th><th>Comp.</th><th>Mando</th><th>Adversário</th><th>Placar</th><th class="num">Posse</th><th class="num">Finaliz.</th><th>Fase</th><th>Estádio</th></tr></thead>
      <tbody>${[...games]
        .reverse()
        .map(
          (g) => `<tr><td>${esc(fmtDate(g.date, { day: "2-digit", month: "2-digit", year: "2-digit" }))}</td>
            <td><span class="comp-tag" style="background:${esc(g.comp_color)}">${esc(g.comp)}</span></td>
            <td>${esc(VENUE_LABEL[g.venue])}</td>
            <td><span class="team-cell">${img(g.logo)}${esc(g.opponent)}</span></td>
            <td><span class="team-cell">${resultDot(g, true)} <b>${esc(g.score)}</b></span></td>
            <td class="num">${isNum(g.possession) ? `${num(g.possession, 0)}%` : "—"}</td>
            <td class="num">${isNum(g.shots) ? `${num(g.shots)} (${num(g.shots_on_target)})` : "—"}</td>
            <td>${esc(g.stage || "")}</td><td class="muted">${esc(g.stadium || "")}</td></tr>`
        )
        .join("")}</tbody>`
    : `<tbody><tr><td>Sem jogos para este filtro.</td></tr></tbody>`;
}

/* ------------------------------------------------------------------ competições */
function renderCompetitions() {
  const cards = state.data.competitions[String(state.season)] ?? [];
  const box = $("comp-cards");
  if (!cards.length) {
    box.innerHTML = empty("Sem competições nesta temporada");
    return;
  }
  box.innerHTML = cards
    .map((c) => {
      const k = c.kpis;
      const stage = c.kind === "league" ? (isNum(c.position) ? `${c.position}º lugar` : `${k.games} rodadas`) : c.stage || "—";
      const path = (c.path ?? [])
        .map(
          (g) =>
            `<li><span>${esc(g.stage)}</span><span>${img(g.logo)}${esc(g.opponent)} <span class="muted">(${esc(
              VENUE_LABEL[g.venue]
            )})</span></span><span>${resultDot(g, true)} ${esc(g.score)}</span></li>`
        )
        .join("");
      return `<article class="card comp" style="--c:${esc(c.color)};${state.comp === c.key ? "outline:2px solid " + esc(c.color) + ";" : ""}" data-comp="${esc(c.key)}" role="button" tabindex="0" title="Filtrar por ${esc(c.name)}">
        <div class="comp__head"><div><p class="eyebrow">${esc(c.short)}</p><h3>${esc(c.name)}</h3></div>
          <span class="comp__status ${c.status === "Em disputa" ? "is-live" : ""}">${esc(c.status)}</span></div>
        <div class="comp__stage">${esc(stage)}</div>
        ${c.note ? `<p class="muted small" style="margin:0">${esc(c.note)}</p>` : ""}
        <div class="comp__rate"><strong>${pct(k.rate, 0)}</strong><span class="muted small">${k.w}V ${k.d}E ${k.l}D · ${k.gf}:${k.ga}</span></div>
        <div class="comp__form">${(c.form ?? []).map((r) => `<span class="res res--sm res--${esc(r)}">${RESULT_LABEL[r]}</span>`).join("")}</div>
        ${path ? `<ul class="path">${path}</ul>` : ""}
      </article>`;
    })
    .join("");
  box.onclick = (ev) => {
    const el = ev.target.closest("[data-comp]");
    if (!el) return;
    state.comp = state.comp === el.dataset.comp ? "all" : el.dataset.comp;
    renderFilters();
    renderFiltered();
    document.getElementById("kpis").scrollIntoView({ behavior: "smooth", block: "center" });
  };
}

/* ------------------------------------------------------------------ temporadas */
function renderSeasons() {
  const sc = state.data.seasons_compare;
  const p = palette();
  const mode = state.seasonsMode;
  const seasons = Object.keys(sc[mode] ?? {}).sort();
  const colors = [p.muted, p.gold, p.accent, p.ink, "#2f6fde"];
  const maxLen = Math.max(0, ...seasons.map((s) => sc[mode][s].length));
  setChart("chart-seasons", {
    grid: { left: 44, right: 20, top: 36, bottom: 30 },
    legend: { top: 0, textStyle: { color: p.muted }, icon: "roundRect" },
    tooltip: {
      ...baseOption().tooltip,
      trigger: "axis",
      valueFormatter: (v) => (mode === "all" ? pct(v) : `${v} pts`),
    },
    xAxis: { type: "category", data: Array.from({ length: maxLen }, (_, i) => String(i + 1)), name: "jogo", ...axisStyle(), splitLine: { show: false } },
    yAxis: {
      type: "value",
      ...axisStyle(),
      ...(mode === "all" ? { min: 0, max: 1, axisLabel: { ...axisStyle().axisLabel, formatter: (v) => `${Math.round(v * 100)}%` } } : {}),
    },
    series: seasons.map((s, i) => {
      const isCurrent = Number(s) === state.season;
      const color = isCurrent ? p.accent : colors[i % colors.length] === p.accent ? p.ink : colors[i % colors.length];
      return {
        name: s,
        type: "line",
        smooth: 0.2,
        symbol: "none",
        data: sc[mode][s],
        lineStyle: { width: isCurrent ? 3.5 : 2, color, opacity: isCurrent ? 1 : 0.75 },
        itemStyle: { color },
        emphasis: { focus: "series" },
      };
    }),
  });

  const comps = state.data.meta.competitions;
  $("seasons-table").innerHTML = `<thead><tr><th>Ano</th><th class="num">J</th><th>V-E-D</th><th class="num">Aprov.</th><th class="num">SG</th></tr></thead>
    <tbody>${[...sc.seasons]
      .reverse()
      .map(
        (s) => `<tr class="${s.season === state.season ? "is-team" : ""}"><td><b>${s.season}</b><div>${Object.entries(s.by_comp)
          .map(([ck, rate]) => {
            const c = comps.find((x) => x.key === ck);
            return c ? `<span class="comp-tag" style="background:${esc(c.color)};margin:2px 2px 0 0" title="${esc(c.name)}">${esc(c.short)} ${pct(rate, 0)}</span>` : "";
          })
          .join("")}</div></td>
          <td class="num">${s.games}</td><td>${s.w}-${s.d}-${s.l}</td><td class="num"><b>${pct(s.rate)}</b></td><td class="num">${signed(s.gd)}</td></tr>`
      )
      .join("")}</tbody>`;
}

/* ------------------------------------------------------------------ clássicos */
function renderClassicos() {
  const list = state.data.classicos ?? [];
  $("classic-cards").innerHTML = list.length
    ? list
        .map((c) => {
          const k = c.kpis;
          const t = Math.max(1, k.games);
          return `<article class="card">
            <div class="classic__head">${img(c.logo)}<div><div class="classic__nick">${esc(c.nickname)}</div><h3>x ${esc(c.rival)}</h3></div>
              <span class="ha__rate" style="margin-left:auto">${pct(k.rate, 0)}</span></div>
            <div class="classic__bar"><span style="width:${(k.w / t) * 100}%"></span><span style="width:${(k.d / t) * 100}%"></span><span style="width:${(k.l / t) * 100}%"></span></div>
            <div class="classic__nums"><span>${k.w} vitórias</span><span>${k.d} empates</span><span>${k.l} derrotas</span></div>
            <div class="classic__nums" style="margin-top:4px"><span>${k.games} jogos</span><span>Gols ${k.gf}:${k.ga}</span></div>
            <ul class="classic__list">${c.games
              .map(
                (g) =>
                  `<li>${resultDot(g, true)}<span>${esc(fmtDate(g.date, { day: "2-digit", month: "2-digit", year: "2-digit" }))} · ${esc(
                    g.comp
                  )} · ${esc(VENUE_LABEL[g.venue])}</span><b>${esc(g.score)}</b></li>`
              )
              .join("")}</ul>
          </article>`;
        })
        .join("")
    : empty("Nenhum clássico nas temporadas analisadas");
}

/* ------------------------------------------------------------------ gols */
function renderGoals(view) {
  const mins = view?.minutes;
  const p = palette();
  if (!mins?.available) {
    setChart("chart-minutes", { title: { text: "Sem eventos de gol para este filtro", left: "center", top: "middle", textStyle: { color: p.muted, fontSize: 13 } } });
  } else {
    setChart("chart-minutes", {
      grid: { left: 36, right: 16, top: 34, bottom: 28 },
      legend: { top: 0, textStyle: { color: p.muted }, icon: "roundRect" },
      tooltip: { ...baseOption().tooltip, trigger: "axis" },
      xAxis: { type: "category", data: mins.bins, ...axisStyle(), splitLine: { show: false } },
      yAxis: { type: "value", minInterval: 1, ...axisStyle() },
      series: [
        { name: "Marcados", type: "bar", data: mins.scored, barGap: "15%", itemStyle: { color: p.ink, borderRadius: [5, 5, 0, 0] } },
        { name: "Sofridos", type: "bar", data: mins.conceded, itemStyle: { color: p.accent, borderRadius: [5, 5, 0, 0] } },
      ],
    });
  }

  const fg = view?.first_goal;
  const block = (label, d) =>
    d && d.games
      ? `<div class="fg"><span>${label}</span><strong>${pct(d.rate, 0)}</strong><em>${d.w}V ${d.d}E ${d.l}D em ${d.games} jogos</em></div>`
      : `<div class="fg"><span>${label}</span><strong>—</strong><em>sem dados</em></div>`;
  $("first-goal").innerHTML =
    block("Saindo na frente", fg?.scored_first) +
    block("Sofrendo o 1º gol", fg?.conceded_first) +
    `<div class="fg"><span>Terminou 0 a 0</span><strong>${fg?.goalless ?? 0}</strong><em>jogos sem gols</em></div>`;

  renderScorers(view);
}

function renderScorers(view) {
  const box = $("scorers");
  const mode = state.scorerMode;
  if (mode === "squad") {
    const squad = state.data.squads?.[`${state.season}|${state.comp}`] ?? [];
    if (!squad.length) {
      box.innerHTML = `<li>${empty("Sem dados de elenco para este filtro.")}</li>`;
      return;
    }
    const initials = (name) =>
      String(name ?? "")
        .split(" ")
        .filter(Boolean)
        .slice(0, 2)
        .map((w) => w[0])
        .join("")
        .toUpperCase();
    const max = Math.max(1, ...squad.map((s) => s.goals + s.assists));
    const note = state.venue !== "all" ? `<li class="muted small">Totais da competição (o filtro casa/fora não se aplica ao elenco).</li>` : "";
    box.innerHTML =
      note +
      squad
        .slice(0, 12)
        .map((s, i) => {
          const games = isNum(s.starts) ? `${num(s.apps)} j (${num(s.starts)} tit.)` : `${num(s.apps)} j`;
          const extra = isNum(s.minutes)
            ? `${num(s.minutes)}' · G+A/90: ${num(s.ga_per90, 2)}`
            : `${num(s.shots)} fin. (${num(s.shots_on_target)} no gol) · ${num(s.yellow)} CA · ${num(s.red)} CV`;
          const perGame = isNum(s.minutes) ? `G+A por 90: ${num(s.ga_per90, 2)}` : `G+A por jogo: ${num(s.ga_per_game, 2)}`;
          const avatar = s.photo
            ? `<img src="${esc(s.photo)}" alt="" loading="lazy" class="avatar" data-initials="${esc(initials(s.player))}" />`
            : `<span class="avatar">${esc(initials(s.player))}</span>`;
          const position = POSITION_PT[s.position] ?? s.position;
          const details = s.apps > 0 || isNum(s.minutes) ? [position, games, extra] : [position, "sem dados de jogos na fonte"];
          return `<li><span class="rank__pos">${i + 1}</span>
            <div><div class="rank__name">${avatar}<span>${esc(s.player)} <small>${esc(details.filter(Boolean).join(" · "))}</small></span></div>
            <div class="rank__bar" style="width:${((s.goals + s.assists) / max) * 100}%"></div></div>
            <span class="rank__val" title="${esc(perGame)}">${num(s.goals)}G ${num(s.assists)}A</span></li>`;
        })
        .join("");
    box.querySelectorAll("img.avatar").forEach((photo) =>
      photo.addEventListener(
        "error",
        () => {
          const fallback = document.createElement("span");
          fallback.className = "avatar";
          fallback.textContent = photo.dataset.initials ?? "";
          photo.replaceWith(fallback);
        },
        { once: true },
      ),
    );
    return;
  }
  const data = view?.scorers ?? { scorers: [], assists: [] };
  const list = mode === "assists" ? data.assists : data.scorers;
  const field = mode === "assists" ? "assists" : "goals";
  if (!list.length) {
    box.innerHTML = `<li>${empty("Sem dados de gols para este filtro.")}</li>`;
    return;
  }
  const max = Math.max(1, ...list.map((s) => s[field]));
  box.innerHTML =
    list
      .map(
        (s, i) => `<li><span class="rank__pos">${i + 1}</span>
          <div><div class="rank__name">${esc(s.player)}${mode === "scorers" && s.penalties ? ` <small>(${s.penalties} pên.)</small>` : ""}</div>
          <div class="rank__bar" style="width:${(s[field] / max) * 100}%"></div></div>
          <span class="rank__val">${s[field]}</span></li>`
      )
      .join("") +
    (mode === "scorers" && data.own_goals_for ? `<li><span></span><span class="muted small">+ ${data.own_goals_for} gol(s) contra a favor</span><span></span></li>` : "");
}

/* ------------------------------------------------------------------ calendário, descanso, adversários */
function renderCalendar(view) {
  const cal = view?.calendar ?? [];
  const p = palette();
  const year = String(state.season);
  setChart("chart-calendar", {
    tooltip: {
      ...baseOption().tooltip,
      formatter: (it) => {
        const d = cal.find((c) => c.day === it.value[0]);
        return d ? `<b>${esc(fmtDate(d.day + "T12:00:00"))}</b><br/>${esc(d.label)}` : "";
      },
    },
    visualMap: {
      show: false,
      type: "piecewise",
      dimension: 1,
      pieces: [
        { value: 3, color: p.W },
        { value: 1, color: p.D },
        { value: 0, color: p.L },
      ],
    },
    calendar: {
      range: year,
      top: 30,
      left: 30,
      right: 10,
      cellSize: ["auto", 16],
      yearLabel: { show: false },
      dayLabel: { firstDay: 1, nameMap: ["D", "S", "T", "Q", "Q", "S", "S"], color: p.muted, fontSize: 10 },
      monthLabel: { nameMap: ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"], color: p.muted, fontSize: 10 },
      itemStyle: { color: p.bg2, borderColor: p.card, borderWidth: 3 },
      splitLine: { show: false },
    },
    series: [{ type: "heatmap", coordinateSystem: "calendar", data: cal.map((c) => [c.day, c.points]) }],
  });

  const rest = view?.rest ?? [];
  setChart("chart-rest", {
    grid: { left: 36, right: 10, top: 16, bottom: 26 },
    tooltip: {
      ...baseOption().tooltip,
      trigger: "axis",
      formatter: (items) => {
        const r = rest[items[0].dataIndex];
        return `${esc(r.label)} de descanso<br/>Aproveitamento <b>${pct(r.rate)}</b> em ${r.games} jogos`;
      },
    },
    xAxis: { type: "category", data: rest.map((r) => r.label), ...axisStyle(), splitLine: { show: false } },
    yAxis: { type: "value", min: 0, max: 1, ...axisStyle(), axisLabel: { ...axisStyle().axisLabel, formatter: (v) => `${Math.round(v * 100)}%` } },
    series: [
      {
        type: "bar",
        barWidth: "50%",
        data: rest.map((r) => r.rate ?? 0),
        itemStyle: { color: p.ink, borderRadius: [6, 6, 0, 0] },
        label: { show: true, position: "top", color: p.muted, fontSize: 10, formatter: (it) => `${rest[it.dataIndex].games}j` },
      },
    ],
  });

  const tiers = state.data.opponent_tiers ?? [];
  $("tiers").innerHTML = tiers.length
    ? `<p class="muted small" style="margin:0">Brasileirão ${state.data.meta.current_season}, pela posição atual do adversário</p>` +
      tiers
        .map(
          (t) => `<div><div class="tier__label"><span>${esc(t.label)}</span><b>${pct(t.rate, 0)} <span class="muted small">(${t.games}j)</span></b></div>
            <div class="tier__bar"><span style="width:${(t.rate ?? 0) * 100}%"></span></div></div>`
        )
        .join("")
    : `<p class="muted small">Sem tabela do Brasileirão disponível.</p>`;
}

/* ------------------------------------------------------------------ sequências */
function renderStreaks(view) {
  const s = view?.streaks;
  if (!s) {
    $("streaks").innerHTML = empty("Sem jogos");
    $("highlights").innerHTML = "";
    return;
  }
  const tile = (value, label, sub) => `<div class="streak"><strong>${value}</strong><span>${label}</span>${sub ? `<em>${sub}</em>` : ""}</div>`;
  const cur = s.current.count ? `${s.current.count} ${s.current.label}` : "—";
  $("streaks").innerHTML =
    tile(s.longest_unbeaten, "Maior invencibilidade", s.current_unbeaten ? `atual: ${s.current_unbeaten}` : "") +
    tile(s.longest_wins, "Vitórias seguidas", "") +
    tile(s.longest_winless, "Maior jejum de vitórias", s.current_winless ? `atual: ${s.current_winless}` : "") +
    tile(s.longest_scoring, "Jogos seguidos marcando", s.current_scoring ? `atual: ${s.current_scoring}` : "") +
    tile(s.longest_clean_sheets, "Jogos seguidos sem sofrer", "") +
    tile(cur, "Sequência atual", "");
  const k = view.kpis;
  const hl = (label, g) =>
    g
      ? `<div class="hl">${img(g.logo)}<div><small>${label}</small>${esc(g.opponent)} · ${esc(g.comp)} · ${esc(fmtDate(g.date))}</div><strong>${esc(g.score)}</strong></div>`
      : "";
  $("highlights").innerHTML = hl("Maior vitória", k.biggest_win) + hl("Pior derrota", k.biggest_loss);
}

/* ------------------------------------------------------------------ análises ampliadas */
function analysisTitle(title, description, tag = "") {
  return `<div class="analysis-head"><div><p class="eyebrow eyebrow--red">Leitura executiva</p><h2>${esc(title)}</h2><p>${esc(description)}</p></div>${tag ? `<span class="analysis-status">${esc(tag)}</span>` : ""}</div>`;
}

function renderCoachAnalysis(data) {
  if (!data?.available) return empty(data?.reason ?? "Análise de técnicos indisponível.");
  const cards = data.coaches
    .map((c) => {
      const k = c.kpis;
      return `<article class="analysis-card coach-card ${c.current ? "is-current" : ""}">
        <div class="analysis-card__top"><span class="analysis-card__label">${c.current ? "Técnico atual" : c.interim ? "Interino" : "Treinador"}</span><span class="analysis-card__dot"></span></div>
        <h3>${esc(c.name)}</h3>
        <p class="analysis-period">${fmtDate(c.start, { day: "2-digit", month: "2-digit", year: "numeric" })} — ${c.end ? fmtDate(c.end, { day: "2-digit", month: "2-digit", year: "numeric" }) : "atual"}</p>
        <strong class="big">${pct(k.rate)}</strong><p>aproveitamento · ${num(k.games)} jogos</p>
        <div class="coach-wdl"><span><b>${num(k.w)}</b>V</span><span><b>${num(k.d)}</b>E</span><span><b>${num(k.l)}</b>D</span></div>
        <div class="analysis-card__foot"><span>Casa <b>${pct(c.home_rate)}</b></span><span>Fora <b>${pct(c.away_rate)}</b></span></div>
      </article>`;
    })
    .join("");
  return analysisTitle("Os 5 últimos técnicos", "Jogos atribuídos pelas datas de cada trabalho. A comparação usa o mesmo banco de partidas do dashboard.", `${data.coaches.length} trabalhos`) +
    `<div class="analysis-grid analysis-grid--coaches">${cards}</div><div class="analysis-block"><div class="analysis-subhead"><div><span class="analysis-kicker">Comparação direta</span><h3>Curva de aproveitamento</h3></div><span class="muted small">primeiros ${num(data.curve_games)} jogos</span></div><div class="chart chart--analysis" id="chart-coaches"></div></div>`;
}

function paintCoachChart(data) {
  if (!data?.available) return;
  const p = palette();
  setChart("chart-coaches", {
    tooltip: { trigger: "axis", valueFormatter: (v) => pct(v) },
    legend: { type: "scroll", textStyle: { color: p.muted } },
    grid: { left: 42, right: 18, top: 42, bottom: 30 },
    xAxis: { type: "value", min: 1, name: "jogos", ...axisStyle() },
    yAxis: { type: "value", min: 0, max: 1, axisLabel: { formatter: (v) => `${v * 100}%`, color: p.muted }, splitLine: axisStyle().splitLine },
    series: data.coaches.map((c) => ({ name: c.name, type: "line", showSymbol: false, data: c.curve.map((v, i) => [i + 1, v]), lineStyle: { width: c.current ? 3 : 1.5, type: c.interim ? "dashed" : "solid" } })),
  });
}

function renderSquadAnalysis(data) {
  if (!data?.available) return empty(data?.reason ?? "Diagnóstico do elenco indisponível.");
  const need = data.need ?? {};
  const sectors = data.sectors.map((s) => `<article class="analysis-card sector-card need--${need[s.sector] >= 5 ? "high" : need[s.sector] >= 2 ? "medium" : "low"}"><div class="analysis-card__top"><span class="sector-code">${esc(POSITION_PT[s.sector] ?? s.sector)}</span><span class="analysis-card__label">necessidade ${num(need[s.sector] ?? 0)}</span></div><h3>${esc(s.label)}</h3><div class="sector-main"><strong class="big">${num(s.regulars)}<small>/${num(s.slots)}</small></strong><span>peças de rotação<br/>por vagas usuais</span></div><div class="analysis-meta"><span class="analysis-chip">${num(s.players)} utilizados</span><span class="analysis-chip">idade ${num(s.avg_age, 1)}</span><span class="analysis-chip">${num(s.goals)}G + ${num(s.assists)}A</span></div></article>`).join("");
  const flags = data.flags.length ? data.flags.map((f) => `<li class="analysis-alert"><i class="severity severity--${esc(f.severity)}"></i><div><strong>${esc(f.title)} · ${esc(f.sector_label)}</strong><span>${esc(f.detail)}</span></div></li>`).join("") : `<li>${empty("Nenhum alerta disparado pelas regras atuais.")}</li>`;
  const dependency = data.dependency;
  return analysisTitle("Raio-X do elenco", "Alertas heurísticos calculados com titularidade, idade, produção, lesões, gols sofridos e posição no Brasileirão.", `${num(data.team_games)} jogos`) +
    `<div class="analysis-note">Um alerta aponta onde investigar; não prova sozinho que um atleta ou setor é deficiente. Idades e lesões dependem da disponibilidade do elenco ESPN.</div>
     <div class="analysis-grid">${sectors}</div>
     <div class="analysis-split"><div class="analysis-block"><div class="analysis-subhead"><div><span class="analysis-kicker">Diagnóstico</span><h3>Pontos de atenção</h3></div><span class="muted small">alta · média · baixa</span></div><ul class="analysis-list">${flags}</ul></div>
     <div class="analysis-block dependency-card"><div class="analysis-subhead"><div><span class="analysis-kicker">Concentração</span><h3>Dependência ofensiva</h3></div></div><strong class="big">${pct(dependency.top3_share)}</strong><p>das participações em gols estão concentradas nos três líderes</p><div class="dependency-rank">${dependency.top.map((x, i) => `<div><span>${i + 1}</span><strong>${esc(x.player)}</strong><small>${num(x.ga)} participações</small><b>${pct(x.share)}</b></div>`).join("")}</div></div></div>`;
}

function renderScoutingAnalysis(data) {
  if (!data?.available) return empty(data?.reason ?? "Scouting indisponível.");
  const sectors = data.sectors.map((s) => `<section class="market-sector"><div class="analysis-subhead"><div><span class="analysis-kicker">${esc(POSITION_PT[s.sector] ?? s.sector)}</span><h3>${s.need ? "Prioridade do elenco" : "Monitoramento"}</h3></div><span class="need-badge need-badge--${s.need >= 5 ? "high" : s.need >= 2 ? "medium" : "low"}">necessidade ${num(s.need)}</span></div><p class="market-metric">Métrica: ${esc(s.metric)}</p>
    ${s.limited ? '<div class="analysis-note">Leitura defensiva limitada: a fonte não oferece desarmes, interceptações ou minutos.</div>' : ""}
    <div class="analysis-grid analysis-grid--3">${s.candidates.slice(0, 6).map((c, i) => `<article class="analysis-card market-player"><span class="market-rank">${String(i + 1).padStart(2, "0")}</span><div class="market-identity">${img(c.team_logo)}<div><strong>${esc(c.player)}</strong><small>${esc(c.team)} · ${esc(c.league)}</small></div></div><div class="market-data"><span>${num(c.age)} anos</span><span>${num(c.apps)} jogos</span><span>${num(c.goals)}G · ${num(c.assists)}A</span></div><div class="market-footer"><div class="market-score"><b>${num(c.index)}</b><small>índice técnico</small></div><div class="market-value"><b>${isNum(c.value_eur_m) ? `€ ${num(c.value_eur_m, 1)} mi` : "—"}</b><small>${isNum(c.cost_benefit) ? `${num(c.cost_benefit, 1)} índice/€ mi` : "valor não informado"}</small></div></div></article>`).join("") || empty("Sem candidatos após os filtros.")}</div></section>`).join("");
  return analysisTitle("Radar de mercado", "Produção ajustada pela força da liga. O índice 0–100 compara atletas da mesma posição; custo-benefício só aparece quando há valor de mercado documentado.", `${num(data.pool_size)} elegíveis`) +
    `<div class="analysis-note">Lista exploratória, não recomendação de contratação. Não inclui salário, duração de contrato, adaptação, lesões históricas ou análise de vídeo.</div>${sectors}`;
}

function renderFinanceAnalysis(data) {
  if (!data) return empty("Análise financeira indisponível.");
  const years = [...(data.years ?? [])].sort((a, b) => b.year - a.year);
  const cards = years.map((y) => {
    const previous = years.find((p) => p.year === y.year - 1)?.official;
    const debtChange = y.official?.debt_total && previous?.debt_total ? y.official.debt_total / previous.debt_total - 1 : null;
    return `<article class="analysis-card finance-card ${y.official ? "" : "is-estimate"}"><div class="analysis-card__top"><span class="finance-year">${esc(y.year)}</span><span class="analysis-card__label">${y.official ? "oficial" : "estimativa parcial"}</span></div><strong class="big money">${money(y.official?.revenue_total ?? y.estimated_total, data.currency)}</strong><p>receita ${y.official ? "publicada" : "calculada até agora"}</p><div class="finance-breakdown"><div><span>Bilheteria estimada</span><b>${money(y.ticket_est, data.currency)}</b></div><div><span>Prêmios estimados</span><b>${money(y.prizes_est, data.currency)}</b></div>${y.official?.debt_total ? `<div class="finance-debt"><span>Passivo oficial</span><b>${money(y.official.debt_total, data.currency)}${isNum(debtChange) ? ` <small>${signed(debtChange * 100, 1)}% a/a</small>` : ""}</b></div>` : ""}</div>${y.official?.source ? `<a class="source-link" href="${esc(y.official.source)}" target="_blank" rel="noopener">Ver demonstração <span>↗</span></a>` : ""}</article>`;
  }).join("");
  const ban = data.transfer_ban;
  const factors = (ban.factors ?? []).map((f) => `<li class="analysis-alert"><i class="severity severity--alta"></i><div><strong>${esc(f.label)}</strong><span>${num(f.points)} pontos no indicador</span></div></li>`).join("");
  const debts = (ban.debts ?? []).map((d) => `<tr><td>${esc(d.creditor)}</td><td>${money(d.amount, d.currency)}</td><td>${esc(d.status)}</td><td>${d.fifa_case ? "FIFA" : "nacional"}</td><td>${d.source ? `<a class="source-link" href="${esc(d.source)}" target="_blank" rel="noopener">fonte</a>` : "—"}</td></tr>`).join("");
  const methodology = Object.values(data.methodology ?? {}).map((x) => `<li class="analysis-alert"><i class="severity"></i><div><span>${esc(x)}</span></div></li>`).join("");
  return analysisTitle("Receita, passivo e transfer ban", "Receitas oficiais vêm das demonstrações; o ano corrente soma apenas bilheteria aproximada e premiações configuradas, portanto é uma estimativa parcial.", ban.level) +
    `<div class="finance-kpis">${cards}</div>
     <div class="analysis-split"><div class="analysis-block risk-card"><div class="analysis-subhead"><div><span class="analysis-kicker">Indicador editorial</span><h3>Risco de transfer ban</h3>${ban.as_of ? `<span class="data-asof">Dados verificados em ${fmtDate(ban.as_of, { day: "2-digit", month: "2-digit", year: "numeric" })}</span>` : ""}</div><strong class="risk-score ${ban.active ? "risk-critical" : ""}">${num(ban.score)}<small>/100</small></strong></div><div class="risk-level">${esc(ban.level)}</div><div class="risk-meter"><span style="width:${Math.max(0, Math.min(100, ban.score))}%"></span></div><ul class="analysis-list">${factors || `<li>${empty("Sem fatores cadastrados.")}</li>`}</ul></div>
     <div class="analysis-block"><div class="analysis-subhead"><div><span class="analysis-kicker">Cobertura</span><h3>Limites da estimativa</h3></div><span class="analysis-count">${num((data.missing ?? []).length)}</span></div><ul class="analysis-list analysis-list--scroll">${(data.missing ?? []).map((x) => `<li class="analysis-alert"><i class="severity"></i><div><span>${esc(x)}</span></div></li>`).join("") || `<li>${empty("Sem lacunas registradas.")}</li>`}</ul></div></div>
     <div class="analysis-subhead"><h3>Como ler os números</h3></div><ul class="analysis-list">${methodology}</ul>
     <div class="analysis-subhead"><h3>Obrigações monitoradas</h3><span class="muted small">cadastro editorial com links de origem</span></div><div class="table-wrap"><table class="table"><thead><tr><th>Credor / caso</th><th>Valor publicado</th><th>Status</th><th>Âmbito</th><th>Origem</th></tr></thead><tbody>${debts || '<tr><td colspan="5">Nenhuma obrigação cadastrada.</td></tr>'}</tbody></table></div>`;
}

function renderQualityAnalysis(data) {
  if (!data) return empty("Diagnóstico de qualidade indisponível.");
  const indicators = (data.indicators ?? []).map((item) => `<article class="analysis-card quality-card"><div class="analysis-card__top"><span class="analysis-card__label">Cobertura</span><strong>${pct(item.rate, 0)}</strong></div><h3>${esc(item.label)}</h3><div class="quality-bar"><span style="width:${Math.max(0, Math.min(100, (item.rate ?? 0) * 100))}%"></span></div><p>${num(item.count)} de ${num(item.total)} registros preenchidos</p></article>`).join("");
  const issues = (data.issues ?? []).map((issue) => `<li class="analysis-alert"><i class="severity severity--media"></i><div><span>${esc(issue)}</span></div></li>`).join("");
  return analysisTitle("Qualidade e cobertura", "Transparência sobre o que existe no artefato atual. Cobertura alta não garante que uma fonte esteja correta; indica apenas presença do dado.", `${num(data.matches.completed)} jogos`) +
    `<div class="quality-summary"><div><span>Concluídos</span><strong>${num(data.matches.completed)}</strong></div><div><span>Agendados</span><strong>${num(data.matches.scheduled)}</strong></div><div><span>Gols detalhados</span><strong>${num(data.goals)}</strong></div><div><span>Atletas no elenco</span><strong>${num(data.squad_players)}</strong></div><div><span>Times na tabela</span><strong>${num(data.standings_teams)}</strong></div></div>
     <div class="analysis-grid quality-grid">${indicators}</div>
     <div class="analysis-split"><div class="analysis-block"><div class="analysis-subhead"><div><span class="analysis-kicker">Auditoria automática</span><h3>Pontos de atenção</h3></div><span class="analysis-count">${num((data.issues ?? []).length)}</span></div><ul class="analysis-list">${issues || `<li>${empty("Nenhum alerta estrutural encontrado.")}</li>`}</ul></div><div class="analysis-block quality-definition"><span class="analysis-kicker">Definição</span><h3>Como interpretar</h3><p>${esc(data.definition)}</p><p>Dados financeiros, valores de mercado e transfer bans são editoriais e possuem suas próprias fontes e datas-base.</p></div></div>`;
}

function renderAnalysis() {
  const panel = $("analysis-panel");
  if (!panel) return;
  const previousCoachChart = charts.get("chart-coaches");
  if (previousCoachChart) {
    if (!previousCoachChart.isDisposed?.()) previousCoachChart.dispose();
    charts.delete("chart-coaches");
  }
  const analysis = state.data.analysis;
  if (!analysis) {
    panel.innerHTML = empty("Execute uma atualização do ETL para gerar as novas análises.");
    return;
  }
  const renderers = { coaches: renderCoachAnalysis, squad: renderSquadAnalysis, scouting: renderScoutingAnalysis, finance: renderFinanceAnalysis, quality: renderQualityAnalysis };
  panel.innerHTML = renderers[state.analysisTab](analysis[state.analysisTab]);
  if (state.analysisTab === "coaches") paintCoachChart(analysis.coaches);
}

/* ------------------------------------------------------------------ rodapé */
function renderFooter() {
  const { meta } = state.data;
  $("brand-logo").src = meta.team_logo;
  $("updated").textContent = new Date(meta.generated_at).toLocaleString("pt-BR");
  $("sources").innerHTML = meta.sources
    .map(
      (s) =>
        `<li><b>${esc(s.label)}</b>: ${s.enabled ? `${s.calls} chamadas, ${s.cache_hits} do cache${s.errors.length ? `, ${s.errors.length} erro(s)` : ""}` : "desativada (sem chave)"}</li>`
    )
    .join("");
  $("notes").innerHTML = meta.notes.length ? meta.notes.map((n) => `<li>${esc(n)}</li>`).join("") : "<li>Nenhum aviso.</li>";
}

/* ------------------------------------------------------------------ orquestração */
function renderFiltered() {
  const view = currentView();
  renderHero(view);
  renderKpis(view);
  renderEvolution(view);
  renderResults(view);
  renderHomeAway();
  renderGames(view);
  renderCompetitions();
  renderGoals(view);
  renderCalendar(view);
  renderStreaks(view);
  renderSeasons();
}

function renderStatic() {
  renderScenarios();
  renderArrival();
  renderClassicos();
  renderNextMatch();
  renderFooter();
  renderAnalysis();
}

function setupTheme() {
  const saved = localStorage.getItem("theme");
  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = saved ?? (prefersDark ? "dark" : "light");
  $("theme-toggle").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("theme", next);
    if (state.data) {
      renderFiltered();
      renderScenarios();
      renderAnalysis();
    }
  };
}

function setupReveal() {
  const items = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) {
    items.forEach((el) => el.classList.add("is-visible"));
    return;
  }
  const io = new IntersectionObserver(
    (entries) =>
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add("is-visible");
          io.unobserve(e.target);
          e.target.querySelectorAll(".chart").forEach((c) => charts.get(c.id)?.resize());
        }
      }),
    { threshold: 0.08 }
  );
  items.forEach((el) => io.observe(el));
}

/* ------------------------------------------------------------------ atualização diária */
const STATUS_URL = "/api/status";
const REFRESH_URL = "/api/refresh";
const REFRESH_POLL_MS = 4000;
let toastTimer = null;

function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("is-error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 6000);
}

async function fetchStatus() {
  const response = await fetch(STATUS_URL, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

const fmtNextAllowed = (iso) =>
  iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "amanhã";

function paintRefresh(status) {
  const btn = $("refresh-btn");
  btn.hidden = false;
  btn.classList.toggle("is-running", Boolean(status.running));
  btn.classList.toggle("is-done", Boolean(status.refreshed_today) && !status.running);
  btn.disabled = !status.can_refresh;
  if (status.running) {
    $("refresh-label").textContent = "Atualizando…";
    btn.title = "Buscando dados nas APIs. O dash recarrega sozinho ao terminar.";
  } else if (status.refreshed_today) {
    $("refresh-label").textContent = "Atualizado hoje";
    btn.title = `Os dados já foram puxados hoje${
      status.last_refresh ? ` (${new Date(status.last_refresh).toLocaleTimeString("pt-BR")})` : ""
    }. Nova atualização liberada em ${fmtNextAllowed(status.next_allowed)}.`;
  } else {
    $("refresh-label").textContent = "Atualizar dados";
    btn.title = status.last_error ? `Última tentativa falhou:\n${status.last_error}` : "Puxar dados novos (1 vez por dia)";
  }
}

function pollRefresh() {
  setTimeout(async () => {
    let status;
    try {
      status = await fetchStatus();
    } catch {
      pollRefresh();
      return;
    }
    if (status.running) {
      paintRefresh(status);
      pollRefresh();
    } else if (status.last_error) {
      paintRefresh(status);
      toast(`A atualização falhou:\n${status.last_error}`, true);
    } else {
      location.reload();
    }
  }, REFRESH_POLL_MS);
}

async function setupRefresh() {
  const btn = $("refresh-btn");
  let status;
  try {
    status = await fetchStatus();
  } catch {
    btn.hidden = true;
    return null;
  }
  paintRefresh(status);
  if (status.running) pollRefresh();

  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const response = await fetch(REFRESH_URL, { method: "POST", headers: { "X-Dash-Refresh": "1" } });
      const body = await response.json();
      paintRefresh(body);
      if (response.status === 202 || response.status === 409) {
        toast("Atualização iniciada. Isso pode levar alguns minutos.");
        pollRefresh();
      } else if (response.status === 429) {
        toast(`Os dados já foram puxados hoje. Tente de novo a partir de ${fmtNextAllowed(body.next_allowed)}.`, true);
      } else {
        toast(`Não foi possível atualizar (HTTP ${response.status}).`, true);
      }
    } catch (err) {
      toast(`Servidor indisponível: ${err instanceof Error ? err.message : String(err)}`, true);
      btn.disabled = false;
    }
  };
  return status;
}

function showFatal(message) {
  const loading = $("loading");
  loading.innerHTML = `<div class="empty" style="max-width:520px">${message}</div>`;
}

async function init() {
  setupTheme();
  setupReveal();
  const refreshStatus = await setupRefresh();
  if (!window.echarts) {
    showFatal("Não foi possível carregar o ECharts (CDN). Verifique a conexão.");
    return;
  }
  let data;
  try {
    const response = await fetch(DATA_URL, { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (err) {
    if (refreshStatus?.running) {
      showFatal("Primeira carga dos dados em andamento (pode levar alguns minutos).<br/>A página recarrega sozinha ao terminar.");
      return;
    }
    showFatal(
      `Não encontrei <code>web/data/dashboard.json</code> (${esc(err instanceof Error ? err.message : String(err))}).<br/><br/>
       Inicie o servidor com <code>python server.py</code> na raiz do projeto: ele gera os dados automaticamente.`
    );
    return;
  }
  state.data = data;
  state.season = data.meta.current_season;
  if (!data.meta.seasons.includes(state.season)) state.season = Math.max(...data.meta.seasons);

  bindStaticSegment("filter-venue", (v) => {
    state.venue = v;
    renderFiltered();
  });
  bindStaticSegment("seasons-mode", (v) => {
    state.seasonsMode = v;
    renderSeasons();
  });
  bindStaticSegment("scorer-mode", (v) => {
    state.scorerMode = v;
    renderScorers(currentView());
  });
  bindStaticSegment("analysis-tabs", (v) => {
    state.analysisTab = v;
    renderAnalysis();
  });

  renderFilters();
  renderStatic();
  renderFiltered();

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => charts.forEach((c) => c.resize()), 120);
  });
  $("loading").classList.add("is-done");
}

init();
