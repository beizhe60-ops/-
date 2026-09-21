"use strict";
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const escapeHTML = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const E = escapeHTML;
let state = null,
  page = location.pathname.split("/")[2] || "overview",
  recordOffset = 0,
  recordStatus = "",
  recordSearch = "";
let accountTab = "monitor";
const titles = {
  overview: "运行概览",
  accounts: "账号管理",
  tasks: "任务与过滤",
  records: "消息记录",
  guards: "不再联系",
  settings: "系统设置",
};
const labels = {
  active: "已登录",
  login_required: "待登录",
  disabled: "已停用",
  connection_error: "连接异常",
  flood_wait: "等待恢复",
  banned: "账号受限",
  pending: "待发送",
  waiting: "平台等待",
  sending: "发送中",
  sent: "已发送",
  failed: "发送失败",
  unknown: "结果待核实",
  skipped: "已跳过",
  cancelled: "已取消",
};
const badge = (s, label) =>
  `<span class="badge ${["active", "sent"].includes(s) ? "good" : ["failed", "unknown", "banned", "connection_error"].includes(s) ? "bad" : ["pending", "waiting", "sending"].includes(s) ? "warn" : ""}">${E(label || labels[s] || s)}</span>`;
const accountName = (id) =>
  state.accounts.find((a) => a.id === id)?.name || `账号 ${id}`;
const chatName = (id) =>
  state.chats.find((c) => c.id === id)?.title || String(id);
const date = (s) =>
  s
    ? new Date(s.endsWith("Z") ? s : s + "Z").toLocaleString("zh-CN", {
        hour12: false,
      })
    : "—";
async function api(path, method = "GET", body) {
  const res = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-Console-Request": "1" },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  let data;
  try {
    data = await res.json();
  } catch {
    throw Error("服务器未返回有效结果，请检查服务状态");
  }
  if (res.status === 401) {
    location.href =
      "/admin/login?next=" + encodeURIComponent(location.pathname);
    throw Error("请重新登录");
  }
  if (!res.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || "请求失败"),
    );
  return data;
}
let toastTimer;
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("show"), 4200);
}
function heading(title, desc, action = "") {
  return `<div class="page-heading"><div><h1>${title}</h1><p>${desc}</p></div>${action}</div>`;
}
function empty(title, desc, action = "") {
  return `<div class="empty"><div class="empty-icon">⌁</div><h3>${title}</h3><p>${desc}</p>${action}</div>`;
}
function openModal(title, html) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = html;
  $("#modal").showModal();
}
$("#close-modal").onclick = () => $("#modal").close();
$("#modal").addEventListener("click", (e) => {
  if (e.target === $("#modal")) {
    const r = $("#modal").getBoundingClientRect();
    if (
      e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom
    )
      $("#modal").close();
  }
});
function setupForm(selector, callback) {
  const f = $(selector);
  f.onsubmit = async (e) => {
    e.preventDefault();
    const button = e.submitter || f.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    const err = f.querySelector(".form-error");
    if (err) err.textContent = "";
    try {
      await callback(new FormData(f), f);
    } catch (ex) {
      if (err) err.textContent = ex.message;
      else toast(ex.message);
    } finally {
      if (button) button.disabled = false;
    }
  };
}
function serviceHeader() {
  const b = $("#service-badge"),
    btn = $("#service-button");
  b.className = "badge " + (state.running ? "good" : "");
  b.textContent = state.running ? "● 服务运行中" : "○ 服务已暂停";
  btn.textContent = state.running ? "暂停全部" : "启动服务";
  btn.className = "button " + (state.running ? "danger" : "secondary");
  btn.disabled = false;
}
async function load() {
  state = await api("/state");
  serviceHeader();
  await render();
}
$("#service-button").onclick = async () => {
  const b = $("#service-button");
  b.disabled = true;
  try {
    await api("/service/" + (state.running ? "stop" : "start"), "POST");
    toast(
      state.running
        ? "服务已暂停；进行中的发送可能已经交付，请查看记录"
        : "服务已启动，账号连接状态稍后更新",
    );
    await load();
  } catch (e) {
    toast(e.message);
    b.disabled = false;
  }
};
function flow() {
  return `<div class="panel"><div class="process"><div class="process-step"><span class="step-letter">A</span><div><strong>监听与筛选</strong><p>指定群组 · 关键词与排除规则</p></div></div><span class="arrow">→</span><div class="process-step"><span class="step-letter">⇄</span><div><strong>群组消息中转</strong><p>群组-发言内容-@用户名</p></div></div><span class="arrow">→</span><div class="process-step"><span class="step-letter">B</span><div><strong>自动私信</strong><p>识别末尾用户名 · 使用预设文案</p></div></div></div></div>`;
}
function tasksList() {
  return state.tasks.length
    ? state.tasks
        .map(
          (t) =>
            `<div class="task-row"><div><div class="actions"><span class="task-name">${E(t.name)}</span>${badge(t.enabled ? "active" : "disabled", t.enabled ? "已启用" : "已暂停")}</div><div class="task-flow">${E(accountName(t.account_a))} · ${t.source_chats.length} 个来源群　→　${E(chatName(t.relay_chat))}　→　${E(accountName(t.account_b))}</div><div class="chips">${t.keywords
              .split(/[\n,，]/)
              .filter(Boolean)
              .slice(0, 6)
              .map((w) => `<span class="chip">${E(w)}</span>`)
              .join(
                "",
              )}</div></div><div class="actions"><button class="secondary small" data-edit-task="${t.id}">编辑配置</button><button class="${t.enabled ? "secondary" : ""} small" data-toggle-task="${t.id}">${t.enabled ? "暂停任务" : "启用任务"}</button></div></div>`,
        )
        .join("")
    : empty(
        "创建第一条消息任务",
        "绑定 A、B 账号与中转群，让筛选和发送按照你的规则运行。",
        "<button data-new-task>新建任务</button>",
      );
}
function overview() {
  const s = state.stats;
  return (
    heading(
      "消息流转，一目了然",
      "从群内发言到私信跟进，在一个工作台里管理。",
      '<a class="button" href="/console/tasks">＋ 新建消息任务</a>',
    ) +
    flow() +
    `<div class="metrics">${[
      ["今日关键词命中", s.hits, "A 账号捕获的消息"],
      ["等待发送", s.pending, "含平台要求等待的消息"],
      ["今日私信成功", s.sent, "B 账号发送成功"],
      ["需要关注", s.failed, "失败或结果不确定"],
    ]
      .map(
        ([a, b, c]) =>
          `<div class="metric"><p>${a}</p><b>${b}</b><small>${c}</small></div>`,
      )
      .join(
        "",
      )}</div><div class="columns"><section class="panel"><div class="panel-head"><h2>消息任务</h2><a href="/console/tasks">管理全部</a></div>${tasksList()}</section><section class="panel"><div class="panel-head"><h2>开始使用</h2><span class="subtle">${[state.accounts.length >= 2, state.chats.length > 0, state.tasks.length > 0].filter(Boolean).length} / 3</span></div><div class="panel-body"><ol class="checklist">${[
      [
        "添加并登录 A/B 账号",
        "监听与私信使用独立的账号。",
        state.accounts.length >= 2,
        "accounts",
      ],
      [
        "同步群组列表",
        "A、B 都需要加入同一个中转群。",
        state.chats.length > 0,
        "accounts",
      ],
      [
        "配置并启用任务",
        "设置关键词、文案，再启动服务。",
        state.tasks.length > 0,
        "tasks",
      ],
    ]
      .map(
        ([a, b, c, d], i) =>
          `<li><span class="check-number ${c ? "done" : ""}">${c ? "✓" : i + 1}</span><div><a href="/console/${d}">${a}</a><p>${b}</p></div></li>`,
      )
      .join(
        "",
      )}</ol></div></section></div><div class="callout">启动服务后只监听新消息；模拟测试不会向 Telegram 发送消息。统计日期按服务器 UTC 计算。</div>`
  );
}
function accounts() {
  const isMonitor = accountTab === "monitor";
  const items = state.accounts.filter((a) => a.role === accountTab);
  const roleName = isMonitor ? "监测账号" : "私信账号";
  return (
    heading(
      "账号管理",
      "监测账号负责读取指定群组，私信账号负责接收线索并发送文案。",
    ) +
    `<div class="account-tabs" role="tablist" aria-label="账号类型"><button role="tab" data-account-tab="monitor" aria-selected="${isMonitor}" class="${isMonitor ? "selected" : ""}">监测账号 <span>${state.accounts.filter((a) => a.role === "monitor").length}</span></button><button role="tab" data-account-tab="sender" aria-selected="${!isMonitor}" class="${!isMonitor ? "selected" : ""}">私信账号 <span>${state.accounts.filter((a) => a.role === "sender").length}</span></button></div>
    <div class="account-section-head"><div><h2>${roleName}管理</h2><p class="subtle">${isMonitor ? "登录后填写监测群组 ID，只有指定群组的消息会进入筛选。" : "独立登录，按权重分配同一中转群的私信任务；数字越大，分配越多。"}</p></div><button id="add-account">＋ ${roleName}登录</button></div>
    ${!state.application?.configured ? '<div class="callout">首次使用请先在 <a href="/console/settings">系统设置</a> 完成 Telegram 连接配置，之后登录账号只需手机号、验证码和二级登录密码。</div>' : ""}` +
    (items.length
      ? `<div class="account-grid">${items
          .map(
            (
              a,
            ) => `<article class="account-card"><div class="account-top"><div><h3>${E(a.name)}</h3><span class="phone">${E(a.phone)}</span></div>${badge(a.connected ? "active" : a.status, a.connected ? "● 已连接" : undefined)}</div>
    ${isMonitor ? `<div class="monitor-summary"><div class="actions"><strong>监测群组 ID</strong><span class="badge">${a.monitor_chat_ids.length} 个</span></div>${a.monitor_chat_ids.length ? `<div class="chips">${a.monitor_chat_ids.map((id) => `<span class="chip">${id}</span>`).join("")}</div>` : '<p class="muted">尚未设置，不会监测任何来源群。</p>'}<button class="secondary small" data-monitor-groups="${a.id}">设置监测群组 ID</button></div>` : ""}
    ${!isMonitor ? `<div class="monitor-summary"><strong>轮询权重 <span class="badge">${a.rotation_weight ?? 1}</span></strong><p class="muted">数字越大，分配的私信越多。默认 1。</p><button class="secondary small" data-weight="${a.id}">设置轮询权重</button></div>` : ""}
    <div class="permission-row">${isMonitor ? "允许转发消息" : "允许发送私信"}<input aria-label="${E(a.name)} 允许发送" class="toggle" type="checkbox" data-permission="${a.id}" data-kind="send_enabled" ${a.send_enabled ? "checked" : ""}></div>
    ${a.last_error ? `<p class="error-text">${E(a.last_error)}</p>` : ""}<div class="actions">${!a.has_session ? `<button class="small" data-login="${a.id}">继续登录</button>` : `<button class="secondary small" data-sync="${a.id}">同步群组</button><button class="text-button small" data-logout-account="${a.id}">退出账号</button>`}</div></article>`,
          )
          .join("")}</div>`
      : `<div class="panel">${empty("还没有" + roleName, "使用手机号、验证码和二级登录密码完成登录。", `<button id="empty-add-account">${roleName}登录</button>`)}</div>`)
  );
}

function tasks() {
  return (
    heading(
      "任务与过滤",
      "每条任务连接一组来源群、一个中转群和一个私信账号。",
      '<div class="actions"><button class="secondary" id="standalone-preview">模拟测试</button><button data-new-task>＋ 新建任务</button></div>',
    ) +
    flow() +
    `<section class="panel"><div class="panel-head"><h2>全部任务</h2><span class="muted">${state.tasks.length} 条任务</span></div>${tasksList()}</section><div class="callout">转发格式固定为“群组-该用户发言的内容-@用户名”。B 只处理指定 A 发出的标准消息。编辑任务会暂停任务并取消旧的待发送内容，保存后需重新启用。</div>`
  );
}
async function records() {
  const r = await api(
    "/records?status=" +
      encodeURIComponent(recordStatus) +
      "&q=" +
      encodeURIComponent(recordSearch) +
      "&offset=" +
      recordOffset,
  );
  return (
    heading(
      "消息记录",
      "查看 A 中转与 B 私信的独立记录，定位每次消息流转的结果。",
      '<button class="secondary" id="refresh-records">刷新记录</button>',
    ) +
    `<section class="panel"><div class="panel-head"><div class="filters"><input id="record-search" aria-label="按用户名搜索" placeholder="搜索 @用户名" value="${E(recordSearch)}"><select id="record-status" aria-label="发送状态"><option value="">全部状态</option>${["pending", "waiting", "sending", "sent", "failed", "unknown", "skipped", "cancelled"].map((s) => `<option value="${s}" ${recordStatus === s ? "selected" : ""}>${labels[s]}</option>`).join("")}</select><button class="secondary small" id="search-records">筛选</button></div><span class="muted">${r.total} 条记录</span></div>${r.items.length ? `<div class="table-wrap"><table><thead><tr><th>阶段 / 时间</th><th>用户 / 来源</th><th>发送内容</th><th>状态</th><th>操作</th></tr></thead><tbody>${r.items.map((j) => `<tr><td>${j.stage === "relay" ? "A → 中转群" : "B → 私信"}<small>${E(date(j.created_at))}</small><small>任务 #${j.task_id}</small></td><td>@${E(j.username)}<small>${E(j.source_title)}</small>${j.user_id ? `<small>ID ${j.user_id}</small>` : ""}</td><td><div class="record-text">${E(j.text)}</div><details><summary>原始发言</summary><div class="record-text">${E(j.original_text)}</div></details></td><td>${badge(j.status)}${j.error ? `<small class="error-text">${E(j.error)}</small>` : ""}${j.status === "waiting" ? `<small>${E(date(j.due_at))}</small>` : ""}</td><td><div class="actions">${["pending", "waiting"].includes(j.status) ? `<button class="secondary small" data-cancel-job="${j.id}">取消</button>` : ""}${j.user_id ? `<button class="text-button small" data-block-user="${j.user_id}">不再联系</button>` : ""}</div></td></tr>`).join("")}</tbody></table></div>` : empty("没有符合条件的记录", "任务运行后，关键词命中和自动私信结果会显示在这里。")}<div class="pagination"><span>每页 50 条</span><div class="actions"><button class="secondary small" id="prev-page" ${recordOffset === 0 ? "disabled" : ""}>上一页</button><button class="secondary small" id="next-page" ${recordOffset + 50 >= r.total ? "disabled" : ""}>下一页</button></div></div></section>`
  );
}
async function guards() {
  const items = await api("/guards");
  return (
    heading(
      "不再联系",
      "名单中的用户会在实际发送前被拦截。",
      '<button id="new-guard">＋ 添加用户</button>',
    ) +
    `<section class="panel">${items.length ? `<div class="table-wrap"><table><thead><tr><th>用户 ID</th><th>备注</th><th>操作</th></tr></thead><tbody>${items.map((g) => `<tr><td>${g.user_id}</td><td>${E(g.note || "—")}</td><td><button class="secondary small" data-remove-guard="${g.id}">移出名单</button></td></tr>`).join("")}</tbody></table></div>` : empty("名单为空", "可以从消息记录添加用户，也可以在这里输入 Telegram 用户 ID。")}</section>`
  );
}
function settings() {
  return (
    heading("系统设置", "控制后台运行和管理访问凭据。") +
    `<section class="panel"><div class="panel-head"><h2>Telegram 连接配置</h2>${badge(state.application?.configured ? "active" : "disabled", state.application?.configured ? "已配置" : "待配置")}</div><div class="panel-body"><p class="subtle">首次使用配置一次，监测账号和私信账号共用；账号登录页面只需手机号、验证码和二级登录密码。修改后用于后续新增账号，不改变现有登录会话。</p><form id="application-form"><div class="form-grid" style="margin-top:20px"><label>API ID<input name="api_id" type="number" min="1" value="${state.application?.api_id || ""}" required></label><label>API Hash<input name="api_hash" type="password" autocomplete="off" placeholder="${state.application?.configured ? "已保存，留空保持不变" : "请输入应用凭据"}" ${state.application?.configured ? "" : "required"}><small>可在 <a href="https://my.telegram.org/apps" target="_blank" rel="noopener noreferrer">Telegram 官方开发者页面</a> 获取。</small></label></div><div class="form-error" role="alert"></div><button type="submit">保存连接配置</button></form></div></section>` +
    `<section class="panel"><div class="panel-head"><h2>运行配置</h2></div><div class="panel-body"><div class="setting-row"><div><strong>后台服务</strong><p>启动状态会保存，服务器重启后按保存的状态恢复。</p></div>${badge(state.running ? "active" : "disabled", state.running ? "运行中" : "已暂停")}</div><div class="setting-row"><div><strong>消息去重</strong><p>使用 Telegram 用户 ID 跨任务去重。结果不确定的发送不会自动重试。</p></div><span class="badge good">已启用</span></div><div class="setting-row"><div><strong>历史消息</strong><p>首次启动不扫描中转群历史，仅响应启动后的新消息。</p></div><span class="badge">仅新消息</span></div><div class="setting-row"><div><strong>服务器部署</strong><p>Linux · 单服务进程 · 本地持久化存储。部署步骤见项目 DEPLOYMENT.zh.md。</p></div><span class="badge">本地存储</span></div></div></section><section class="panel"><div class="panel-head"><h2>后台密码</h2></div><div class="panel-body"><form id="password-form"><div class="form-grid"><label>当前密码<input type="password" name="current_password" autocomplete="current-password" required></label><label>新密码<input type="password" name="new_password" autocomplete="new-password" minlength="12" required><small>至少 12 个字符。修改后请重新登录。</small></label></div><div class="form-error" role="alert"></div><button type="submit">更新密码</button></form></div></section>`
  );
}
async function render() {
  document.title = titles[page] + " · Telegram Ops";
  $("#crumb").textContent = titles[page];
  $$("[data-page]").forEach((a) =>
    a.classList.toggle("active", a.dataset.page === page),
  );
  $("#content").innerHTML = await (
    { overview, accounts, tasks, records, guards, settings }[page] || overview
  )();
  bindPage();
}
function accountDialog() {
  loginDialog(null, accountTab);
}
function loginDialog(id, role = accountTab) {
  const existing = state.accounts.find((a) => a.id === id);
  role = existing?.role || role;
  let accountId = id,
    requestedPhone = existing?.phone || "";
  openModal(
    (role === "monitor" ? "监测账号" : "私信账号") + "登录",
    `<form id="verify-form" class="simple-login"><label>手机号<input name="phone" type="tel" autocomplete="tel" placeholder="+国家区号手机号" value="${E(existing?.phone || "")}" ${existing ? "readonly" : ""} required pattern="\\+[1-9][0-9]{6,14}"></label><label>验证码<div class="code-input-row"><input name="code" inputmode="numeric" autocomplete="one-time-code" placeholder="请输入验证码" required><button type="button" id="request-code" class="secondary">获取验证码</button></div></label><label>二级登录密码<input name="password" type="password" autocomplete="off" placeholder="已开启两步验证时填写，未设置可留空"><small>即 Telegram 两步验证密码。</small></label><p id="code-result" class="muted" role="status"></p><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">登录</button></div></form>`,
  );
  $("#request-code").onclick = async () => {
    const button = $("#request-code"),
      phoneInput = $("[name=phone]"),
      phone = phoneInput.value.trim();
    if (!phoneInput.reportValidity()) return;
    button.disabled = true;
    try {
      if (!accountId || requestedPhone !== phone) {
        const found = state.accounts.find((a) => a.phone === phone);
        if (found) {
          if (found.role !== role)
            throw Error("该手机号已用于另一类账号，请使用独立手机号");
          if (found.has_session) throw Error("该账号已经登录");
          accountId = found.id;
        } else {
          const a = await api("/accounts", "POST", { phone, role });
          accountId = a.id;
          await load();
        }
        requestedPhone = phone;
      }
      const result = await api(`/accounts/${accountId}/send-code`, "POST");
      $("#code-result").textContent = result.message;
    } catch (e) {
      $("#code-result").textContent = e.message;
    } finally {
      button.disabled = false;
    }
  };
  setupForm("#verify-form", async (fd) => {
    if (!accountId || fd.get("phone").trim() !== requestedPhone)
      throw Error("请先为当前手机号获取验证码");
    await api(`/accounts/${accountId}/verify`, "POST", {
      code: fd.get("code"),
      password: fd.get("password"),
    });
    $("#modal").close();
    await load();
    toast(
      role === "monitor" ? "登录成功，请设置监测群组 ID" : "私信账号登录成功",
    );
    if (role === "monitor") monitorGroupsDialog(accountId);
  });
}
function monitorGroupsDialog(id) {
  const a = state.accounts.find((a) => a.id === id);
  openModal(
    "设置监测群组 ID",
    `<form id="monitor-groups-form"><p class="subtle">${E(a.name)} · ${E(a.phone)}</p><label style="margin-top:20px">监测群组 ID<textarea name="chat_ids" rows="6" placeholder="-1001234567890\n-1009876543210">${a.monitor_chat_ids.join("\n")}</textarea><small>每行一个完整群组 ID，或用逗号分隔。留空表示不监测任何来源群。</small></label><div class="callout" style="margin-top:20px">账号需要已经加入这些群组。新增 ID 后可在任务中选择；移除 ID 会立即停止对应来源，并暂停受影响的任务。</div><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">保存监测群组</button></div></form>`,
  );
  setupForm("#monitor-groups-form", async (fd) => {
    const parts = fd
      .get("chat_ids")
      .split(/[\s,，]+/)
      .filter(Boolean);
    if (
      parts.some(
        (v) => !/^-[1-9][0-9]*$/.test(v) || !Number.isSafeInteger(Number(v)),
      )
    )
      throw Error("请填写完整的负整数群组 ID，例如 -1001234567890");
    const result = await api(`/accounts/${id}/monitor-groups`, "PUT", {
      chat_ids: parts.map(Number),
    });
    $("#modal").close();
    await load();
    toast(result.message);
  });
}

function previewFields() {
  return `<div class="form-grid"><label>示例群名<input id="sample-title" value="示例来源群"></label><label>实际发言者用户名<input id="sample-username" value="example_user"></label><label class="span-2">测试发言<textarea id="sample-text" placeholder="粘贴一条群内发言，验证是否命中"></textarea></label></div><button class="secondary" type="button" id="run-preview" style="margin-top:15px">运行模拟测试</button><div id="preview-result" role="status"></div>`;
}
function taskDialog(id) {
  const t = state.tasks.find((t) => t.id === id) || {
    name: "",
    account_a: state.accounts.find((a) => a.role === "monitor")?.id,
    account_b: state.accounts.find((a) => a.role === "sender")?.id,
    source_chats: [],
    relay_chat: "",
    keywords: "",
    exclude_keywords: "",
    ignore_users: "",
    match_mode: "any",
    template: "",
  };
  if (
    !state.accounts.some((a) => a.role === "monitor") ||
    !state.accounts.some((a) => a.role === "sender")
  ) {
    toast("请先分别登录监测账号和私信账号");
    return;
  }
  const opts = (selected, role) =>
    state.accounts
      .filter((a) => a.role === role)
      .map(
        (a) =>
          `<option value="${a.id}" ${a.id === selected ? "selected" : ""}>${E(a.name)} · ${E(labels[a.status] || a.status)}</option>`,
      )
      .join("");
  openModal(
    id ? "编辑消息任务" : "新建消息任务",
    `<form id="task-form"><label>任务名称<input name="name" value="${E(t.name)}" placeholder="例如：产品咨询线索" required maxlength="120"></label><div class="form-section" style="margin-top:26px"><div class="section-title"><span>A</span>来源与筛选</div><div class="form-grid"><label>监听账号<select id="account-a" name="account_a">${opts(t.account_a, "monitor")}</select></label><label>匹配方式<select name="match_mode"><option value="any" ${t.match_mode === "any" ? "selected" : ""}>包含任意关键词</option><option value="all" ${t.match_mode === "all" ? "selected" : ""}>包含全部关键词</option><option value="exact" ${t.match_mode === "exact" ? "selected" : ""}>整条发言精确匹配</option></select></label><div class="span-2 field">来源群组<div id="source-options" class="checkbox-list"></div><small class="muted">这里只显示该监测账号配置的群组 ID；账号需要已加入群组。</small></div><label>包含关键词<textarea name="keywords" placeholder="每行一个关键词，或用逗号分隔" required>${E(t.keywords)}</textarea></label><label>排除关键词<textarea name="exclude_keywords" placeholder="命中任意排除词则跳过">${E(t.exclude_keywords)}</textarea></label><label class="span-2">忽略用户<input name="ignore_users" value="${E(t.ignore_users)}" placeholder="@用户名或用户 ID，用逗号分隔"></label></div></div><div class="form-section"><div class="section-title"><span>⇄</span>中转群组</div><label>选择 A、B 共同加入的群<select id="relay-chat" name="relay_chat" required></select></label><p class="preview-label">固定发送格式</p><div class="preview-box">群组-该用户发言的内容-@用户名</div></div><div class="form-section"><div class="section-title"><span>B</span>私信账号与文案</div><div class="form-grid"><label>私信账号<select id="account-b" name="account_b">${opts(t.account_b, "sender")}</select></label><div class="callout" style="margin:0">所选 B 负责接收指定 A 的消息；发送时按同一中转群的私信账号权重分配。正文其他 @提及不会作为收件人。</div><label class="span-2">私信文案<textarea name="template" placeholder="输入实际发送给用户的文案" required maxlength="3500">${E(t.template)}</textarea><small>可用 {{ username }} 插入目标 @用户名；其他内容按原文发送。</small></label></div></div><details class="simulation"><summary>模拟测试过滤与消息格式（不会发送）</summary>${previewFields()}</details><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">${id ? "保存更改并暂停" : "保存为暂停任务"}</button></div></form>`,
  );
  function groupOptions(initial = false) {
    const aid = Number($("#account-a").value),
      bid = Number($("#account-b").value);
    const source = (
      state.accounts.find((a) => a.id === aid)?.monitor_chat_ids || []
    ).map((id) => ({
      id,
      title:
        state.chats.find((c) => c.id === id && c.account_id === aid)?.title ||
        "指定群组",
    }));
    $("#source-options").innerHTML = source.length
      ? source
          .map(
            (c) =>
              `<label><input type="checkbox" name="source_chats" value="${c.id}" ${(initial && t.source_chats.includes(c.id)) || (!id && !initial) ? "checked" : ""}>${E(c.title)} <span class="muted">${c.id}</span></label>`,
          )
          .join("")
      : "<p>尚未配置监测群组 ID，请先在监测账号管理中设置。</p>";
    relayOptions(bid, aid, initial);
  }
  function relayOptions(bid, aid, initial = false) {
    const shared = state.chats.filter(
      (c) =>
        c.account_id === aid &&
        c.type !== "channel" &&
        state.chats.some(
          (b) => b.account_id === bid && b.id === c.id && b.type !== "channel",
        ),
    );
    const before = $("#relay-chat").value;
    $("#relay-chat").innerHTML =
      '<option value="">请选择中转群</option>' +
      shared
        .map(
          (c) =>
            `<option value="${c.id}" ${(initial ? t.relay_chat : Number(before)) === c.id ? "selected" : ""}>${E(c.title)}</option>`,
        )
        .join("");
  }
  groupOptions(true);
  $("#account-a").onchange = () => groupOptions();
  $("#account-b").onchange = () =>
    relayOptions(Number($("#account-b").value), Number($("#account-a").value));
  $("#run-preview").onclick = () => runPreview($("#task-form"));
  setupForm("#task-form", async (fd) => {
    const data = Object.fromEntries(fd);
    data.account_a = Number(data.account_a);
    data.account_b = Number(data.account_b);
    data.relay_chat = Number(data.relay_chat);
    data.source_chats = fd.getAll("source_chats").map(Number);
    await api(id ? `/tasks/${id}` : "/tasks", id ? "PUT" : "POST", data);
    $("#modal").close();
    await load();
    toast("任务已保存为暂停状态，请核对后启用");
  });
}
async function runPreview(form) {
  const b = $("#run-preview");
  b.disabled = true;
  try {
    const fd = new FormData(form);
    const r = await api("/preview", "POST", {
      text: $("#sample-text").value,
      title: $("#sample-title").value,
      username: $("#sample-username").value,
      keywords: fd.get("keywords") || "",
      exclude_keywords: fd.get("exclude_keywords") || "",
      ignore_users: fd.get("ignore_users") || "",
      match_mode: fd.get("match_mode") || "any",
      template: fd.get("template") || "",
    });
    $("#preview-result").innerHTML =
      `<p class="preview-label">模拟结果 · ${E(r.reason)}</p>${r.matched ? `<p class="preview-label">A 发送到中转群</p><div class="preview-box">${E(r.relay)}</div><p class="preview-label">B 私信内容</p><div class="preview-box">${E(r.dm || "尚未设置文案")}</div>` : ""}`;
  } catch (e) {
    $("#preview-result").textContent = e.message;
  } finally {
    b.disabled = false;
  }
}
function previewDialog() {
  openModal(
    "模拟测试 · 不会发送消息",
    `<form id="preview-form"><div class="form-grid"><label>包含关键词<textarea name="keywords" placeholder="例如：咨询，服务"></textarea></label><label>排除关键词<textarea name="exclude_keywords" placeholder="例如：广告"></textarea></label><label class="span-2">私信文案<textarea name="template" placeholder="填写文案，可使用 {{ username }}"></textarea></label></div>${previewFields()}</form>`,
  );
  $("#preview-form").onsubmit = (e) => e.preventDefault();
  $("#run-preview").onclick = () => runPreview($("#preview-form"));
}
function guardDialog(userId = "") {
  openModal(
    "添加不再联系的用户",
    `<form id="guard-form"><div class="form-grid"><label>Telegram 用户 ID<input name="user_id" type="number" min="1" value="${E(userId)}" required></label><label>备注<input name="note" placeholder="例如：用户要求不再联系" maxlength="500"></label></div><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">加入名单</button></div></form>`,
  );
  setupForm("#guard-form", async (fd) => {
    await api("/guards", "POST", {
      user_id: Number(fd.get("user_id")),
      note: fd.get("note"),
    });
    $("#modal").close();
    toast("已加入不再联系名单");
    await load();
  });
}
function weightDialog(id) {
  const a = state.accounts.find((a) => a.id === id);
  openModal(
    "设置轮询权重",
    `<form id="weight-form"><p class="subtle">${E(a.name)} · ${E(a.phone)}</p><label>轮询权重<input name="weight" type="number" min="1" max="1000" step="1" required value="${a.rotation_weight ?? 1}"></label><p class="muted">范围 1–1000，数字越大，分配比例越高。权重 3 和 1 的账号长期约按 3∶1 分配。同一中转群中已登录、已同步群组且允许发送的私信账号参与轮询。已分配消息不受修改影响。</p><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">保存权重</button></div></form>`,
  );
  setupForm("#weight-form", async (fd) => {
    await api(`/accounts/${id}/weight`, "PUT", {
      weight: Number(fd.get("weight")),
    });
    $("#modal").close();
    await load();
    toast("轮询权重已保存");
  });
}
function bindPage() {
  $$("[data-weight]").forEach(
    (b) => (b.onclick = () => weightDialog(Number(b.dataset.weight))),
  );
  $$("[data-account-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        accountTab = b.dataset.accountTab;
        render();
      }),
  );
  $$("[data-monitor-groups]").forEach(
    (b) =>
      (b.onclick = () => monitorGroupsDialog(Number(b.dataset.monitorGroups))),
  );
  if ($("#application-form"))
    setupForm("#application-form", async (fd) => {
      await api("/application", "PUT", {
        api_id: Number(fd.get("api_id")),
        api_hash: fd.get("api_hash"),
      });
      await load();
      toast("Telegram 连接配置已保存");
    });
  for (const id of ["add-account", "empty-add-account"])
    if ($("#" + id)) $("#" + id).onclick = accountDialog;
  $$("[data-new-task]").forEach((b) => (b.onclick = () => taskDialog()));
  $$("[data-edit-task]").forEach(
    (b) => (b.onclick = () => taskDialog(Number(b.dataset.editTask))),
  );
  $$("[data-toggle-task]").forEach(
    (b) =>
      (b.onclick = () =>
        action(
          b,
          () => api(`/tasks/${b.dataset.toggleTask}/toggle`, "POST"),
          "任务状态已更新",
        )),
  );
  $$("[data-login]").forEach(
    (b) => (b.onclick = () => loginDialog(Number(b.dataset.login))),
  );
  $$("[data-sync]").forEach(
    (b) =>
      (b.onclick = () =>
        action(
          b,
          () => api(`/accounts/${b.dataset.sync}/sync`, "POST"),
          "群组已同步",
        )),
  );
  $$("[data-logout-account]").forEach(
    (b) =>
      (b.onclick = () => {
        if (confirm("退出该 Telegram 账号会注销服务器登录会话。继续？"))
          action(
            b,
            () => api(`/accounts/${b.dataset.logoutAccount}/logout`, "POST"),
            "账号已退出",
          );
      }),
  );
  $$("[data-permission]").forEach(
    (c) =>
      (c.onchange = () => {
        const a = state.accounts.find(
          (a) => a.id === Number(c.dataset.permission),
        );
        action(
          c,
          () =>
            api(`/accounts/${a.id}/permissions`, "POST", {
              send_enabled: a.send_enabled,
              private_message_enabled: a.private_message_enabled,
              [c.dataset.kind]: c.checked,
            }),
          "账号权限已更新",
        );
      }),
  );
  if ($("#standalone-preview"))
    $("#standalone-preview").onclick = previewDialog;
  if ($("#search-records"))
    $("#search-records").onclick = async () => {
      recordSearch = $("#record-search").value;
      recordStatus = $("#record-status").value;
      recordOffset = 0;
      await render();
    };
  if ($("#refresh-records")) $("#refresh-records").onclick = () => load();
  if ($("#prev-page"))
    $("#prev-page").onclick = async () => {
      recordOffset = Math.max(0, recordOffset - 50);
      await render();
    };
  if ($("#next-page"))
    $("#next-page").onclick = async () => {
      recordOffset += 50;
      await render();
    };
  $$("[data-cancel-job]").forEach(
    (b) =>
      (b.onclick = () =>
        action(
          b,
          () => api(`/records/${b.dataset.cancelJob}/cancel`, "POST"),
          "待发送消息已取消",
        )),
  );
  $$("[data-block-user]").forEach(
    (b) => (b.onclick = () => guardDialog(b.dataset.blockUser)),
  );
  if ($("#new-guard")) $("#new-guard").onclick = () => guardDialog();
  $$("[data-remove-guard]").forEach(
    (b) =>
      (b.onclick = () => {
        if (confirm("确定将此用户移出不再联系名单？"))
          action(
            b,
            () => api(`/guards/${b.dataset.removeGuard}`, "DELETE"),
            "已移出名单",
          );
      }),
  );
  if ($("#password-form"))
    setupForm("#password-form", async (fd) => {
      await api("/password", "POST", Object.fromEntries(fd));
      location.href = "/admin/logout";
    });
}
async function action(button, fn, message) {
  button.disabled = true;
  try {
    const r = await fn();
    toast(r?.message || message);
    await load();
  } catch (e) {
    toast(e.message);
    button.disabled = false;
    if (button.type === "checkbox") button.checked = !button.checked;
  }
}
load().catch((e) => {
  $("#content").innerHTML = empty(
    "暂时无法加载工作空间",
    E(e.message),
    '<button onclick="location.reload()">重新加载</button>',
  );
});
setInterval(async () => {
  if (document.hidden || $("#modal").open) return;
  try {
    state = await api("/state");
    serviceHeader();
    if (page === "overview") await render();
  } catch {}
}, 15000);
