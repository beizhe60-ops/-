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
            `<div class="task-row"><div><div class="actions"><span class="task-name">${E(t.name)}</span>${badge(t.enabled ? "active" : "disabled", t.enabled ? "已启用" : "已暂停")}</div><div class="task-flow">${E(accountName(t.account_a))} · ${t.source_chats.map((g) => E(g)).join("、")}　→　${E(t.relay_chat)}${chatName(t.relay_chat) !== String(t.relay_chat) ? "（" + E(chatName(t.relay_chat)) + "）" : ""} · ${state.accounts.filter((a) => a.role === "sender" && (a.receive_chat_ids || []).includes(t.relay_chat)).length} 个私信账号已绑定</div><div class="chips">${t.keywords
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
        "绑定监测群 ID 与转发目标群 ID，再设置关键词。监测转发可独立运行。",
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
      )}</div><div class="columns"><section class="panel"><div class="panel-head"><h2>消息任务</h2><a href="/console/tasks">管理全部</a></div>${tasksList()}</section><section class="panel"><div class="panel-head"><h2>开始使用</h2><span class="subtle">${[state.accounts.some((a) => a.role === "monitor" && a.has_session), state.tasks.length > 0, state.tasks.some((t) => t.enabled)].filter(Boolean).length} / 3</span></div><div class="panel-body"><ol class="checklist">${[
      [
        "登录监测账号",
        "需要自动私信时，再登录私信账号。",
        state.accounts.some((a) => a.role === "monitor" && a.has_session),
        "accounts",
      ],
      [
        "绑定功能群组 ID",
        "监测转发与私信接收分别绑定。",
        state.tasks.length > 0,
        "accounts",
      ],
      [
        "配置并启用任务",
        "核对群组 ID 和关键词，再启动服务。",
        state.tasks.some((t) => t.enabled),
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
    <div class="account-section-head"><div><h2>${roleName}管理</h2><p class="subtle">${isMonitor ? "绑定监测群组 ID 和转发目标 ID，设置关键词后启用。" : "绑定接收群组 ID 与文案，同一群内按权重分配私信。"}</p></div><button id="add-account">＋ ${roleName}登录</button></div>
    ${!state.application?.configured ? '<div class="callout">首次使用请先在 <a href="/console/settings">系统设置</a> 完成 Telegram 连接配置，之后登录账号只需手机号、验证码和二级登录密码。</div>' : ""}` +
    (items.length
      ? `<div class="account-grid">${items
          .map(
            (
              a,
            ) => `<article class="account-card"><div class="account-top"><div><h3>${E(a.name)}</h3><span class="phone">${E(a.phone)}</span></div>${badge(a.connected ? "active" : a.status, a.connected ? "● 已连接" : undefined)}</div>
    ${
      isMonitor
        ? `<div class="monitor-summary"><strong>监测群组 → 转发群组</strong>${
            state.tasks
              .filter((t) => t.account_a === a.id)
              .map(
                (t) =>
                  `<div class="group-binding-row"><div class="chips">${t.source_chats.map((g) => `<span class="chip">${g}</span>`).join("")}</div><p>→ <strong>${t.relay_chat}</strong></p><div class="actions">${badge(t.enabled ? "active" : "disabled", t.enabled ? "已启用" : "已暂停")}<button class="secondary small" data-edit-task="${t.id}">编辑绑定</button><button class="secondary small" data-toggle-task="${t.id}">${t.enabled ? "暂停任务" : "启用任务"}</button></div></div>`,
              )
              .join("") || '<p class="muted">尚未绑定，不会监测转发。</p>'
          }<button class="secondary small" data-monitor-groups="${a.id}">绑定监测与转发群组</button></div>`
        : `<div class="monitor-summary"><strong>私信接收群组 ID</strong><div class="chips">${(a.receive_chat_ids || []).map((g) => `<span class="chip">${g}</span>`).join("") || '<p class="muted">尚未绑定，不接收消息、不参与轮询。</p>'}</div><button class="secondary small" data-receive-groups="${a.id}">绑定接收群组</button></div>`
    }
    ${!isMonitor ? `<div class="monitor-summary"><strong>轮询权重 <span class="badge">${a.rotation_weight ?? 1}</span></strong><p class="muted">数字越大，分配的私信越多。默认 1。</p><button class="secondary small" data-weight="${a.id}">设置轮询权重</button></div>` : ""}
    <div class="permission-row">${isMonitor ? "允许转发消息" : "允许发送私信"}<input aria-label="${E(a.name)} 允许发送" class="toggle" type="checkbox" data-permission="${a.id}" data-kind="send_enabled" ${a.send_enabled ? "checked" : ""}></div>
    ${a.last_error ? `<p class="error-text">${E(a.last_error)}</p>` : ""}<div class="actions">${!a.has_session ? `<button class="small" data-login="${a.id}">继续登录</button>` : `<button class="secondary small" data-sync="${a.id}">同步群名称（可选）</button><button class="text-button small" data-logout-account="${a.id}">退出账号</button>`}</div></article>`,
          )
          .join("")}</div>`
      : `<div class="panel">${empty("还没有" + roleName, "使用手机号、验证码和二级登录密码完成登录。", `<button id="empty-add-account">${roleName}登录</button>`)}</div>`)
  );
}

function tasks() {
  return (
    heading(
      "任务与过滤",
      "每条绑定指定监测来源群 ID、转发目标群 ID 与过滤规则。私信账号另行绑定接收群 ID。",
      '<div class="actions"><button class="secondary" id="standalone-preview">模拟测试</button><button data-new-task>＋ 新建任务</button></div>',
    ) +
    flow() +
    `<section class="panel"><div class="panel-head"><h2>全部任务</h2><span class="muted">${state.tasks.length} 条任务</span></div>${tasksList()}</section><div class="callout">转发格式固定为“群组-该用户发言的内容-@用户名”。私信账号只处理已绑定群组内、本系统监测账号发送的标准消息。编辑任务会暂停任务并取消旧的待发送内容，保存后需重新启用。</div>`
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
    `<section class="panel"><div class="panel-head"><div class="filters"><input id="record-search" aria-label="按用户名搜索" placeholder="搜索 @用户名" value="${E(recordSearch)}"><select id="record-status" aria-label="发送状态"><option value="">全部状态</option>${["pending", "waiting", "sending", "sent", "failed", "unknown", "skipped", "cancelled"].map((s) => `<option value="${s}" ${recordStatus === s ? "selected" : ""}>${labels[s]}</option>`).join("")}</select><button class="secondary small" id="search-records">筛选</button></div><span class="muted">${r.total} 条记录</span></div>${r.items.length ? `<div class="table-wrap"><table><thead><tr><th>阶段 / 时间</th><th>用户 / 来源</th><th>发送内容</th><th>状态</th><th>操作</th></tr></thead><tbody>${r.items.map((j) => `<tr><td>${j.stage === "relay" ? "A → 中转群" : "B → 私信"}<small>${E(date(j.created_at))}</small><small>任务 #${j.task_id}</small><small>${j.stage === "relay" ? "监测" : "接收"}群 ID ${E(j.chat_id)}</small></td><td>@${E(j.username)}<small>${E(j.source_title)}</small>${j.user_id ? `<small>ID ${j.user_id}</small>` : ""}</td><td><div class="record-text">${E(j.text)}</div><details><summary>原始发言</summary><div class="record-text">${E(j.original_text)}</div></details></td><td>${badge(j.status)}${j.error ? `<small class="error-text">${E(j.error)}</small>` : ""}${j.status === "waiting" ? `<small>${E(date(j.due_at))}</small>` : ""}</td><td><div class="actions">${["pending", "waiting"].includes(j.status) ? `<button class="secondary small" data-cancel-job="${j.id}">取消</button>` : ""}${j.user_id ? `<button class="text-button small" data-block-user="${j.user_id}">不再联系</button>` : ""}</div></td></tr>`).join("")}</tbody></table></div>` : empty("没有符合条件的记录", "任务运行后，关键词命中和自动私信结果会显示在这里。")}<div class="pagination"><span>每页 50 条</span><div class="actions"><button class="secondary small" id="prev-page" ${recordOffset === 0 ? "disabled" : ""}>上一页</button><button class="secondary small" id="next-page" ${recordOffset + 50 >= r.total ? "disabled" : ""}>下一页</button></div></div></section>`
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
    else receiveGroupsDialog(accountId);
  });
}
function monitorGroupsDialog(id) {
  taskDialog(null, id);
}
function previewFields() {
  return `<div class="form-grid"><label>示例群名<input id="sample-title" value="示例来源群"></label><label>实际发言者用户名<input id="sample-username" value="example_user"></label><label class="span-2">测试发言<textarea id="sample-text" placeholder="粘贴一条群内发言，验证是否命中"></textarea></label></div><button class="secondary" type="button" id="run-preview" style="margin-top:15px">运行模拟测试</button><div id="preview-result" role="status"></div>`;
}
function parseGroupIds(value, allowEmpty = false) {
  const parts = value.split(/[\s,，]+/).filter(Boolean);
  if (
    (!allowEmpty && !parts.length) ||
    parts.some(
      (v) =>
        !/^-[1-9][0-9]*$/.test(v) ||
        !Number.isSafeInteger(Number(v)) ||
        Number(v) < -(2 ** 52),
    )
  )
    throw Error(
      "请填写完整的负整数群组 ID，例如 -1001234567890；多个 ID 用换行或逗号分隔",
    );
  return [...new Set(parts.map(Number))];
}
function receiveGroupsDialog(id) {
  const a = state.accounts.find((a) => a.id === id);
  openModal(
    "绑定私信接收群组",
    `<form id="receive-groups-form"><p class="subtle">${E(a.name)} · ${E(a.phone)}</p><label>接收群组 ID<textarea name="chat_ids" rows="4" placeholder="-1001234567890">${E((a.receive_chat_ids || []).join("\n"))}</textarea><small>填写监测账号的转发目标群 ID，每行一个；留空则停止接收。账号需要已加入这些群组。</small></label><label>私信文案<textarea name="template" rows="4" maxlength="3500" placeholder="您好 {{ username }}，这是您咨询的资料。">${E(a.dm_template || "")}</textarea><small>可用 {{ username }} 插入用户名。旧任务如有独立文案，则优先使用任务文案。</small></label><div class="callout">只接收绑定 ID 内、本系统监测账号转发的标准消息。同群内已绑定的私信账号按权重分配。移除 ID 或修改文案会取消受影响的待发送消息。</div><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">保存接收绑定</button></div></form>`,
  );
  setupForm("#receive-groups-form", async (fd) => {
    const chat_ids = parseGroupIds(fd.get("chat_ids"), true),
      template = fd.get("template").trim();
    if (chat_ids.length && !template) throw Error("请填写私信文案");
    await api(`/accounts/${id}/receive-groups`, "PUT", { chat_ids, template });
    $("#modal").close();
    await load();
    toast("接收群组与私信文案已保存");
  });
}
function taskDialog(id, monitorId) {
  const t = state.tasks.find((t) => t.id === id) || {
    name: "",
    account_a:
      monitorId || state.accounts.find((a) => a.role === "monitor")?.id,
    source_chats: [],
    relay_chat: "",
    keywords: "",
    exclude_keywords: "",
    ignore_users: "",
    match_mode: "any",
    template: "",
  };
  if (!t.account_a) {
    toast("请先添加监测账号");
    return;
  }
  const options = state.accounts
    .filter((a) => a.role === "monitor")
    .map(
      (a) =>
        `<option value="${a.id}" ${a.id === t.account_a ? "selected" : ""}>${E(a.name)} · ${E(a.phone)}</option>`,
    )
    .join("");
  openModal(
    id ? "编辑监测与转发绑定" : "绑定监测与转发群组",
    `<form id="task-form">
    <label>绑定名称<input name="name" value="${E(t.name)}" required maxlength="120" placeholder="例如：产品咨询监测"></label>
    <div class="form-grid" style="margin-top:20px"><label class="span-2">监测账号<select name="account_a">${options}</select></label>
    <label>监测群组 ID<textarea name="source_chats" rows="4" required placeholder="-1001234567890\n-1009876543210">${E(t.source_chats.join("\n"))}</textarea><small>每行一个完整 ID；只有这些来源群进入筛选。</small></label>
    <label>转发目标群组 ID<input name="relay_chat" value="${E(t.relay_chat)}" required placeholder="-1001122334455"><small>本条绑定的消息只发送到这个 ID。不同目标可建立多条绑定。</small></label></div>
    <div class="callout">账号需已加入来源群和目标群，并能在目标群发消息。不需要同步群组列表，也不需要先配置私信账号。</div>
    <div class="form-section"><div class="section-title">消息过滤</div><div class="form-grid"><label>匹配方式<select name="match_mode"><option value="any" ${t.match_mode === "any" ? "selected" : ""}>包含任意关键词</option><option value="all" ${t.match_mode === "all" ? "selected" : ""}>包含全部关键词</option><option value="exact" ${t.match_mode === "exact" ? "selected" : ""}>整条发言精确匹配</option></select></label><label>忽略用户<input name="ignore_users" value="${E(t.ignore_users)}" placeholder="@用户名或用户 ID，用逗号分隔"></label><label>包含关键词<textarea name="keywords" required>${E(t.keywords)}</textarea></label><label>排除关键词<textarea name="exclude_keywords">${E(t.exclude_keywords)}</textarea></label></div></div>
    <p class="preview-label">固定转发格式</p><div class="preview-box">群组-该用户发言的内容-@用户名</div>
    ${t.template ? `<details class="simulation"><summary>原任务私信文案（兼容已有配置）</summary><label>任务独立文案<textarea name="template" maxlength="3500">${E(t.template)}</textarea><small>清空后使用私信账号设置的文案。</small></label></details>` : ""}
    <details class="simulation"><summary>模拟测试过滤与消息格式（不会发送）</summary>${previewFields()}</details><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">${id ? "保存更改并暂停" : "保存绑定（暂停）"}</button></div></form>`,
  );
  $("#run-preview").onclick = () => runPreview($("#task-form"));
  setupForm("#task-form", async (fd) => {
    const data = Object.fromEntries(fd);
    data.account_a = Number(fd.get("account_a"));
    data.source_chats = parseGroupIds(fd.get("source_chats"));
    const target = parseGroupIds(fd.get("relay_chat"));
    if (target.length !== 1) throw Error("每条绑定只能设置一个转发目标群 ID");
    data.relay_chat = target[0];
    if (data.source_chats.includes(data.relay_chat))
      throw Error("转发群不能同时作为监测来源群");
    data.template = fd.get("template") || "";
    await api(id ? `/tasks/${id}` : "/tasks", id ? "PUT" : "POST", data);
    $("#modal").close();
    await load();
    toast("群组绑定已保存为暂停状态，请核对后启用");
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
    `<form id="weight-form"><p class="subtle">${E(a.name)} · ${E(a.phone)}</p><label>轮询权重<input name="weight" type="number" min="1" max="1000" step="1" required value="${a.rotation_weight ?? 1}"></label><p class="muted">范围 1–1000，数字越大，分配比例越高。权重 3 和 1 的账号长期约按 3∶1 分配。同一接收群 ID 下已显式绑定、在线且允许发送的私信账号参与轮询。已分配消息不受修改影响。</p><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">保存权重</button></div></form>`,
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
  $$("[data-receive-groups]").forEach(
    (b) =>
      (b.onclick = () => receiveGroupsDialog(Number(b.dataset.receiveGroups))),
  );
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
