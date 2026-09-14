/**
 * MKAY Radar's always-on Telegram webhook.
 * Deploy to Cloudflare Workers. All secrets are Worker secrets, not source code.
 */

const TELEGRAM_API = "https://api.telegram.org/bot";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("MKAY Radar webhook is online.", { status: 200 });
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    try {
      await handleUpdate(await request.json(), env);
    } catch (error) {
      // Telegram retries non-200 responses. Log the error but acknowledge the update
      // to avoid duplicate broadcasts; failed calls remain visible in Worker logs.
      console.error("Telegram update failed", error);
    }
    return new Response("OK");
  },
};

async function handleUpdate(update, env) {
  if (update.callback_query) return handleCallback(update.callback_query, env);
  const message = update.message || update.channel_post;
  if (message?.text?.startsWith("/enable_broadcast")) return enableBroadcast(message, env);
}

async function handleCallback(callback, env) {
  const [action, opportunityId] = String(callback.data || "").split(":", 2);
  const status = action === "approve" ? "APPROVED" : action === "reject" ? "REJECTED" : null;
  if (!status || !opportunityId) return;

  await supabase(env, `opportunities?id=eq.${encodeURIComponent(opportunityId)}`, {
    method: "PATCH", body: JSON.stringify({ status }),
  });
  const rows = await supabase(env, `opportunities?id=eq.${encodeURIComponent(opportunityId)}&select=*`);
  const opportunity = rows[0];
  let sent = 0;
  if (status === "APPROVED" && opportunity) {
    const chats = await supabase(env, "telegram_chats?is_active=is.true&select=*");
    for (const chat of chats.slice(0, 45)) { // Leaves room under the Free plan's per-request subrequest limit.
      await telegram(env, "sendMessage", {
        chat_id: chat.chat_id,
        text: formatAlpha(opportunity),
        parse_mode: "HTML",
        disable_web_page_preview: true,
      });
      sent += 1;
    }
  }

  await telegram(env, "answerCallbackQuery", {
    callback_query_id: callback.id,
    text: status === "APPROVED" ? `Approved — sent to ${sent} group(s).` : "Rejected.",
  });
  await telegram(env, "editMessageReplyMarkup", {
    chat_id: callback.message.chat.id,
    message_id: callback.message.message_id,
    reply_markup: { inline_keyboard: [] },
  });
}

async function enableBroadcast(message, env) {
  const chat = message.chat;
  if (!["group", "supergroup", "channel"].includes(chat.type)) return;
  const senderId = message.from?.id;
  const me = await telegram(env, "getMe", {});
  const sender = senderId ? await telegram(env, "getChatMember", { chat_id: chat.id, user_id: senderId }) : null;
  const bot = await telegram(env, "getChatMember", { chat_id: chat.id, user_id: me.id });
  const admin = new Set(["administrator", "creator", "owner"]);

  if (!sender || !admin.has(sender.status)) {
    return telegram(env, "sendMessage", { chat_id: chat.id, text: "Only a group administrator can enable broadcasts." });
  }
  if (!admin.has(bot.status)) {
    return telegram(env, "sendMessage", { chat_id: chat.id, text: "Make me a group administrator first, then try again." });
  }
  await supabase(env, "telegram_chats?on_conflict=chat_id", {
    method: "POST",
    headers: { Prefer: "resolution=merge-duplicates" },
    body: JSON.stringify({ chat_id: chat.id, title: chat.title || null, chat_type: chat.type, is_active: true }),
  });
  return telegram(env, "sendMessage", { chat_id: chat.id, text: "✅ This group is registered for approved MKAY Alpha posts." });
}

async function telegram(env, method, body) {
  const response = await fetch(`${TELEGRAM_API}${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(`Telegram ${method} failed: ${data.description || response.status}`);
  return data.result;
}

async function supabase(env, path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("apikey", env.SUPABASE_SERVICE_ROLE_KEY);
  headers.set("Authorization", `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`);
  headers.set("content-type", "application/json");
  const response = await fetch(`${env.SUPABASE_URL}/rest/v1/${path}`, { ...options, headers });
  if (!response.ok) throw new Error(`Supabase failed: ${response.status} ${await response.text()}`);
  const text = await response.text();
  return text ? JSON.parse(text) : [];
}

function formatAlpha(item) {
  const actions = actionBullets(item.requirements);
  const lines = [
    "<b>🚀 MKAY Alpha</b>",
    "",
    `<b>${escapeHtml(item.title)}</b> is an active ${escapeHtml(String(item.category).toLowerCase())} opportunity.`,
    "",
    "<b>What to do:</b>",
    ...actions.map((action) => `• ${escapeHtml(action)}`),
  ];
  for (const [label, value] of [["Chain", item.chain], ["Reward", item.reward], ["Status", item.source_status]]) {
    if (value) lines.push(`<b>${label}:</b> ${escapeHtml(String(value).slice(0, 160))}`);
  }
  lines.push("", `🔗 <a href=\"${escapeAttribute(item.url)}\">Open the official guide</a>`, "", "<i>DYOR. Never share your seed phrase.</i>");
  return lines.join("\n").slice(0, 4000);
}

function actionBullets(requirements) {
  if (!requirements) return ["Review the official guide and confirm your eligibility."];
  const values = String(requirements).split(/[,;\n]/).map((value) => value.trim().replace(/[. ]+$/, "")).filter(Boolean);
  return values.slice(0, 3).length ? values.slice(0, 3) : ["Review the official guide and confirm your eligibility."];
}

function escapeHtml(value) { return String(value).replace(/[&<>]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[char]); }
function escapeAttribute(value) { return escapeHtml(value).replace(/"/g, "&quot;"); }
