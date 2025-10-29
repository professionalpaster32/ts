# bot.py
import os
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from uuid import uuid4

from telegram import Update, User, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup
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
import asyncio

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or GEMINI_API_KEY in .env")

genai.configure(api_key=GEMINI_API_KEY)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Data Stores ---
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

# --- Helpers ---
def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if chat_id > 0:
        return True
    try:
        member = context.bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except:
        return False

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
    chat_id = update.effective_chat.id

    model_name = user_models.get(user_id, DEFAULT_MODEL)
    instructions = user_instructions.get(user_id, DEFAULT_INSTRUCTIONS)

    if incognito_mode.get(user_id, False):
        chat_history = []
    else:
        chat_history = user_chats.get(user_id, [])
        chat_history.append({"role": "user", "parts": [prompt]})

    model = genai.GenerativeModel(model_name, system_instruction=instructions)
    try:
        if incognito_mode.get(user_id, False):
            response = await model.generate_content_async(prompt)
        else:
            chat = model.start_chat(history=chat_history)
            response = await chat.send_message_async(prompt)
            user_chats[user_id] = chat.history
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        await update.message.reply_text("❌ Error generating response. Try again.")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "hello, how can i assist you?\n"
        "/help\n"
        "📘 Shows all available commands and what they do.\n"
        "Use this anytime if you’re lost!\n\n"
        "/addtogroup\n"
        "👥 Authorizes the bot to work in your group — so everyone can chat with Gemini together!\n\n"
        "/instructions\n"
        "🧠 Add or change custom system instructions for Gemini.\n"
        "Example:\n"
        "/instructions Talk like an Member of Gen z\n\n"
        "/switchmodel\n"
        "⚙️ Switch between models:\n\n"
        "gemini-2.5-flash ⚡ (fastest)\n\n"
        "gemini-2.5-pro 🧩 (for long-horizontal tasks)\n"
        "Example:\n"
        "/switchmodel gemini-2.5-pro\n\n"
        "💸 Our bot is 100% FREE — no billing or pricing required!\n"
        "Just chat, code, and create.\n\n"
        "💬 Chat Control Commands\n\n"
        "🆕 /newchat\n"
        "Starts a brand-new conversation — clears all context and memory.\n"
        "Use this when you want a clean slate.\n\n"
        "🧹 /clearchat\n"
        "Same as /newchat, just another way to reset the chat.\n\n"
        "🕵️ /incognitomode\n"
        "Turns on/off private chat mode.\n"
        "When ON → ❌ your messages won’t be saved (no memory).\n"
        "When OFF → 💾 chat memory resumes normally.\n\n"
        "📜 /chathistory\n"
        "Shows your current chat memory with Gemini.\n"
        "Lets you view what’s been remembered in your conversation.\n\n"
        "🚀 Overall\n"
        "✅  AI assistant\n"
        "✅ Generate and debug code\n"
        "✅ Work in groups with /addtogroup\n"
        "✅ Support model switching & system instructions\n"
        "✅ Absolutely free — no billing required"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def addtogroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0:
        await update.message.reply_text("Use this command in a group to authorize the bot.")
        return
    group_admin_mode[chat_id] = True
    await update.message.reply_text("✅ Bot authorized for group use! Admin commands now active.")

# --- Private Chat AI Commands ---
async def newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_chats[user_id] = []
    await update.message.reply_text("🆕 New chat started! Memory cleared.")

async def clearchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await newchat(update, context)

async def incognitomode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current = incognito_mode.get(user_id, False)
    incognito_mode[user_id] = not current
    status = "ON" if not current else "OFF"
    icon = "❌" if not current else "💾"
    await update.message.reply_text(f"{icon} Incognito mode {status}.")

async def chathistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history = user_chats.get(user_id, [])
    if not history:
        await update.message.reply_text("📜 No chat history yet.")
        return
    text = "\n".join([f"{'You' if msg['role'] == 'user' else 'Gemini'}: {msg['parts'][0]}" for msg in history])
    if len(text) > 4000:
        text = text[-4000:]
    await update.message.reply_text(f"📜 Chat History:\n\n{text}")

async def switchmodel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /switchmodel <model_name>\nAvailable: gemini-2.5-flash, gemini-2.5-pro")
        return
    model = context.args[0]
    if model not in ALLOWED_MODELS:
        await update.message.reply_text("Invalid model. Choose: gemini-2.5-flash or gemini-2.5-pro")
        return
    user_models[user_id] = model
    await update.message.reply_text(f"⚙️ Model switched to {model}")

async def instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /instructions <your instruction>")
        return
    instr = " ".join(context.args)
    user_instructions[user_id] = instr
    await update.message.reply_text("🧠 Custom instructions set!")

# --- Group Admin Commands ---
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /ban <user> <duration>")
        return
    target = context.args[0]
    duration = " ".join(context.args[1:])
    user_id = None
    if target.startswith("@"):
        try:
            user = await context.bot.get_chat_member(chat_id, target)
            user_id = user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            user_id = int(target)
        except:
            await update.message.reply_text("Invalid user ID or username.")
            return
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await update.message.reply_text(f"{target}, you have been banned for {duration} from this group!")
    except Exception as e:
        await update.message.reply_text(f"Failed to ban: {e}")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /unban <user>")
        return
    target = context.args[0]
    user_id = None
    if target.startswith("@"):
        try:
            user = await context.bot.get_chat_member(chat_id, target)
            user_id = user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            user_id = int(target)
        except:
            await update.message.reply_text("Invalid user ID or username.")
            return
    try:
        await context.bot.unban_chat_member(chat_id, user_id)
        await update.message.reply_text(f"{target} has been unbanned.")
    except Exception as e:
        await update.message.reply_text(f"Failed to unban: {e}")

async def tempban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /tempban <user> <duration>")
        return
    target = context.args[0]
    duration_str = " ".join(context.args[1:])
    td = parse_duration(duration_str)
    if not td:
        await update.message.reply_text("Invalid duration. Use: 1 day, 2 weeks, etc.")
        return
    user_id = None
    if target.startswith("@"):
        try:
            user = await context.bot.get_chat_member(chat_id, target)
            user_id = user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            user_id = int(target)
        except:
            await update.message.reply_text("Invalid user ID or username.")
            return
    until = datetime.now() + td
    try:
        await context.bot.ban_chat_member(chat_id, user_id, until_date=until)
        await update.message.reply_text(f"{target} banned until {until.strftime('%Y-%m-%d %H:%M')}")
    except Exception as e:
        await update.message.reply_text(f"Failed to tempban: {e}")

async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /mute <user> <duration>")
        return
    target = context.args[0]
    duration_str = " ".join(context.args[1:])
    td = parse_duration(duration_str)
    if not td:
        await update.message.reply_text("Invalid duration. Use: 10 minutes, 1 day, etc.")
        return
    user_id = None
    if target.startswith("@"):
        try:
            user = await context.bot.get_chat_member(chat_id, target)
            user_id = user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            user_id = int(target)
        except:
            await update.message.reply_text("Invalid user ID or username.")
            return
    until = datetime.now() + td
    try:
        await context.bot.restrict_chat_member(chat_id, user_id, until_date=until, can_send_messages=False)
        await update.message.reply_text(f"{target} muted until {until.strftime('%Y-%m-%d %H:%M')}")
    except Exception as e:
        await update.message.reply_text(f"Failed to mute: {e}")

async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /unmute <user>")
        return
    target = context.args[0]
    user_id = None
    if target.startswith("@"):
        try:
            user = await context.bot.get_chat_member(chat_id, target)
            user_id = user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            user_id = int(target)
        except:
            await update.message.reply_text("Invalid user ID or username.")
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
        await update.message.reply_text(f"Failed to unmute: {e}")

async def warning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /warning <user> <reason>")
        return
    target = context.args[0]
    reason = " ".join(context.args[1:])
    user_id = None
    if target.startswith("@"):
        try:
            user = await context.bot.get_chat_member(chat_id, target)
            user_id = user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            user_id = int(target)
        except:
            await update.message.reply_text("Invalid user ID or username.")
            return
    if chat_id not in group_warnings:
        group_warnings[chat_id] = {}
    if user_id not in group_warnings[chat_id]:
        group_warnings[chat_id][user_id] = []
    group_warnings[chat_id][user_id].append(reason)
    count = len(group_warnings[chat_id][user_id])
    await update.message.reply_text(f"⚠️ Warning issued to {target} ({count}/3): {reason}")
    if count >= 3:
        until = datetime.now() + timedelta(hours=24)
        try:
            await context.bot.ban_chat_member(chat_id, user_id, until_date=until)
            await update.message.reply_text(f"🚨 {target} auto-banned for 24h after 3 warnings.")
        except:
            pass

async def removewarnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removewarnings <user>")
        return
    target = context.args[0]
    user_id = None
    if target.startswith("@"):
        try:
            user = await context.bot.get_chat_member(chat_id, target)
            user_id = user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            user_id = int(target)
        except:
            await update.message.reply_text("Invalid user ID or username.")
            return
    if chat_id in group_warnings and user_id in group_warnings[chat_id]:
        del group_warnings[chat_id][user_id]
    await update.message.reply_text(f"🧹 Warnings cleared for {target}.")

async def checkwarnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    target_id = user_id
    if context.args:
        target = context.args[0]
        if target.startswith("@"):
            try:
                user = await context.bot.get_chat_member(chat_id, target)
                target_id = user.user.id
            except:
                await update.message.reply_text("User not found.")
                return
        else:
            try:
                target_id = int(target)
            except:
                await update.message.reply_text("Invalid user ID or username.")
                return
    if chat_id in group_warnings and target_id in group_warnings[chat_id]:
        count = len(group_warnings[chat_id][target_id])
        await update.message.reply_text(f"📋 {target_id} has {count} warning(s).")
    else:
        await update.message.reply_text("📋 No warnings found.")

async def role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /role <user> <role>")
        return
    target = context.args[0]
    role_name = " ".join(context.args[1:])
    user_id = None
    if target.startswith("@"):
        try:
            user = await context.bot.get_chat_member(chat_id, target)
            user_id = user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            user_id = int(target)
        except:
            await update.message.reply_text("Invalid user ID or username.")
            return
    if chat_id not in group_roles:
        group_roles[chat_id] = {}
    group_roles[chat_id][user_id] = role_name
    await update.message.reply_text(f"🎭 Role '{role_name}' assigned to {target}.")

async def removerole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /removerole <user>")
        return
    target = context.args[0]
    user_id = None
    if target.startswith("@"):
        try:
            user = await context.bot.get_chat_member(chat_id, target)
            user_id = user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            user_id = int(target)
        except:
            await update.message.reply_text("Invalid user ID or username.")
            return
    if chat_id in group_roles and user_id in group_roles[chat_id]:
        del group_roles[chat_id][user_id]
    await update.message.reply_text(f"❌ Role removed from {target}.")

async def setautomode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /setautomode <strict|normal|fun>")
        return
    mode = context.args[0].lower()
    if mode not in ["strict", "normal", "fun"]:
        await update.message.reply_text("Invalid mode. Choose: strict, normal, fun")
        return
    group_automode[chat_id] = mode
    await update.message.reply_text(f"⚙️ Auto mode set to: {mode}")

async def removeautomode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if chat_id in group_automode:
        del group_automode[chat_id]
    await update.message.reply_text("🚫 Auto mode disabled.")

async def welcomemessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /welcomemessage <text>")
        return
    msg = " ".join(context.args)
    group_welcome_msg[chat_id] = msg
    await update.message.reply_text("👋 Welcome message set!")

async def leavingmessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0 or not is_admin(chat_id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /leavingmessage <text>")
        return
    msg = " ".join(context.args)
    group_leaving_msg[chat_id] = msg
    await update.message.reply_text("😢 Leaving message set!")

async def poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /poll Question | Option1 | Option2 | ...")
        return
    full = " ".join(context.args)
    if "|" not in full:
        await update.message.reply_text("Separate question and options with '|'")  
        return
    parts = [p.strip() for p in full.split("|")]
    question = parts[0]
    options = parts[1:]
    if len(options) < 2:
        await update.message.reply_text("Need at least 2 options.")
        return
    await update.message.reply_poll(question, options, is_anonymous=False)

async def tictactoe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    if not context.args:
        if chat_id > 0:
            await update.message.reply_text("Usage: /tictactoe <@opponent>")
            return
        else:
            await update.message.reply_text("Start a game in private or tag someone in group.")
            return
    opponent = context.args[0]
    opponent_id = None
    if opponent.startswith("@"):
        try:
            opp_user = await context.bot.get_chat_member(chat_id, opponent)
            opponent_id = opp_user.user.id
        except:
            await update.message.reply_text("User not found.")
            return
    else:
        try:
            opponent_id = int(opponent)
        except:
            await update.message.reply_text("Invalid user.")
            return
    game_id = str(uuid4())
    active_games[game_id] = {
        "players": [user.id, opponent_id],
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
        f"🎮 Tic-Tac-Toe: {user.mention_html()} vs {opponent}\n"
        f"Turn: {user.mention_html()} (X)",
        reply_markup=markup,
        parse_mode="HTML"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("ttt_"):
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
            await query.answer("Already taken!", show_alert=True)
            return
        symbol = "X" if game["turn"] == 0 else "O"
        game["board"][pos] = symbol
        # Check win
        win_patterns = [
            [0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]
        ]
        winner = None
        for pattern in win_patterns:
            if game["board"][pattern[0]] == game["board"][pattern[1]] == game["board"][pattern[2]] != " ":
                winner = game["turn"]
                break
        if winner is not None:
            winner_user = await context.bot.get_chat_member(game["chat_id"], game["players"][winner])
            await query.edit_message_text(f"🎉 {winner_user.user.mention_html()} wins!", parse_mode="HTML")
            del active_games[game_id]
            return
        if " " not in game["board"]:
            await query.edit_message_text("🤝 It's a draw!")
            del active_games[game_id]
            return
        game["turn"] = 1 - game["turn"]
        next_user = await context.bot.get_chat_member(game["chat_id"], game["players"][game["turn"]])
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(game["board"][0] or " ", callback_data=f"ttt_{game_id}_0"),
             InlineKeyboardButton(game["board"][1] or " ", callback_data=f"ttt_{game_id}_1"),
             InlineKeyboardButton(game["board"][2] or " ", callback_data=f"ttt_{game_id}_2")],
            [InlineKeyboardButton(game["board"][3] or " ", callback_data=f"ttt_{game_id}_3"),
             InlineKeyboardButton(game["board"][4] or " ", callback_data=f"ttt_{game_id}_4"),
             InlineKeyboardButton(game["board"][5] or " ", callback_data=f"ttt_{game_id}_5")],
            [InlineKeyboardButton(game["board"][6] or " ", callback_data=f"ttt_{game_id}_6"),
             InlineKeyboardButton(game["board"][7] or " ", callback_data=f"ttt_{game_id}_7"),
             InlineKeyboardButton(game["board"][8] or " ", callback_data=f"ttt_{game_id}_8")]
        ])
        await query.edit_message_text(
            f"🎮 Tic-Tac-Toe\nTurn: {next_user.user.mention_html()} ({'X' if game['turn'] == 0 else 'O'})",
            reply_markup=markup,
            parse_mode="HTML"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id < 0:
        if not group_admin_mode.get(chat_id, False):
            return
        # Group mode: ignore non-command messages
        return
    # Private mode: AI chat
    if update.message.text and not update.message.text.startswith("/"):
        await send_gemini_response(update, context, update.message.text)

async def member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id > 0:
        return
    if not group_admin_mode.get(chat_id, False):
        return
    status = update.chat_member.difference.get("status")
    if not status:
        return
    old_status, new_status = status
    user = update.chat_member.new_chat_member.user
    if new_status == "member" and old_status is None:
        msg = group_welcome_msg.get(chat_id, "Welcome {user}!")
        msg = msg.replace("{user}", user.mention_html())
        await context.bot.send_message(chat_id, msg, parse_mode="HTML")
    elif new_status in ["left", "kicked"] and old_status == "member":
        msg = group_leaving_msg.get(chat_id, "Goodbye {user}!")
        msg = msg.replace("{user}", user.mention_html())
        await context.bot.send_message(chat_id, msg, parse_mode="HTML")

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Private Chat Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("addtogroup", addtogroup))
    app.add_handler(CommandHandler("newchat", newchat))
    app.add_handler(CommandHandler("clearchat", clearchat))
    app.add_handler(CommandHandler("incognitomode", incognitomode))
    app.add_handler(CommandHandler("chathistory", chathistory))
    app.add_handler(CommandHandler("switchmodel", switchmodel))
    app.add_handler(CommandHandler("instructions", instructions))

    # Group Admin Commands
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("tempban", tempban))
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

    # Callbacks
    app.add_handler(CallbackQueryHandler(button_handler))

    # Message Handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Member Handler
    app.add_handler(ChatMemberHandler(member_handler, ChatMemberHandler.CHAT_MEMBER))

    app.run_polling()

if __name__ == "__main__":
    main()