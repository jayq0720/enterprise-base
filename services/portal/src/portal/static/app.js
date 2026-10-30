// 员工服务台页面。只用 textContent 写入服务端返回的内容，不拼 HTML。
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const STAGES = { manager: "主管审批", hr: "HR 审批", returned: "已退回待修改" };
const TABS = [
  { id: "hr", label: "HR 助手", show: (me) => me.chat.hr },
  { id: "policy", label: "制度问答", show: (me) => me.chat.policy },
  { id: "leave", label: "我的假期", show: (me) => me.features.leave_query || me.features.leave_apply },
  { id: "approval", label: "待我审批", show: (me) => me.features.approval },
  { id: "duties", label: "岗位职责", show: (me) => me.features.duties },
  { id: "inquiry", label: "我的询盘", show: (me) => me.features.inquiry },
  { id: "marketing", label: "询盘助手", show: (me) => me.chat.marketing && me.features.inquiry },
  { id: "inquiry-stats", label: "询盘统计", show: (me) => me.features.inquiry_stats },
  { id: "knowledge", label: "知识检索", show: () => true },
];

async function api(path, body) {
  const init = { headers: { "X-EB-Portal": "1" }, credentials: "same-origin" };
  if (body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(path, init);
  if (resp.status === 401) {
    showLogin();
    throw new Error("请先登录");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || "系统暂时不可用，请稍后再试。");
  return data;
}

function cell(row, text) {
  const td = document.createElement("td");
  td.textContent = text === undefined || text === null ? "" : String(text);
  row.appendChild(td);
  return td;
}

function fill(table, rows, columns, empty) {
  const body = $("tbody", table);
  body.replaceChildren();
  if (!rows.length) {
    const tr = body.insertRow();
    const td = cell(tr, empty);
    td.colSpan = $("thead tr", table).children.length;
    td.className = "muted";
    return;
  }
  for (const r of rows) {
    const tr = body.insertRow();
    columns(r).forEach((v) => (v instanceof Node ? cell(tr, "").appendChild(v) : cell(tr, v)));
  }
}

function fail(err) {
  const n = $("#notice");
  n.textContent = err.message || String(err);
  n.hidden = false;
}

function showLogin() {
  $("#app").hidden = true;
  $("#login").hidden = false;
  const reason = new URLSearchParams(location.search).get("login");
  const msg = { failed: "登录没有成功，请重新扫码。", denied: "没有找到你的在职员工信息，请联系 HR。" }[reason];
  if (msg) {
    $("#login-error").textContent = msg;
    $("#login-error").hidden = false;
  }
}

// —— 对话 ——
function setupChat(panel, app) {
  let conversation = "";
  const box = $(".messages", panel);
  const add = (who, text) => {
    const p = document.createElement("div");
    p.className = "msg " + who;
    p.textContent = text;
    box.appendChild(p);
    box.scrollTop = box.scrollHeight;
    return p;
  };
  $(".reset", panel).addEventListener("click", () => {
    conversation = "";
    box.replaceChildren();
  });
  $("form", panel).addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = e.target.q;
    const query = input.value.trim();
    if (!query) return;
    input.value = "";
    add("me", query);
    const waiting = add("bot pending", "正在思考……");
    try {
      const r = await api("/api/chat", { app, query, conversation_id: conversation });
      conversation = r.conversation_id || conversation;
      waiting.textContent = r.answer || r.error || "（没有回答）";
    } catch (err) {
      waiting.textContent = err.message;
    }
    waiting.classList.remove("pending");
  });
}

// —— 假期 ——
async function loadLeave(me) {
  if (me.features.leave_query) {
    const b = await api("/api/leave/balance");
    if (b.error) fail(new Error(b.error));
    fill($("#balances"), b.balances || [], (r) => [r.leave_type, r.total, r.taken, r.pending_approval, r.remaining], "暂无假期额度");
    const a = await api("/api/leave/applications");
    fill(
      $("#applications"),
      a.applications || [],
      (r) => [r.id, r.leave_type, `${r.from_date} 至 ${r.to_date}`, r.days, STAGES[r.stage] || r.status, r.approver],
      "暂无请假记录",
    );
  }
  if (me.features.leave_apply) {
    $("#apply").hidden = false;
    const t = await api("/api/leave/types");
    const select = $("#leave-form select");
    select.replaceChildren(...(t.leave_types || []).map((name) => new Option(name, name)));
  }
}

function setupLeave(me) {
  let token = "";
  $("#leave-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const body = Object.fromEntries(["leave_type", "from_date", "to_date", "reason"].map((k) => [k, f[k].value]));
    try {
      const r = await api("/api/leave/draft", body);
      if (r.error) throw new Error(r.error);
      token = r.draft_token;
      const d = r.draft;
      const left = r.remaining === null || r.remaining === undefined ? "" : `，当前剩余 ${r.remaining} 天`;
      $("#draft-text").textContent = `${d.leave_type}：${d.from_date} 至 ${d.to_date}${d.reason ? "，事由：" + d.reason : ""}${left}。还没有提交，请核对后点“确认提交”。`;
      $("#draft").hidden = false;
    } catch (err) {
      fail(err);
    }
  });
  $("#draft-cancel").addEventListener("click", () => {
    token = "";
    $("#draft").hidden = true;
  });
  $("#draft-confirm").addEventListener("click", async () => {
    try {
      const r = await api("/api/leave/confirm", { draft_token: token });
      $("#draft-text").textContent = r.ok ? r.message : r.error;
      token = "";
      $("#draft-confirm").hidden = !!r.ok;
      if (r.ok) await loadLeave(me);
    } catch (err) {
      fail(err);
    }
  });
}

// —— 审批 ——
async function loadApprovals() {
  const r = await api("/api/approvals");
  if (r.error) fail(new Error(r.error));
  fill(
    $("#pending"),
    r.applications || [],
    (row) => {
      const actions = document.createElement("span");
      for (const [action, label] of [["approve", "通过"], ["return", "退回"]]) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = action === "approve" ? "button primary small" : "button small";
        b.textContent = label;
        b.addEventListener("click", async () => {
          if (!confirm(`${label} ${row.applicant} 的 ${row.leave_type}（${row.from_date} 至 ${row.to_date}）？`)) return;
          try {
            const d = await api("/api/approvals/decide", { leave: row.id, action });
            if (!d.ok) fail(new Error(d.error));
            await loadApprovals();
          } catch (err) {
            fail(err);
          }
        });
        actions.appendChild(b);
      }
      return [row.applicant, row.leave_type, `${row.from_date} 至 ${row.to_date}`, row.days, STAGES[row.stage] || row.status, actions];
    },
    "没有等你审批的单",
  );
}

// —— 询盘 ——
const SOURCE = { landing_page: "落地页", keyword: "搜索关键词", ai_engine: "AI 引擎", utm_source: "UTM 来源", utm_medium: "UTM 媒介", utm_campaign: "UTM 活动" };
let currentInquiry = "";

async function loadInquiries() {
  const r = await api("/api/inquiries" + ($("#inquiry-closed").checked ? "?closed=1" : ""));
  if (r.error) fail(new Error(r.error));
  fill(
    $("#inquiries"),
    r.inquiries || [],
    (row) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "button small";
      b.textContent = "查看";
      b.addEventListener("click", () => showInquiry(row.id).catch(fail));
      const who = [row.customer, row.company, row.country].filter(Boolean).join(" · ");
      return [row.grade || "-", who, row.product, row.channel, row.received_at, row.status, b];
    },
    "没有分配给你的询盘",
  );
}

function field(list, label, value) {
  if (!value) return;
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  list.append(dt, dd);
}

async function showInquiry(id) {
  const r = await api("/api/inquiries/detail", { inquiry_id: id });
  if (!r.ok) throw new Error(r.error);
  const q = r.inquiry;
  currentInquiry = q.id;
  $("#inquiry").hidden = false;
  $("#inquiry-title").textContent = `${q.id} · ${q.grade || "-"} 级 · ${q.status}`;
  const list = $("#inquiry-fields");
  list.replaceChildren();
  field(list, "客户", [q.customer, q.company].filter(Boolean).join("，"));
  field(list, "国家/语言", [q.country, q.language].filter(Boolean).join(" / "));
  field(list, "邮箱", q.contact && q.contact.email);
  field(list, "电话", q.contact && q.contact.phone);
  field(list, "官网", q.website);
  field(list, "产品/数量/时间", [q.product, q.quantity, q.timeline].filter(Boolean).join("，"));
  field(list, "渠道", q.channel);
  for (const [k, label] of Object.entries(SOURCE)) field(list, label, q.source[k]);
  field(list, "评分", `${q.score} 分：${q.score_reasons || ""}`);
  $("#inquiry-requirement").textContent = q.requirement || "（无）";
  $("#inquiry-notes").replaceChildren(
    ...(r.notes.length ? r.notes : [{ note: "暂无跟进记录" }]).map((n) => {
      const li = document.createElement("li");
      li.textContent = n.at ? `${n.at.slice(0, 16)} ${n.by}：${n.note}` : n.note;
      return li;
    }),
  );
  $("#inquiry").scrollIntoView({ behavior: "smooth" });
}

function setupInquiry() {
  $("#inquiry-closed").addEventListener("change", () => loadInquiries().catch(fail));
  $("#follow-up").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const r = await api("/api/inquiries/follow-up", { inquiry_id: currentInquiry, status: f.status.value, note: f.note.value });
      if (!r.ok) throw new Error(r.error);
      f.reset();
      await showInquiry(currentInquiry);
      await loadInquiries();
    } catch (err) {
      fail(err);
    }
  });
}

async function loadStats() {
  const r = await api("/api/inquiries/stats", { days: $("#stats-days").value });
  if (!r.ok) throw new Error(r.error);
  const avg = r.avg_first_response_minutes;
  $("#stats-summary").textContent = `最近 ${r.days} 天新进询盘 ${r.total} 条` + (avg === null ? "。" : `，平均首次响应 ${Math.round(avg)} 分钟。`);
  const box = $("#stats");
  box.replaceChildren();
  for (const [key, title] of [["by_channel", "按渠道"], ["by_grade", "按等级"], ["by_ai_engine", "按 AI 引擎来源"], ["by_status", "按状态"]]) {
    const table = document.createElement("table");
    const head = table.createTHead().insertRow();
    cell(head, title);
    cell(head, "数量");
    const body = table.createTBody();
    for (const [k, n] of Object.entries(r[key] || {})) {
      const tr = body.insertRow();
      cell(tr, k);
      cell(tr, n);
    }
    box.appendChild(table);
  }
  const funnel = r.funnel;
  if (!funnel) return;
  const all = funnel.all;
  $("#stats-summary").textContent += ` 其中有效 ${all.valid} 条，转商机 ${all.opportunity} 条，成交 ${all.won} 条，成交额 ${money(all.revenue)} ${r.currency || ""}。`;
  for (const [key, title] of [["by_channel", "转化：按渠道"], ["by_ai_engine", "转化：按 AI 引擎来源"]]) {
    const table = document.createElement("table");
    table.className = "wide";
    const head = table.createTHead().insertRow();
    for (const h of [title, "询盘", "有效", "已跟进", "转商机", "成交", "成交额"]) cell(head, h);
    const body = table.createTBody();
    for (const [k, v] of Object.entries(funnel[key] || {})) {
      const tr = body.insertRow();
      for (const value of [k, v.total, v.valid, v.responded, v.opportunity, v.won, money(v.revenue)]) cell(tr, value);
    }
    box.appendChild(table);
  }
}

function money(n) {
  return Number(n || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

// —— 岗位职责、知识检索 ——
async function loadDuties() {
  const r = await api("/api/duties");
  $("#duties-title").textContent = r.designation ? `岗位职责：${r.designation}` : "岗位职责";
  $("#duties").textContent = r.responsibilities || r.error || "";
}

function setupSearch() {
  $("#search").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("#results");
    out.replaceChildren();
    try {
      const r = await api("/api/search", { query: e.target.q.value });
      $("#search-notice").textContent = r.notice || (r.records.length ? "" : "没有找到相关内容。");
      for (const rec of r.records) {
        const card = document.createElement("article");
        card.className = "result";
        const h = document.createElement("h3");
        h.textContent = rec.title;
        const meta = document.createElement("p");
        meta.className = "muted";
        meta.textContent = rec.space;
        const body = document.createElement("p");
        body.textContent = rec.content;
        card.append(h, meta, body);
        out.appendChild(card);
      }
    } catch (err) {
      fail(err);
    }
  });
}

const loaders = { leave: loadLeave, approval: loadApprovals, duties: loadDuties, inquiry: loadInquiries, "inquiry-stats": loadStats };

function openTab(id, me) {
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("active", b.dataset.id === id);
  for (const p of document.querySelectorAll(".panel")) p.hidden = p.dataset.tab !== id;
  $("#notice").hidden = !me.notice;
  $("#notice").textContent = me.notice || "";
  if (loaders[id]) loaders[id](me).catch(fail);
}

// 企业微信提醒里的“去处理”链接是 /#inquiry=询盘号；登录跳转会丢掉 #，先记下来
const DEEP = "eb-open-inquiry";
function rememberDeepLink() {
  const m = /^#inquiry=([A-Za-z0-9.-]{1,140})$/.exec(location.hash);
  try {
    if (m) sessionStorage.setItem(DEEP, m[1]);
    const id = sessionStorage.getItem(DEEP);
    return id;
  } catch {
    return m ? m[1] : null;
  }
}

function forgetDeepLink() {
  try {
    sessionStorage.removeItem(DEEP);
  } catch {
    // 浏览器禁用存储时没有可清的
  }
}

async function start() {
  const deep = rememberDeepLink();
  let me;
  try {
    me = await api("/api/me");
  } catch {
    return;
  }
  history.replaceState(null, "", "/");
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#who").textContent = me.designation ? `${me.employee_name} · ${me.designation}` : me.employee_name;
  const tabs = TABS.filter((t) => t.show(me));
  $("#tabs").replaceChildren(
    ...tabs.map((t) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.id = t.id;
      b.textContent = t.label;
      b.addEventListener("click", () => openTab(t.id, me));
      return b;
    }),
  );
  setupChat($('[data-tab="hr"]'), "hr");
  setupChat($('[data-tab="policy"]'), "policy");
  setupLeave(me);
  setupChat($('[data-tab="marketing"]'), "marketing");
  setupInquiry();
  $("#stats-days").addEventListener("change", () => loadStats().catch(fail));
  setupSearch();
  $("#logout").addEventListener("click", async () => {
    await api("/auth/logout", {}).catch(() => {});
    location.replace("/");
  });
  if (deep && me.features.inquiry) {
    forgetDeepLink();
    openTab("inquiry", me);
    showInquiry(deep).catch(fail);
  } else if (tabs.length) openTab(tabs[0].id, me);
}

document.addEventListener("DOMContentLoaded", start);
