from telegram.ext import ContextTypes, Application, PicklePersistence, filters, CommandHandler, MessageHandler, Defaults, BaseRateLimiter
from telegram import Update, LinkPreviewOptions
import logging
import asyncio
from datetime import datetime, timedelta
import pytz
from custom_context import RedditContext
import json
import traceback
from os import getenv
from ratelimiter import RateLimiter
from collections import defaultdict 


__import__("dotenv").load_dotenv()

TOKEN = getenv("TOKEN")
OWNER_USER_ID = getenv("OWNER_USER_ID")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs.log")
    ]
)

logging.getLogger("httpx").setLevel(logging.WARNING)

settings = json.load(open("settings.json"))

def datetime_round(dt: datetime, minutes: int) -> datetime:
    minutes_delta = timedelta(minutes=minutes)
    return dt + (minutes_delta - (timedelta(minutes=dt.minute, seconds=dt.second, microseconds=dt.microsecond) % minutes_delta))


async def reddit_post(update: Update, context: RedditContext):
    if not context.args:
        await update.effective_message.reply_text("Syntax:\n/reddit <post_id>")
        return
    submission = await context.get_submission(context.args[0])
    try:
        await context.send_reddit_post(update.effective_chat.id, submission)
    except Exception as e:
        await context.bot.send_message(chat_id = OWNER_USER_ID, text = f"{e} in post {submission.id}")
        logging.error(traceback.format_exc())



async def reddit_posts(update: Update, context: RedditContext):
    if not context.args:
        await update.effective_message.reply_text("Syntax:\n/reddit <subreddit> <number_of_posts (optional)>")
        return
    submissions = await context.get_subreddit_submissions(context.args[0], context.args[1] if len(context.args) > 1 else 10)
    for submission in submissions:
        try:
            await context.send_reddit_post(update.effective_chat.id, submission)
        except Exception as e:
            await context.bot.send_message(chat_id = OWNER_USER_ID, text = f'{e} in post {submission.id}')
            logging.error(traceback.format_exc())
    


async def reddit_on_channel(context: RedditContext):
    for channel in settings["channels"]:
        for submission in await context.get_subreddit_submissions(channel["subreddits"], channel["limit"], channel["sort_by"]):
            try:
                if submission.id not in context.bot_data["sent_submissions"][channel["channel"]]:
                    await context.send_reddit_post(channel["channel"], submission)
                    context.bot_data["sent_submissions"][channel["channel"]].append(submission.id)
                    await asyncio.sleep(5)
            except Exception as e:
                await context.bot.send_message(chat_id = OWNER_USER_ID, text = f'{e} in post {submission.id}')
                logging.error(traceback.format_exc())


async def manual_reddit_on_channel(update: Update, context: RedditContext):
    await reddit_on_channel(context)


async def unpinner(update: Update, context: RedditContext):
    group_chats = [(await context.bot.get_chat(channel["channel"])).linked_chat_id for channel in settings["channels"]]
    print(group_chats)
    if update.effective_chat.id in group_chats:
        await update.effective_message.unpin()

async def post_init(application: Application):
    application.bot_data.setdefault("sent_submissions", defaultdict(list))



def main():
    application = (
        Application.builder()
        .token(TOKEN)
        .defaults(
            Defaults(
                link_preview_options = LinkPreviewOptions(
                    True
                )
            )
        )
        .persistence(
            PicklePersistence(
                "persistence.pickle"
            )
        )
        .write_timeout(300)
        .read_timeout(30)
        .context_types(ContextTypes(RedditContext))
        .rate_limiter(RateLimiter())
        .post_init(post_init)
        .build()
    )
    

    job = application.job_queue
    
    application.add_handler(CommandHandler('reddit', reddit_post, filters.User(266949564)))
    application.add_handler(CommandHandler('reddits', reddit_posts, filters.User(266949564)))
    application.add_handler(CommandHandler('manual_reddit_on_channel', manual_reddit_on_channel, filters.User(266949564)))
    application.add_handler(MessageHandler(filters.IS_AUTOMATIC_FORWARD, unpinner))

    job.run_repeating(reddit_on_channel, interval=settings["interval"]*60, first=datetime_round(datetime.now(pytz.UTC), settings["interval"]))

    application.run_polling() 


if __name__ == '__main__':
    main()
