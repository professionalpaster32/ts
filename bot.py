import os
import logging
import re
import datetime
import asyncio
from dotenv import load_dotenv
import google.generativeai as genai
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
    ChatMemberHandler,
)

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

try:
    genai.configure(api_key=GEMINI_API_KEY)
except Exception as e:
    logger.error(f"Failed to configure Gemini: {e}")

user_states = {}
group_states = {}
tictactoe_games = {}

def get_user_state(user_id):
    if user_id not in user_states:
        user_states[user_id] = {
            "history": [],
            "model": "gemini-flash",
            "instructions": "You are a helpful assistant.",
            "incognito": False,
        }
    return user_states[user_id]

def get_group_state(chat_id):
    if chat_id not in group_states:
        group_states[chat_id] = {
            "warnings": {},
            "roles": {},
            "automode": "normal",
            "welcome_msg": "Welcome {user} to the group!",
            "leaving_msg": "Goodbye {user}!",
        }
    return group_states[chat_id]

async def is_admin(chat_id, user_id, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        return user_id in [admin.user.id for admin in admins]
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

def parse_duration(text: str) -> datetime.timedelta | None:
    match = re.match(r"(\d+)\s+(minute|minutes|day|days|week|weeks|month|months)", text, re.IGNORECASE)
    if not match:
        return None
    
    value = int(match.group(1))
    unit = match.group(2).lower()
    
    if unit in ["minute", "minutes"]:
        return datetime.timedelta(minutes=value)
    if unit in ["day", "days"]:
        return datetime.timedelta(days=value)
    if unit in ["week", "weeks"]:
        return datetime.timedelta(weeks=value)
    if unit in ["month", "months"]:
        return datetime.timedelta(days=value * 30)
    return None

PRIVATE_HELP_TEXT = """
hello, how can i assist you?
/help
📘 Shows all available commands and what they do.
Use this anytime if you’re lost!

/addtogroup
👥 Authorizes the bot to work in your group — so everyone can chat with Gemini together!

/instructions
🧠 Add or change custom system instructions for Gemini.
Example:
/instructions Talk like an Member of Gen z

/switchmodel
⚙️ Switch between models:
`gemini-flash` ⚡ (fastest)
`gemini-pro` 🧩 (smarter)
Example:
/switchmodel gemini-pro

💸 Our bot is 100% FREE — no billing or pricing required!
Just chat, code, and create.

💬 Chat Control Commands

🆕 /newchat
Starts a brand-new conversation — clears all context and memory.
Use this when you want a clean slate.

🧹 /clearchat
Same as /newchat, just another way to reset the chat.

🕵️ /incognitomode
Turns on/off private chat mode.
When ON → ❌ your messages won’t be saved (no memory).
When OFF → 💾 chat memory resumes normally.

📜 /chathistory
Shows your current chat memory with Gemini.
Lets you view what’s been remembered in your conversation.

🚀 Overall
✅  AI assistant
✅ Generate and debug code
✅ Work in groups with /addtogroup
✅ Support model switching & system instructions
✅ Absolutely free — no billing required
"""

async def start_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PRIVATE_HELP_TEXT, parse_mode=ParseMode.MARKDOWN)

async def help_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PRIVATE_HELP_TEXT, parse_mode=ParseMode.MARKDOWN)

async def add_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = (await context.bot.get_me()).username
    url = f"https://t.me/{bot_username}?startgroup=true"
    keyboard = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton(text="Click to Add to Group", url=url)
    )
    await update.message.reply_text(
        "Lit! Click the button below to add me to your group.\n\n"
        "Remember, in groups I act as an **Admin Bot** 🛡️, not a chatbot.",
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    state["history"] = []
    await update.message.reply_text("Clean slate. Your chat history is cleared. 🧹")

async def incognito_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    state["incognito"] = not state["incognito"]
    status = "ON 🕵️ (chat history *won't* be saved)" if state["incognito"] else "OFF 💾 (chat history *will* be saved)"
    await update.message.reply_text(f"Incognito mode is now {status}.")

async def chat_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    
    if not state["history"]:
        await update.message.reply_text("You have no chat history saved. Start a new chat!")
        return

    history_text = "📜 **Your Chat History:**\n\n"
    for item in state["history"]:
        role = "You" if item['role'] == 'user' else "Gemini"
        history_text += f"**{role}:** {item['parts'][0]}\n\n"
        
    if len(history_text) > 4096:
        history_text = history_text[:4090] + "\n... (truncated)"
        
    await update.message.reply_text(history_text, parse_mode=ParseMode.MARKDOWN)

async def switch_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    
    if not context.args:
        await update.message.reply_text(
            f"Your current model is: `{state['model']}`\n\n"
            "To switch, use:\n"
            "`/switchmodel gemini-flash` ⚡\n"
            "`/switchmodel gemini-pro` 🧩",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    model_name = context.args[0].lower()
    if model_name in ["gemini-pro", "gemini-flash"]:
        state["model"] = model_name
        await update.message.reply_text(f"Bet. Switched model to `{state['model']}`.")
    else:
        await update.message.reply_text("Nah, that's not a valid model. Use `gemini-pro` or `gemini-flash`.")

async def set_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    
    instructions = " ".join(context.args)
    if not instructions:
        await update.message.reply_text(
            f"**Current Instructions:**\n`{state['instructions']}`\n\n"
            "To set new ones, type:\n"
            "`/instructions You are a pirate`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
        
    state["instructions"] = instructions
    await update.message.reply_text(f"Got it. System instructions updated. 🧠")
    
async def handle_gemini_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    prompt = update.message.text
    state = get_user_state(user_id)
    
    if not GEMINI_API_KEY:
        await update.message.reply_text("Sry, the admin hasn't set up the Gemini API key. Can't chat rn. 😬")
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        model = genai.GenerativeModel(
            model_name=state["model"],
            system_instruction=state["instructions"]
        )
        
        chat = model.start_chat(history=state["history"])
        response = await chat.send_message_async(prompt)
        
        if not state["incognito"]:
            state["history"] = chat.history

        await update.message.reply_text(response.text)
        
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        await update.message.reply_text(f"Oof, something went wrong with the AI. Error: {e}")

GROUP_HELP_TEXT = """
🛡️ **Admin Command Mode** 🛡️

I'm in group mode. Only admins can use most commands.

---

⚔️ **Moderation Commands**

`/ban {user} {reason}`
🚫 Permanently ban a member.
Example: `/ban @user spamming`

`/unban {user_id or username}`
✅ Unban a previously banned user.

`/tempban {user} {time} {reason}`
⏳ Temp ban. Time: `10 minutes`, `2 days`, `1 week`
Example: `/tempban @user 2 days timeout`

`/mute {user} {time}`
🔇 Restrict a user for a specified time.
Example: `/mute @user 30 minutes`

`/unmute {user}`
🔊 Removes mute from a member.

---

⚠️ **Warnings System**

`/warning {user} {reason}`
⚠️ Give a warning. 3 warnings = 24h temp-ban (if automode=strict).

`/removewarnings {user}`
🧹 Clears all warnings for a user.

`/checkwarnings {optional @user}`
📋 Shows warning count.

---

👑 **Role Management**

`/role {user} {role}`
🎭 Assign a custom text role (e.g., VIP).

`/removerole {user}`
❌ Removes a user’s assigned role.

---

⚙️ **Auto & Mode Settings**

`/setautomode {mode}`
⚙️ Set moderation mode:
`strict` 🔒 (auto-ban on 3 warnings)
`normal` ⚖️ (warn only)
`fun` 🎉 (light moderation)

`/removeautomode`
🚫 Disables auto moderation (sets to normal).

---

👋 **Welcome & Leaving Messages**

`/welcomemessage {text}`
👋 Sets custom welcome. Use `{user}` placeholder.
Example: `/welcomemessage Welcome {user}! ❤️`

`/leavingmessage {text}`
😢 Sets custom leaving message.
Example: `/leavingmessage Goodbye {user}!`

---

🗳️ **Polls & Fun** (Admin/Member)

`/poll {question} | {opt1} | {opt2} ...`
📊 Create a quick poll.

`/tictactoe {user}`
🎮 Play Tic-Tac-Toe with another member!

---

👥 **Member Commands** (non-admins)
`/checkwarnings` → view their own warnings
`/poll` → create polls
`/tictactoe` → start a match
`/help` → show this list
"""

async def help_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(GROUP_HELP_TEXT, parse_mode=ParseMode.MARKDOWN)

async def get_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user = None
    reason_parts = []

    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        reason_parts = context.args
    elif context.args:
        username = context.args[0]
        if username.startswith('@'):
            username = username[1:]
        
        if update.message.entities:
            for entity in update.message.entities:
                if entity.type == 'mention':
                    username = update.message.text[entity.offset+1:entity.offset+entity.length]
                if entity.type == 'text_mention':
                    target_user = entity.user
                    reason_parts = context.args[1:]
                    break
        
        if not target_user and not update.message.reply_to_message:
            await update.message.reply_text("Please reply to the user you want to action.")
            return None, ""

    reason = " ".join(reason_parts) if reason_parts else "No reason provided."
    return target_user, reason

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return

    target_user, reason = await get_target_user(update, context)
    if not target_user:
        return
        
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=target_user.id)
        mention = target_user.mention_markdown()
        await update.message.reply_text(f"{mention}, you have been banned from this group! Reason: {reason}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Couldn't ban user. {e}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return

    if not context.args:
        await update.message.reply_text("Who? Use `/unban {user_id or @username}`")
        return

    user_input = context.args[0]
    target_user_id = None
    
    try:
        target_user_id = int(user_input)
    except ValueError:
        await update.message.reply_text("Please provide a user ID. Unbanning by username is flaky.")
        return

    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=target_user_id)
        await update.message.reply_text(f"User {target_user_id} has been unbanned. ✅")
    except Exception as e:
        await update.message.reply_text(f"Couldn't unban user. {e}")

async def temp_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return

    target_user, _ = await get_target_user(update, context)
    if not target_user:
        return
        
    args = context.args[1:]
    if len(args) < 2:
        await update.message.reply_text("Usage: `/tempban {user} {time} {reason}`\nExample: `/tempban @user 2 days spam`")
        return
    
    duration_str = f"{args[0]} {args[1]}"
    duration = parse_duration(duration_str)
    reason = " ".join(args[2:]) if len(args) > 2 else "No reason provided."
    
    if not duration:
        await update.message.reply_text("Weird time format. Use: `10 minutes`, `2 days`, `1 week`")
        return
        
    until_date = datetime.datetime.now() + duration
    
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=target_user.id, until_date=until_date)
        mention = target_user.mention_markdown()
        await update.message.reply_text(f"{mention}, you have been temp-banned for {duration_str}. Reason: {reason}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Couldn't temp-ban user. {e}")

async def mute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return

    target_user, _ = await get_target_user(update, context)
    if not target_user:
        return
        
    args = context.args[1:]
    if len(args) < 2:
        await update.message.reply_text("Usage: `/mute {user} {time}`\nExample: `/mute @user 30 minutes`")
        return
        
    duration_str = f"{args[0]} {args[1]}"
    duration = parse_duration(duration_str)
    
    if not duration:
        await update.message.reply_text("Weird time format. Use: `10 minutes`, `2 days`, `1 week`")
        return
        
    until_date = datetime.datetime.now() + duration
    
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target_user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until_date
        )
        mention = target_user.mention_markdown()
        await update.message.reply_text(f"{mention}, you have been muted for {duration_str}. 🔇", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Couldn't mute user. {e}")

async def unmute_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return

    target_user, _ = await get_target_user(update, context)
    if not target_user:
        return
        
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target_user.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        mention = target_user.mention_markdown()
        await update.message.reply_text(f"{mention}, you have been unmuted. 🔊", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Couldn't unmute user. {e}")

async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return

    target_user, reason = await get_target_user(update, context)
    if not target_user:
        return
        
    group = get_group_state(chat_id)
    target_id_str = str(target_user.id)
    
    if target_id_str not in group["warnings"]:
        group["warnings"][target_id_str] = 0
        
    group["warnings"][target_id_str] += 1
    warnings = group["warnings"][target_id_str]
    mention = target_user.mention_markdown()
    
    await update.message.reply_text(f"⚠️ Warning issued to {mention}. Reason: {reason}\nThey now have {warnings}/3 warnings.", parse_mode=ParseMode.MARKDOWN)
    
    if warnings >= 3 and group["automode"] == "strict":
        await update.message.reply_text(f"{mention} reached 3 warnings. Issuing 24-hour temp-ban.", parse_mode=ParseMode.MARKDOWN)
        until_date = datetime.datetime.now() + datetime.timedelta(days=1)
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=target_user.id, until_date=until_date)
            group["warnings"][target_id_str] = 0
        except Exception as e:
            await update.message.reply_text(f"Couldn't auto-ban user. {e}")

async def remove_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return

    target_user, _ = await get_target_user(update, context)
    if not target_user:
        return
        
    group = get_group_state(chat_id)
    target_id_str = str(target_user.id)
    
    if target_id_str in group["warnings"]:
        group["warnings"][target_id_str] = 0
        
    mention = target_user.mention_markdown()
    await update.message.reply_text(f"Warnings cleared for {mention}. 🧹", parse_mode=ParseMode.MARKDOWN)

async def check_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    group = get_group_state(chat_id)
    
    target_user = None
    if context.args:
        if not await is_admin(chat_id, update.effective_user.id, context):
            await update.message.reply_text("You can only check your own warnings. Admins can check others.")
            return
        
        if not update.message.reply_to_message:
             await update.message.reply_text("Admin, please reply to the user to check their warnings.")
             return
        target_user = update.message.reply_to_message.from_user
    else:
        target_user = update.effective_user
        
    target_id_str = str(target_user.id)
    warnings = group["warnings"].get(target_id_str, 0)
    
    if target_user.id == update.effective_user.id:
        await update.message.reply_text(f"You have {warnings}/3 warnings. 📋")
    else:
        mention = target_user.mention_markdown()
        await update.message.reply_text(f"{mention} has {warnings}/3 warnings. 📋", parse_mode=ParseMode.MARKDOWN)

async def set_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return

    target_user, role = await get_target_user(update, context)
    if not target_user:
        return
        
    if not role:
        await update.message.reply_text("Usage: `/role {user} {role_name}`")
        return
        
    group = get_group_state(chat_id)
    group["roles"][str(target_user.id)] = role
    mention = target_user.mention_markdown()
    await update.message.reply_text(f"Role updated: {mention} is now a {role}! 🎭", parse_mode=ParseMode.MARKDOWN)

async def remove_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return

    target_user, _ = await get_target_user(update, context)
    if not target_user:
        return
        
    group = get_group_state(chat_id)
    target_id_str = str(target_user.id)
    
    if target_id_str in group["roles"]:
        del group["roles"][target_id_str]
        
    mention = target_user.mention_markdown()
    await update.message.reply_text(f"Role removed for {mention}. ❌", parse_mode=ParseMode.MARKDOWN)

async def set_auto_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return
        
    if not context.args:
        await update.message.reply_text("Usage: `/setautomode {strict|normal|fun}`")
        return
        
    mode = context.args[0].lower()
    if mode not in ["strict", "normal", "fun"]:
        await update.message.reply_text("Not a valid mode. Use `strict`, `normal`, or `fun`.")
        return
        
    group = get_group_state(chat_id)
    group["automode"] = mode
    await update.message.reply_text(f"Auto-moderation mode set to: {mode} ⚙️")

async def remove_auto_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return
        
    group = get_group_state(chat_id)
    group["automode"] = "normal"
    await update.message.reply_text("Auto-moderation disabled (set to `normal`). 🚫")

async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return
        
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: `/welcomemessage {text}`\nUse `{user}` as a placeholder.")
        return
        
    group = get_group_state(chat_id)
    group["welcome_msg"] = message
    await update.message.reply_text(f"Welcome message set! 👋")

async def set_leaving(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not await is_admin(chat_id, user_id, context):
        await update.message.reply_text("You're not an admin, my guy.")
        return
        
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: `/leavingmessage {text}`\nUse `{user}` as a placeholder.")
        return
        
    group = get_group_state(chat_id)
    group["leaving_msg"] = message
    await update.message.reply_text(f"Leaving message set! 😢")

async def poll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    parts = text.split('|')
    
    if len(parts) < 3:
        await update.message.reply_text("Usage: `/poll {question} | {option1} | {option2} ...`")
        return
        
    question = parts[0].strip()
    options = [opt.strip() for opt in parts[1:] if opt.strip()]
    
    if len(options) > 10:
        await update.message.reply_text("Max 10 options for a poll.")
        return
        
    try:
        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=question,
            options=options,
            is_anonymous=False 
        )
    except Exception as e:
        await update.message.reply_text(f"Couldn't create poll. {e}")

async def tictactoe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("TicTacToe feature is complex and under construction! 🚧")

async def welcome_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    group = get_group_state(chat_id)
    
    bot_id = context.bot.id
    
    for new_member in update.message.new_chat_members:
        if new_member.id == bot_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Ayo, thanks for adding me! I'm **MrGreedy_Bot**.\n\n"
                     "In this group, I'm in 🛡️ **Admin Mode** 🛡️.\n"
                     "Admins can use `/help` to see all moderation commands.\n\n"
                     "To chat with my AI, message me privately!",
                parse_mode=ParseMode.MARKDOWN
            )
            continue
            
        message = group["welcome_msg"]
        user_mention = new_member.mention_markdown()
        formatted_message = message.replace("{user}", user_mention)
        
        try:
            await update.message.reply_text(formatted_message, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"Failed to send welcome message: {e}")

async def leaving_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    group = get_group_state(chat_id)
    
    if update.message.left_chat_member:
        user = update.message.left_chat_member
        if user.id == context.bot.id:
            logger.info(f"Bot was removed from group {chat_id}")
            if chat_id in group_states:
                del group_states[chat_id]
            return
            
        message = group["leaving_msg"]
        user_mention = user.mention_markdown() if user.username else user.first_name
        formatted_message = message.replace("{user}", user_mention)
        
        try:
            await context.bot.send_message(chat_id=chat_id, text=formatted_message, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"Failed to send leaving message: {e}")

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN env var not set.")
        return
        
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    private_filter = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", start_private, filters=private_filter))
    app.add_handler(CommandHandler("help", help_private, filters=private_filter))
    app.add_handler(CommandHandler("addtogroup", add_to_group, filters=private_filter))
    app.add_handler(CommandHandler("newchat", new_chat, filters=private_filter))
    app.add_handler(CommandHandler("clearchat", new_chat, filters=private_filter))
    app.add_handler(CommandHandler("incognitomode", incognito_mode, filters=private_filter))
    app.add_handler(CommandHandler("chathistory", chat_history, filters=private_filter))
    app.add_handler(CommandHandler("switchmodel", switch_model, filters=private_filter))
    app.add_handler(CommandHandler("instructions", set_instructions, filters=private_filter))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private_filter, handle_gemini_chat))
    
    group_filter = filters.ChatType.GROUP | filters.ChatType.SUPERGROUP
    app.add_handler(CommandHandler("help", help_group, filters=group_filter))
    app.add_handler(CommandHandler("ban", ban_user, filters=group_filter))
    app.add_handler(CommandHandler("unban", unban_user, filters=group_filter))
    app.add_handler(CommandHandler("tempban", temp_ban_user, filters=group_filter))
    app.add_handler(CommandHandler("mute", mute_user, filters=group_filter))
    app.add_handler(CommandHandler("unmute", unmute_user, filters=group_filter))
    app.add_handler(CommandHandler("warning", warn_user, filters=group_filter))
    app.add_handler(CommandHandler("removewarnings", remove_warnings, filters=group_filter))
    app.add_handler(CommandHandler("checkwarnings", check_warnings, filters=group_filter))
    app.add_handler(CommandHandler("role", set_role, filters=group_filter))
    app.add_handler(CommandHandler("removerole", remove_role, filters=group_filter))
    app.add_handler(CommandHandler("setautomode", set_auto_mode, filters=group_filter))
    app.add_handler(CommandHandler("removeautomode", remove_auto_mode, filters=group_filter))
    app.add_handler(CommandHandler("welcomemessage", set_welcome, filters=group_filter))
    app.add_handler(CommandHandler("leavingmessage", set_leaving, filters=group_filter))
    app.add_handler(CommandHandler("poll", poll_command, filters=group_filter))
    app.add_handler(CommandHandler("tictactoe", tictactoe_command, filters=group_filter))
    
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_handler))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, leaving_handler))

    PORT = int(os.environ.get('PORT', 8443))
    RENDER_URL = os.environ.get('RENDER_URL') 

    if not RENDER_URL:
        logger.warning("RENDER_URL env var not set. Falling back to polling for local dev.")
        logger.info("Starting bot with polling...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        webhook_path = f"/{TELEGRAM_BOT_TOKEN}"
        full_webhook_url = f"{RENDER_URL}{webhook_path}"

        logger.info(f"Starting webhook server on 0.0.0.0:{PORT}...")
        
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=webhook_path,
            webhook_url=full_webhook_url,
            allowed_updates=Update.ALL_TYPES
        )

if __name__ == "__main__":
    main()


