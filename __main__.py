from telegram.ext import ContextTypes, Application, PicklePersistence, filters, CommandHandler, MessageHandler, Defaults
from telegram import Update, LinkPreviewOptions
from telegram.error import BadRequest
import logging
import asyncio
from datetime import datetime, timedelta
import pytz
from custom_context import RedditContext
import traceback
from os import getenv
from ratelimiter import RateLimiter
from collections import defaultdict 
from base_posters import Poster, NSFWPoster, get_channel_posters

__import__("dotenv").load_dotenv()

TOKEN = getenv("TOKEN")
OWNER_USER_ID = int(getenv("OWNER_USER_ID"))

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs.log")
    ]
)

logging.getLogger("httpx").setLevel(logging.WARNING)

channel_posters = get_channel_posters()

def datetime_round(dt: datetime, minutes: int) -> datetime:
    minutes_delta = timedelta(minutes=minutes)
    return dt + (minutes_delta - (timedelta(minutes=dt.minute, seconds=dt.second, microseconds=dt.microsecond) % minutes_delta))


async def reddit_post(update: Update, context: RedditContext):
    if not context.args:
        await update.effective_message.reply_text("Syntax:\n/reddit <post_id>")
        return
    nsfw = await context.all_subreddits_nsfw(context.args[0])
    submissions = await context.get_submission_raw(context.args[0])
    await send_reddit(update.effective_chat.id, submissions, context, NSFWPoster if nsfw else Poster)



async def reddit_posts(update: Update, context: RedditContext):
    if not context.args:
        await update.effective_message.reply_text("Syntax:\n/reddit <subreddit> <number_of_posts (optional)>")
        return
    submissions = await context.get_subreddit_submissions_raw(context.args[0], context.args[1] if len(context.args) > 1 else 10)
    nsfw = await context.all_subreddits_nsfw(context.args[0])
    for submission in submissions:
        await send_reddit(update.effective_chat.id, submission, context, NSFWPoster if nsfw else Poster)
    


async def reddit_on_channel(context: RedditContext):
    for poster in channel_posters:
        for submission in await context.get_subreddit_submissions_raw(poster.subreddits, poster.limit, poster.sort_by):
            if submission["id"] not in context.bot_data["sent_submissions"][poster.chat]:
                await send_reddit(poster.chat, submission, context, poster)
                context.bot_data["sent_submissions"][poster.chat].append(submission["id"])
                await asyncio.sleep(5)


async def send_reddit(chat_id: str | int, submission: dict, context: RedditContext, poster: type[Poster]):
    try:
        submission_poster = poster(await context.parse_submission(submission))
        if submission_poster.should_post():
            await context.send_reddit_post(chat_id, submission_poster)
    except Exception as e:
        await context.bot.send_message(chat_id = OWNER_USER_ID, text = f'{repr(e)} in post {submission["id"]}')
        logging.error(traceback.format_exc())


async def manual_reddit_on_channel(update: Update, context: RedditContext):
    await reddit_on_channel(context)


async def unpinner(update: Update, context: RedditContext):
    if update.effective_chat.id in context.bot_data["group_chats"]:
        try:
            await update.effective_message.unpin()
        except BadRequest:
            pass

async def post_init(application: Application):
    application.bot_data.setdefault("sent_submissions", defaultdict(list))
    application.bot_data["group_chats"] = [(await application.bot.get_chat(poster.chat)).linked_chat_id for poster in channel_posters]

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
    
    application.add_handler(CommandHandler('reddit', reddit_post, filters.User(OWNER_USER_ID)))
    application.add_handler(CommandHandler('reddits', reddit_posts, filters.User(OWNER_USER_ID)))
    application.add_handler(CommandHandler('manual_reddit_on_channel', manual_reddit_on_channel, filters.User(OWNER_USER_ID)))
    application.add_handler(MessageHandler(filters.IS_AUTOMATIC_FORWARD, unpinner))

    minutes_interval = 30

    job.run_repeating(reddit_on_channel, interval=minutes_interval*60, first=datetime_round(datetime.now(pytz.UTC), minutes_interval))

    application.run_polling() 


if __name__ == '__main__':
    main()
