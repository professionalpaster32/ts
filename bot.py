import os
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from uuid import uuid4

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
    ChatMemberHandler
)
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

user_chats: Dict[int, List[Dict]] = {}
user_instructions: Dict[int, str] = {}
user_models: Dict[int, str] = {}
incognito_mode: Dict[int, bool] = {}
group_admin_mode: Dict[int, bool] = {}
group_warnings: Dict[int, Dict[int, List[str]]] = {}
group_roles: Dict[int, Dict[int, str]] = {}
group_automode: Dict[int, str] = {}
group_welcome_msg: Dict[int, str] = {}
group_leaving_msg: Dict[int, str] = {}
active_games: Dict[str, Dict] = {}

DEFAULT_INSTRUCTIONS = "You are a helpful assistant."
DEFAULT_MODEL = "gemini-2.5-flash"
ALLOWED_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro"]

async def _is_admin(chat_id: int, user_id: int, bot) -> bool:
    if chat_id > 0:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except:
        return False

async def _resolve_user(chat_id: int, target: str, bot):
    if target.startswith("@"):
        try:
            user = await bot.get_chat_member(chat_id, target)
            return user.user.id
        except:
            await bot.send_message(chat_id, "User not found.")
            return None
    else:
        try:
            return int(target)
        except ValueError:
            await bot.send_message(chat_id, "Invalid user ID or username.")
            return None

def parse_duration(duration_str: str) -> Optional[timedelta]:
    match = re.match(r"(\d+)\s*(day|week|month|minute|hour)s?", duration_str.lower())
    if not match:
        return None
    amount, unit = match.groups()
    amount = int(amount)
    if "day" in unit:
        return timedelta(days=amount)
    elif "week" in unit:
        return timedelta(weeks=amount)
    elif "month" in unit:
        return timedelta(days=30 * amount)
    elif "hour" in unit:
        return timedelta(hours=amount)
    elif "minute" in unit:
        return timedelta(minutes=amount)
    return None

async def send_gemini_response(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    user_id = update.effective_user.id
    model_name = user_models.get(user_id, DEFAULT_MODEL)
    instructions = user_instructions.get(user_id, DEFAULT_INSTRUCTIONS)
    full_prompt = f"{instructions}\n\nUser: {prompt}"
    model = genai.GenerativeModel(model_name)
    try:
        if incognito_mode.get(user_id, False):
            response = await model.generate_content_async(full_prompt)
        else:
            chat_history = user_chats.get(user_id, [])
            chat = model.start_chat(history=chat_history)
            response = await chat.send_message_async(full_prompt)
            user_chats[user_id] = chat.history
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        await update.message.reply_text("❌ AI error. Try again.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "hello, how can i assist you?\n"
        "/help — shows all commands\n"
        "/newchat — clear memory\n"
        "/chathistory — view recent messages\n"
        "/addtogroup — authorize in groups\n"
        "/instructions — set custom behavior\n"
        "/switchmodel — choose AI model"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def addtogroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0:
        await update.message.reply_text("👥 Use this command in a group.")
        return
    keyboard = [[InlineKeyboardButton("✅ Authorize Bot", callback_data=f"auth_{chat_id}")]]
    await update.message.reply_text(
        "🔐 Only admins can enable bot features.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_chats[update.effective_user.id] = []
    await update.message.reply_text("🆕 Chat history cleared.")

async def chathistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = user_chats.get(update.effective_user.id, [])
    if not history:
        await update.message.reply_text("📜 No chat history.")
        return
    messages = []
    for msg in history[-10:]:
        role = "You" if msg["role"] == "user" else "Gemini"
        content = msg["parts"][0] if isinstance(msg["parts"], list) else str(msg["parts"])
        messages.append(f"{role}: {content}")
    await update.message.reply_text(f"📜 Recent Chat:\n\n" + "\n\n".join(messages[-4000:]))

async def switchmodel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        keyboard = [
            [InlineKeyboardButton("gemini-2.5-flash ⚡", callback_data="model_gemini-2.5-flash")],
            [InlineKeyboardButton("gemini-2.5-pro 🧩", callback_data="model_gemini-2.5-pro")]
        ]
        await update.message.reply_text("⚙️ Choose a model:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    model = context.args[0]
    if model in ALLOWED_MODELS:
        user_models[update.effective_user.id] = model
        await update.message.reply_text(f"⚙️ Model switched to {model}")
    else:
        await update.message.reply_text("Invalid model.")

async def instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /instructions <text>")
        return
    user_instructions[update.effective_user.id] = " ".join(context.args)
    await update.message.reply_text("🧠 Instructions set!")

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /ban <user> <duration>")
        return
    target = context.args[0]
    user_id = await _resolve_user(chat_id, target, context.bot)
    if user_id is None:
        return
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status == "creator":
            await update.message.reply_text("This user is the owner, I cannot ban this user!")
            return
    except:
        pass
    duration = " ".join(context.args[1:])
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await update.message.reply_text(f"{target} banned for {duration}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ban failed: {e}")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if not context.args:
        await update.message.reply_text("Usage: /unban <user>")
        return
    target = context.args[0]
    user_id = await _resolve_user(chat_id, target, context.bot)
    if user_id is None:
        return
    try:
        await context.bot.unban_chat_member(chat_id, user_id)
        await update.message.reply_text(f"{target} unbanned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Unban failed: {e}")

async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /mute <user> <duration>")
        return
    target = context.args[0]
    user_id = await _resolve_user(chat_id, target, context.bot)
    if user_id is None:
        return
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status == "creator":
            await update.message.reply_text("This user is the owner, I cannot mute this user!")
            return
    except:
        pass
    duration_str = " ".join(context.args[1:])
    td = parse_duration(duration_str)
    if not td:
        await update.message.reply_text("Invalid duration.")
        return
    until = datetime.now() + td
    try:
        await context.bot.restrict_chat_member(chat_id, user_id, until_date=int(until.timestamp()), can_send_messages=False)
        await update.message.reply_text(f"{target} muted until {until.strftime('%Y-%m-%d %H:%M')}")
    except Exception as e:
        await update.message.reply_text(f"❌ Mute failed: {e}")

async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if not context.args:
        await update.message.reply_text("Usage: /unmute <user>")
        return
    target = context.args[0]
    user_id = await _resolve_user(chat_id, target, context.bot)
    if user_id is None:
        return
    try:
        await context.bot.restrict_chat_member(
            chat_id, user_id,
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        await update.message.reply_text(f"{target} unmuted.")
    except Exception as e:
        await update.message.reply_text(f"❌ Unmute failed: {e}")

async def warning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /warning <user> <reason>")
        return
    target = context.args[0]
    user_id = await _resolve_user(chat_id, target, context.bot)
    if user_id is None:
        return
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status == "creator":
            await update.message.reply_text("This user is the owner, I cannot warn this user!")
            return
    except:
        pass
    if chat_id not in group_warnings:
        group_warnings[chat_id] = {}
    if user_id not in group_warnings[chat_id]:
        group_warnings[chat_id][user_id] = []
    group_warnings[chat_id][user_id].append(" ".join(context.args[1:]))
    count = len(group_warnings[chat_id][user_id])
    await update.message.reply_text(f"⚠️ Warning {count}/3 issued to {target}.")
    if count >= 3:
        until = datetime.now() + timedelta(hours=24)
        try:
            await context.bot.ban_chat_member(chat_id, user_id, until_date=int(until.timestamp()))
            await update.message.reply_text(f"🚨 Auto-banned for 24h after 3 warnings.")
        except:
            pass

async def removewarnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removewarnings <user>")
        return
    target = context.args[0]
    user_id = await _resolve_user(chat_id, target, context.bot)
    if user_id is None:
        return
    if chat_id in group_warnings and user_id in group_warnings[chat_id]:
        del group_warnings[chat_id][user_id]
    await update.message.reply_text(f"🧹 Warnings cleared for {target}.")

async def checkwarnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if context.args:
        resolved = await _resolve_user(chat_id, context.args[0], context.bot)
        if resolved:
            user_id = resolved
    count = len(group_warnings.get(chat_id, {}).get(user_id, []))
    await update.message.reply_text(f"📋 You have {count} warning(s).")

async def role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /role <user> <role>")
        return
    target = context.args[0]
    user_id = await _resolve_user(chat_id, target, context.bot)
    if user_id is None:
        return
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status == "creator":
            await update.message.reply_text("Owner role cannot be changed.")
            return
    except:
        pass
    if chat_id not in group_roles:
        group_roles[chat_id] = {}
    group_roles[chat_id][user_id] = " ".join(context.args[1:])
    await update.message.reply_text(f"🎭 Role set for {target}.")

async def removerole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removerole <user>")
        return
    target = context.args[0]
    user_id = await _resolve_user(chat_id, target, context.bot)
    if user_id is None:
        return
    if chat_id in group_roles and user_id in group_roles[chat_id]:
        del group_roles[chat_id][user_id]
    await update.message.reply_text(f"❌ Role removed from {target}.")

async def setautomode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if not context.args:
        keyboard = [
            [InlineKeyboardButton("strict 🔒", callback_data="automode_strict")],
            [InlineKeyboardButton("normal ⚖️", callback_data="automode_normal")],
            [InlineKeyboardButton("fun 🎉", callback_data="automode_fun")]
        ]
        await update.message.reply_text("⚙️ Select mode:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    mode = context.args[0].lower()
    if mode in ["strict", "normal", "fun"]:
        group_automode[chat_id] = mode
        await update.message.reply_text(f"⚙️ Auto mode: {mode}")
    else:
        await update.message.reply_text("Invalid mode.")

async def removeautomode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if chat_id in group_automode:
        del group_automode[chat_id]
    await update.message.reply_text("🚫 Auto mode disabled.")

async def welcomemessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if not context.args:
        await update.message.reply_text("Usage: /welcomemessage <text>")
        return
    group_welcome_msg[chat_id] = " ".join(context.args)
    await update.message.reply_text("👋 Welcome message set!")

async def leavingmessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not await _is_admin(chat_id, update.effective_user.id, context.bot):
        return
    if not context.args:
        await update.message.reply_text("Usage: /leavingmessage <text>")
        return
    group_leaving_msg[chat_id] = " ".join(context.args)
    await update.message.reply_text("😢 Leaving message set!")

async def poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /poll Q | A | B")
        return
    parts = " ".join(context.args).split("|")
    if len(parts) < 2:
        await update.message.reply_text("Use '|' to separate options.")
        return
    q, *opts = [p.strip() for p in parts]
    if len(opts) < 2:
        await update.message.reply_text("Need ≥2 options.")
        return
    await update.message.reply_poll(q, opts, is_anonymous=False)

async def tictactoe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Usage: /tictactoe @user")
        return
    opponent_id = await _resolve_user(chat_id, context.args[0], context.bot)
    if opponent_id is None:
        return
    game_id = str(uuid4())
    active_games[game_id] = {
        "players": [update.effective_user.id, opponent_id],
        "board": [" "] * 9,
        "turn": 0,
        "chat_id": chat_id
    }
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(" ", callback_data=f"ttt_{game_id}_0"),
         InlineKeyboardButton(" ", callback_data=f"ttt_{game_id}_1"),
         InlineKeyboardButton(" ", callback_data=f"ttt_{game_id}_2")],
        [InlineKeyboardButton(" ", callback_data=f"ttt_{game_id}_3"),
         InlineKeyboardButton(" ", callback_data=f"ttt_{game_id}_4"),
         InlineKeyboardButton(" ", callback_data=f"ttt_{game_id}_5")],
        [InlineKeyboardButton(" ", callback_data=f"ttt_{game_id}_6"),
         InlineKeyboardButton(" ", callback_data=f"ttt_{game_id}_7"),
         InlineKeyboardButton(" ", callback_data=f"ttt_{game_id}_8")]
    ])
    await update.message.reply_text(
        f"🎮 Tic-Tac-Toe\nTurn: {update.effective_user.mention_html()} (X)",
        reply_markup=markup,
        parse_mode="HTML"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("auth_"):
        chat_id = int(data.split("_")[1])
        if await _is_admin(chat_id, query.from_user.id, context.bot):
            group_admin_mode[chat_id] = True
            await query.edit_message_text("✅ Bot authorized!")
        else:
            await query.answer("Admins only.", show_alert=True)
    elif data.startswith("model_"):
        model = data.split("_", 1)[1]
        if model in ALLOWED_MODELS:
            user_models[query.from_user.id] = model
            await query.edit_message_text(f"⚙️ Model: {model}")
    elif data.startswith("automode_"):
        mode = data.split("_", 1)[1]
        if mode in ["strict", "normal", "fun"]:
            group_automode[query.message.chat.id] = mode
            await query.edit_message_text(f"⚙️ Mode: {mode}")
    elif data.startswith("ttt_"):
        _, game_id, pos = data.split("_")
        pos = int(pos)
        if game_id not in active_games:
            await query.edit_message_text("Game expired.")
            return
        game = active_games[game_id]
        if query.from_user.id != game["players"][game["turn"]]:
            await query.answer("Not your turn!", show_alert=True)
            return
        if game["board"][pos] != " ":
            await query.answer("Taken!", show_alert=True)
            return
        symbol = "X" if game["turn"] == 0 else "O"
        game["board"][pos] = symbol
        win = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]]
        winner = None
        for p in win:
            if game["board"][p[0]] == game["board"][p[1]] == game["board"][p[2]] != " ":
                winner = game["turn"]
                break
        if winner is not None:
            u = await context.bot.get_chat_member(game["chat_id"], game["players"][winner])
            await query.edit_message_text(f"🎉 {u.user.mention_html()} wins!", parse_mode="HTML")
            del active_games[game_id]
        elif " " not in game["board"]:
            await query.edit_message_text("🤝 Draw!")
            del active_games[game_id]
        else:
            game["turn"] = 1 - game["turn"]
            next_u = await context.bot.get_chat_member(game["chat_id"], game["players"][game["turn"]])
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(game["board"][i] or " ", callback_data=f"ttt_{game_id}_{i}") for i in range(3)],
                [InlineKeyboardButton(game["board"][i] or " ", callback_data=f"ttt_{game_id}_{i}") for i in range(3,6)],
                [InlineKeyboardButton(game["board"][i] or " ", callback_data=f"ttt_{game_id}_{i}") for i in range(6,9)]
            ])
            await query.edit_message_text(
                f"🎮 Tic-Tac-Toe\nTurn: {next_u.user.mention_html()} ({'X' if game['turn']==0 else 'O'})",
                reply_markup=markup,
                parse_mode="HTML"
            )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id < 0:
        if not group_admin_mode.get(chat_id, False):
            return
        return
    if update.message.text and not update.message.text.startswith("/"):
        await send_gemini_response(update, context, update.message.text)

async def member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not group_admin_mode.get(chat_id, False):
        return
    status = update.chat_member.difference.get("status")
    if not status:
        return
    old_status, new_status = status
    user = update.chat_member.new_chat_member.user
    if new_status == "member" and old_status is None:
        msg = group_welcome_msg.get(chat_id, "Welcome {user}!").replace("{user}", user.mention_html())
        await context.bot.send_message(chat_id, msg, parse_mode="HTML")
    elif new_status in ["left", "kicked"] and old_status == "member":
        msg = group_leaving_msg.get(chat_id, "Goodbye {user}!").replace("{user}", user.mention_html())
        await context.bot.send_message(chat_id, msg, parse_mode="HTML")

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("addtogroup", addtogroup))
    app.add_handler(CommandHandler("newchat", newchat))
    app.add_handler(CommandHandler("clearchat", newchat))
    app.add_handler(CommandHandler("incognitomode", lambda u,c: (incognitomode.update({u.effective_user.id: not incognitomode.get(u.effective_user.id, False)}), u.message.reply_text(f"{'❌' if incognitomode.get(u.effective_user.id, False) else '💾'} Incognito {'OFF' if incognitomode.get(u.effective_user.id, False) else 'ON'}."))[1]))
    app.add_handler(CommandHandler("chathistory", chathistory))
    app.add_handler(CommandHandler("switchmodel", switchmodel))
    app.add_handler(CommandHandler("instructions", instructions))

    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("warning", warning))
    app.add_handler(CommandHandler("removewarnings", removewarnings))
    app.add_handler(CommandHandler("checkwarnings", checkwarnings))
    app.add_handler(CommandHandler("role", role))
    app.add_handler(CommandHandler("removerole", removerole))
    app.add_handler(CommandHandler("setautomode", setautomode))
    app.add_handler(CommandHandler("removeautomode", removeautomode))
    app.add_handler(CommandHandler("welcomemessage", welcomemessage))
    app.add_handler(CommandHandler("leavingmessage", leavingmessage))
    app.add_handler(CommandHandler("poll", poll))
    app.add_handler(CommandHandler("tictactoe", tictactoe))

    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(ChatMemberHandler(member_handler, ChatMemberHandler.CHAT_MEMBER))

    PORT = int(os.environ.get("PORT", "10000"))
    RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not RENDER_EXTERNAL_URL:
        raise RuntimeError("RENDER_EXTERNAL_URL not set")
    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/webhook"
    
    app.bot.set_webhook(url=WEBHOOK_URL) 
    
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
