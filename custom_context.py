import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine
from httpx import AsyncClient
from telegram.ext import Application, CallbackContext, ExtBot
from telegram.error import BadRequest
from telegram.constants import MessageLimit 
from telegram import InputMediaPhoto, InputMediaVideo, Message
from html import unescape as unescape_html
import textwrap
import re
import json
import subprocess
import logging
import os
from reddit_types import *

ffmpeg_logger = logging.getLogger("ffmpeg")
logger = logging.getLogger("bot")

__import__("dotenv").load_dotenv()

REDDIT_CLIENT_ID=os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET=os.getenv("REDDIT_CLIENT_SECRET")

def ffmpeg_installed():
    try:
        subprocess.run(["ffmpeg", "-v", "quiet"])
        return True
    except FileNotFoundError:
        return False

def chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


class RedditContext(CallbackContext[ExtBot, dict, dict, dict]):
    __base_headers = {"User-Agent": "kyryh/reddit2telegram"}

    __telegram_html_tags = [
        "b", "strong", "i", "em",
        "u", "ins", "s", "strike",
        "del", "span", "a", "code",
        "pre", "code", "blockquote"
    ]

    def __init__(self, application: Application, chat_id: int | None = None, user_id: int | None = None):
        super().__init__(application, chat_id, user_id)
        self.client = AsyncClient()
        self.access_token: str | None = None

    @property
    def headers(self):
        return (self.__base_headers | {"Authorization": f"bearer {self.access_token}"}
                if self.access_token else self.__base_headers)

    async def update_access_token(self):
        if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
            req = await self.client.post(
                "https://www.reddit.com/api/v1/access_token",
                data={"grant_type": "client_credentials"},
                auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET)
            )
            self.access_token = req.json()["access_token"]

    async def get_subreddits_info(self, subreddits: str) -> list[dict]:
        await self.update_access_token()
        if self.access_token:
            req = await self.client.get(f"https://oauth.reddit.com/api/info.json?sr_name={subreddits.replace('+', ',')}", headers=self.headers)
        else:
            req = await self.client.get(f"https://www.reddit.com/api/info.json?sr_name={subreddits.replace('+', ',')}", headers=self.headers)
        return [subreddit["data"] for subreddit in req.json()["data"]["children"]]

    async def all_subreddits_nsfw(self, subreddits: str):
        return all(subreddit.get("over18") for subreddit in await self.get_subreddits_info(subreddits))


    async def get_subreddit_submissions_raw(self, subreddit: str, limit: int, sort_by: str = "hot") -> list[dict]:
        await self.update_access_token()
        if self.access_token:
            req = await self.client.get(f"https://oauth.reddit.com/r/{subreddit}/{sort_by}?limit={limit}&raw_json=1", headers=self.headers)
        else:
            req = await self.client.get(f"https://www.reddit.com/r/{subreddit}/{sort_by}.json?limit={limit}&raw_json=1", headers=self.headers)
        req.raise_for_status()
        data = req.json()
        submissions = [submission["data"] for submission in data["data"]["children"]]
        return submissions
    
    async def get_submission_raw(self, submission_id: str) -> dict:
        await self.update_access_token()
        if self.access_token:
            req = await self.client.get(f"https://oauth.reddit.com/comments/{submission_id}?raw_json=1", headers=self.headers)
        else:
            req = await self.client.get(f"https://www.reddit.com/comments/{submission_id}.json?raw_json=1", headers=self.headers)
        req.raise_for_status()
        return req.json()[0]["data"]["children"][0]["data"]

    async def get_subreddit_submissions(self, subreddit: str, limit: int, sort_by: str = "hot") -> list[RedditSubmission]:
        return [await self.parse_submission(s) for s in await self.get_subreddit_submissions_raw(subreddit, limit, sort_by)]
    
    async def get_submission(self, submission_id: str) -> RedditSubmission:
        return await self.parse_submission(await self.get_submission_raw(submission_id))
    
    async def get_media_size(self, url: str):
        return int((await self.client.head(url)).headers["Content-Length"])
    
    async def parse_submission(self, s: dict) -> RedditSubmission:
        if s.get("removed_by_category"):
            raise Exception("The post has been deleted")
        submission = RedditSubmission(
            s["title"],
            s["id"],
            self.parse_selftext(s["selftext_html"] or ""),
            s["spoiler"],
            s["over_18"]
        )

        og_s = s
        if 'crosspost_parent_list' in s:
            s = s['crosspost_parent_list'][0]

        if s["is_video"]:
            req = (
                await self.client.get(
                    f"https://www.reddit.com{s['permalink']}",
                    headers=self.headers
                )
            ).text
            re_match = re.search(r'<shreddit-player.*packaged-media-json="(.*?)".*<\/shreddit-player>', req, re.S)
            if re_match:
                
                video_urls = json.loads(
                    unescape_html(
                        re_match.group(1)
                    )
                )

                submission.data = RedditVideo(
                    [
                        video["source"]["url"]
                        for video in video_urls["playbackMp4s"]["permutations"][::-1]
                    ],
                    s["media"]["reddit_video"]["width"],
                    s["media"]["reddit_video"]["height"],
                    s["media"]["reddit_video"]["duration"],
                    s["preview"]["images"][0]["resolutions"][-1]["url"]
                )

            elif ffmpeg_installed():
                video_info = await self.client.get(s["media"]["reddit_video"]["dash_url"], headers=self.headers)
                video_urls = []
                audio_urls = []
                for format in re.findall(r'<BaseURL>(.*?)<\/BaseURL>', video_info.text)[::-1]:
                    url = f"{s['url']}/{format}"
                    if "AUDIO" in format:
                        audio_urls.append(url)
                    else:
                        video_urls.append(url)
                video_urls.insert(0, s["media"]["reddit_video"]["fallback_url"])

                video = audio = None
                for audio_url in audio_urls:
                    if video is not None:
                        break
                    audio_size = await self.get_media_size(audio_url)
                    for video_url in video_urls:
                        if await self.get_media_size(video_url) + audio_size < 50_000_000:
                            video = video_url
                            audio = audio_url
                            break

                result = subprocess.run(
                    ["ffmpeg", "-i", video, "-i", audio, "-y", "-v", "warning", "-c", "copy", "video.mp4"],
                    capture_output=True,
                    text=True
                )
                output = f"{result.stdout}\n{result.stderr}"
                if not output.isspace():
                    ffmpeg_logger.info(output)
                result.check_returncode()

                if s["preview"]["images"][0]["resolutions"]:
                    thumb = s["preview"]["images"][0]["resolutions"][-1]
                else:
                    thumb = s["preview"]["images"][0]["source"]
            

                with open("video.mp4", "rb") as f:
                    submission.data = RedditVideo(
                        [f.read()],
                        s["media"]["reddit_video"]["width"],
                        s["media"]["reddit_video"]["height"],
                        s["media"]["reddit_video"]["duration"],
                        thumb["url"]
                    )
                os.remove("video.mp4")
            else:
                raise Exception("The video is too big and ffmpeg was not found")
            
        elif s["url"].endswith(".mp4"):
            submission.data = RedditVideo(
                [s["url"]]
            )
        elif s.get('is_gallery'):
            gallery: list[RedditGalleryMedia] = []
            for media in s["gallery_data"]["items"]:
                media_id = media["media_id"]
                metadata = s["media_metadata"][media_id]
                gallery.append(RedditGalleryMedia(
                    media = (
                        metadata["s"].get("gif") or
                        metadata["s"].get("u")
                    ),
                    media_lower = 
                        metadata["s"].get("mp4") or (
                            metadata["p"][-1].get("u")
                            if metadata["p"] else None
                        ),
                    type = metadata["e"],
                    caption = media.get("caption", "")
                ))
            submission.data = RedditGallery(
                gallery
            )
        elif any(s["url"].endswith(ext) for ext in (".gif", ".gifv")):
            if "reddit_video_preview" in s["preview"]:
                gifs = [s["preview"]["reddit_video_preview"]["fallback_url"]]
            else:
                gifs: list = [gif["url"] for gif in s["preview"]["images"][0]["variants"]["gif"]["resolutions"]]
                gifs.append(s["preview"]["images"][0]["variants"]["gif"]["source"]["url"])

            if s["preview"]["images"][0]["resolutions"]:
                thumb = s["preview"]["images"][0]["resolutions"][-1]
            else:
                thumb = s["preview"]["images"][0]["source"]
            
            submission.data = RedditGif(
                gifs[::-1],
                thumb["width"],
                thumb["height"],
                thumb["url"]
            )
        elif s.get("url_overridden_by_dest", "").startswith("https://i.redd"):
            if "preview" in og_s:
                images: list = [img["url"] for img in og_s["preview"]["images"][0]["resolutions"]]
                images.append(og_s["preview"]["images"][0]["source"]["url"])
            else:
                images = [s["url_overridden_by_dest"]]
            submission.data = RedditImage(
                images[::-1]
            )
        elif not s["is_self"]:
            submission.text += f"\n\n{s['url'].strip()}"

        submission.text = submission.text.strip()

        return submission

    def parse_selftext(self, selftext_html: str):
        matches: list[str] = re.findall(r"<(.*?)>", selftext_html)
        for match in matches:
            if not any(match.split(" ")[0] in (tag, "/"+tag) for tag in self.__telegram_html_tags):
                selftext_html = selftext_html.replace(f"<{match}>", "")
        selftext_html = selftext_html.replace('<span class="md-spoiler-text">', '<span class="tg-spoiler">')
        return selftext_html
    
    @staticmethod
    def fix_tags_single(text: str) -> tuple[str, str]:
        tags: list[str] = re.findall(r"<(.*?)>", text)
        tags_count = defaultdict(int)
        last_tags = defaultdict(list)
        for tag in tags:
            effective_tag = tag.replace("/", "").split(" ")[0]
            if tag[0] != "/":
                last_tags[effective_tag].append(tag)
                tags_count[effective_tag] += 1
            else:
                tags_count[effective_tag] -= 1

        next_text_prefix = ""
        for tag, count in tags_count.items():
            if count > 0:
                text += "</"+tag*count+">"
                next_text_prefix += "<"+last_tags[tag].pop()+">"
        
        return text, next_text_prefix
    
    @staticmethod
    def fix_tags_multiple(texts: list[str]) -> list[str]:
        new_texts = list(texts)
        text_prefix = ""
        for i in range(len(new_texts)):
            new_texts[i], text_prefix = RedditContext.fix_tags_single(text_prefix+new_texts[i])
        return new_texts

    async def send_media(self, bot_method: Callable[..., Coroutine[Any, Any, Message]], chat_id: int | str, media: list[str | bytes], **kwargs):
        index = 0
        current_media = media[0]
        filename = None
        while index < len(media):
            try:
                message = await bot_method(
                    chat_id,
                    current_media,
                    **kwargs,
                    parse_mode="HTML",
                    show_caption_above_media=True,
                    filename=filename
                )
                return message
            except BadRequest as e:
                if e.message not in [
                        "Wrong file identifier/http url specified",
                        "Wrong type of the web page content",
                        "Failed to get http url content",
                        "Photo_invalid_dimensions"
                    ]:
                    raise e
                if isinstance(current_media, str) and await self.get_media_size(current_media) < 50_000_000:
                    current_media = await self.client.get(current_media)
                    filename = current_media.url.path.split("/")[-1]
                else:
                    index += 1
                    current_media = media[index]
        return None


    async def send_reddit_post(self, chat_id: int, submission: RedditSubmission, hide_nsfw = True, extra_text: str = None):
        if not submission.data:
            texts = textwrap.wrap(submission.get_text(hide_nsfw=hide_nsfw, extra_text=extra_text), MessageLimit.MAX_TEXT_LENGTH, fix_sentence_endings = False, replace_whitespace = False)
            texts = self.fix_tags_multiple(texts)
            for text in texts:
                await self.bot.send_message(
                    chat_id = chat_id,
                    text = text,
                    parse_mode = "HTML"
                )
        elif isinstance(submission.data, RedditImage):
            image_sent = await self.send_media(
                self.bot.send_photo,
                chat_id,
                submission.data.resolutions,
                caption = submission.get_text(hide_nsfw=hide_nsfw, short=True, extra_text=extra_text),
                has_spoiler = submission.should_hide(hide_nsfw),
            )
            if not image_sent:
                logger.warning("Failed sending image as photo, sending it as url instead")
                submission.text += "\n\n" + submission.data.resolutions[0]
                submission.data = None
                await self.send_reddit_post(chat_id, submission)
                
        elif isinstance(submission.data, RedditVideo):
            video_sent = await self.send_media(
                self.bot.send_video,
                chat_id,
                submission.data.resolutions,
                duration = submission.data.duration,
                caption = submission.get_text(hide_nsfw=hide_nsfw, short=True, extra_text=extra_text),
                width = submission.data.width,
                height = submission.data.height,
                supports_streaming = True,
                has_spoiler = submission.should_hide(hide_nsfw),
                thumbnail = (await self.client.get(submission.data.thumbnail)).content if submission.data.thumbnail else None
            )
            if not video_sent:
                raise Exception("something happened idk what (video)")

        elif isinstance(submission.data, RedditGif):
            gif_sent = await self.send_media(
                self.bot.send_animation,
                chat_id,
                submission.data.resolutions,
                caption = submission.get_text(hide_nsfw=hide_nsfw, short=True, extra_text=extra_text),
                has_spoiler = submission.should_hide(hide_nsfw),
                width = submission.data.width,
                height = submission.data.height,
                thumbnail = (await self.client.get(submission.data.thumbnail)).content if submission.data.thumbnail else None
            )
            if not gif_sent:
                raise Exception("something happened idk what (gif)")
            
        elif isinstance(submission.data, RedditGallery):
            await self.bot.send_message(
                chat_id = chat_id,
                text = submission.get_text(hide_nsfw=hide_nsfw, extra_text=extra_text),
                parse_mode="HTML"
            )
            gallery = []
            gallery_lower = [] 
            for item in submission.data.items:
                match item.type:
                    case "Image":
                        InputMedia = InputMediaPhoto
                    case "AnimatedImage":
                        InputMedia = InputMediaVideo
                    case _:
                        raise Exception(f"Unsupported gallery media type ({item.type})")

                gallery.append(
                    InputMedia(
                        item.media if item.type == "Image" else
                        (await self.client.get(item.media)).content,
                        item.caption,
                        has_spoiler=submission.should_hide(hide_nsfw)
                    )
                )
                if item.media_lower is not None:
                    gallery_lower.append(
                        InputMedia(
                            item.media_lower if item.type == "Image" else
                            (await self.client.get(item.media_lower)).content,
                            item.caption,
                            has_spoiler=submission.should_hide(hide_nsfw)
                        )
                    )

            try:
                for media_group in chunks(gallery, 10):
                    await self.bot.send_media_group(
                        chat_id = chat_id,
                        media = media_group
                    )

            except BadRequest as e:
                if not re.match(r"Failed to send message #\d+ with the error message \".*\"", e.message):
                    raise e
                for media_group in chunks(gallery_lower, 10):
                    await self.bot.send_media_group(
                        chat_id = chat_id,
                        media = media_group
                    )

async def main():
    pass

if __name__ == "__main__":
    asyncio.run(main())
