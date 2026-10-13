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
  let result = { sent: 0, failed: 0 };
  if (status === "APPROVED" && opportunity) result = await broadcastAlpha(env, opportunity);

  // The review message must always be answered and cleaned up, whatever happened while
  // broadcasting, so a bad group can never leave the Approve button looking dead.
  const failedNote = result.failed ? `, ${result.failed} failed` : "";
  await bestEffort("answerCallbackQuery", () => telegram(env, "answerCallbackQuery", {
    callback_query_id: callback.id,
    text: status === "APPROVED" ? `Approved — sent to ${result.sent} group(s)${failedNote}.` : "Rejected.",
  }));
  await bestEffort("editMessageReplyMarkup", () => telegram(env, "editMessageReplyMarkup", {
    chat_id: callback.message.chat.id,
    message_id: callback.message.message_id,
    reply_markup: { inline_keyboard: [] },
  }));
}

// Telegram errors that mean the bot can no longer post there (group deleted, bot removed).
// Rights problems are deliberately excluded: an admin can fix those without re-registering.
const UNREACHABLE_CHAT = /chat not found|bot was kicked|bot is not a member|chat was deleted|bot was blocked/i;

async function broadcastAlpha(env, opportunity) {
  const chats = await supabase(env, "telegram_chats?is_active=is.true&select=*");
  const text = formatAlpha(opportunity);
  const unreachable = [];
  let sent = 0;
  let failed = 0;
  // Capped to stay under the Free plan's 50 subrequests per invocation:
  // 5 fixed calls + up to 40 sends + 1 batched deactivation.
  for (const chat of chats.slice(0, 40)) {
    try {
      await telegram(env, "sendMessage", {
        chat_id: chat.chat_id, text, parse_mode: "HTML", disable_web_page_preview: true,
      });
      sent += 1;
    } catch (error) {
      failed += 1;
      console.error(`Broadcast to ${chat.chat_id} failed`, error);
      if (UNREACHABLE_CHAT.test(String(error.message))) unreachable.push(chat.chat_id);
    }
  }
  if (unreachable.length) {
    // One batched call. A group that is re-added later is re-activated by /enable_broadcast.
    await bestEffort("deactivate unreachable chats", () => supabase(
      env, `telegram_chats?chat_id=in.(${unreachable.join(",")})`,
      { method: "PATCH", body: JSON.stringify({ is_active: false }) },
    ));
  }
  return { sent, failed };
}

async function bestEffort(label, action) {
  try { return await action(); } catch (error) { console.error(`${label} failed`, error); }
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
