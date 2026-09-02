/* T 系统前端公共工具 */
const API = {
  token: localStorage.getItem("t_token") || "",
  user: JSON.parse(localStorage.getItem("t_user") || "null"),
  setSession(user) {
    this.token = user.token; this.user = user;
    localStorage.setItem("t_token", user.token);
    localStorage.setItem("t_user", JSON.stringify(user));
  },
  clearSession() { this.token = ""; this.user = null; localStorage.removeItem("t_token"); localStorage.removeItem("t_user"); },
  async fetch(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (this.token) headers["Authorization"] = "Bearer " + this.token;
    const resp = await fetch(path, { ...options, headers });
    let data = {};
    try { data = await resp.json(); } catch (e) { /* no body */ }
    if (!resp.ok) {
      const err = new Error(data.message || data.detail || ("HTTP " + resp.status));
      err.status = resp.status; err.data = data;
      throw err;
    }
    return data;
  },
};

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const OP_LABELS = {
  manual_modify: "人工修改", rule_override: "规则覆盖", rule_toggle: "规则启停",
  once_modify: "单次修改", rule_delete: "规则删除", memory_record: "记忆记入", rule_create: "规则新建",
};

const SOURCE_BADGE = {
  memory: '<span class="badge memory">由记忆自动修改</span>',
  human: '<span class="badge human">人工修改</span>',
  zhimou: '<span class="badge zhimou">智眸原值</span>',
};

function sourceBadge(source) { return SOURCE_BADGE[source] || esc(source); }

function ruleTypeBadge(t) {
  return t === "once" ? '<span class="badge once">单次</span>' : '<span class="badge on">长期</span>';
}

function statusBadge(s) {
  const map = { enabled: ["on", "启用"], disabled: ["off", "停用"],
    created: ["info", "已建单"], synced: ["on", "已回写"], sync_failed: ["err", "回写失败"],
    create_failed: ["err", "建单失败"], pending_create: ["off", "待建单"], draft: ["info", "草稿"], submitted: ["on", "已提交"] };
  const m = map[s] || ["off", s];
  const cls = m[0] === "info" ? "zhimou" : (m[0] === "err" ? "memory" : m[0]);
  return `<span class="badge ${cls}">${esc(m[1])}</span>`;
}

function msgHtml(kind, text) { return `<div class="msg ${kind}">${esc(text)}</div>`; }
