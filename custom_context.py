import asyncio
from httpx import AsyncClient
from telegram.ext import Application, CallbackContext, ExtBot
from telegram.error import BadRequest
from telegram import InputMediaPhoto, InputMediaVideo
from html import escape as escape_html, unescape as unescape_html
import textwrap
import re
import json
import subprocess
import logging
import os
from reddit_types import *

ffmpeg_logger = logging.getLogger("ffmpeg")
logger = logging.getLogger("bot")


def ffmpeg_installed():
    try:
        subprocess.run('ffmpeg -v quiet')
        return True
    except FileNotFoundError:
        return False

def chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


class RedditContext(CallbackContext[ExtBot, dict, dict, dict]):
    headers = {"User-Agent": "kyryh/reddit2telegram"}

    def __init__(self, application: Application, chat_id: int | None = None, user_id: int | None = None):
        super().__init__(application, chat_id, user_id)
        self.client = AsyncClient()

    async def __get_subreddit_submissions_raw(self, subreddit: str, limit: int, sort_by: str = "hot") -> list[dict]:
        data = (await self.client.get(f"https://www.reddit.com/r/{subreddit}/{sort_by}.json?limit={limit}&raw_json=1", headers=self.headers)).json()
        submissions = [submission["data"] for submission in data["data"]["children"]]
        return submissions
    
    async def __get_submission_raw(self, submission_id: str) -> dict:
        req = await self.client.get(f"https://www.reddit.com/{submission_id}.json?raw_json=1", headers=self.headers)
        return (await self.client.send(req.next_request)).json()[0]["data"]["children"][0]["data"]

    async def get_subreddit_submissions(self, subreddit: str, limit: int, sort_by: str = "hot") -> list[RedditSubmission]:
        return [await self.__parse_submission(s) for s in await self.__get_subreddit_submissions_raw(subreddit, limit, sort_by)]
    
    async def get_submission(self, submission_id: str) -> RedditSubmission:
        return await self.__parse_submission(await self.__get_submission_raw(submission_id))
    
    async def get_media_size(self, url: str):
        return int((await self.client.head(url)).headers["Content-Length"])
    
    async def __parse_submission(self, s: dict) -> RedditSubmission:
        submission = RedditSubmission(
            s["title"],
            s["id"],
            s["selftext"].strip(),
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
                    video["source"]["url"]
                    for video in video_urls["playbackMp4s"]["permutations"][::-1]
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
                    f'ffmpeg -i "{video}" -i "{audio}" -y -v warning -c copy video.mp4',
                    capture_output=True,
                    text=True
                )
                output = f"{result.stdout}\n{result.stderr}"
                if not output.isspace():
                    ffmpeg_logger.info(output)
                result.check_returncode()

                thumb = re.search(r'<shreddit-player.*poster="(.*?)".*<\/shreddit-player>', req, re.S).group(1)

                with open("video.mp4", "rb") as f:
                    submission.data = RedditVideo(
                        [f.read()],
                        s["media"]["reddit_video"]["width"],
                        s["media"]["reddit_video"]["height"],
                        s["media"]["reddit_video"]["duration"],
                        unescape_html(thumb)
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
                        metadata["s"].get("u") or
                        metadata["s"].get("gif") or
                        metadata["s"].get("mp4")
                    ),
                    media_lower = (
                        metadata["p"][-1].get("u") or
                        metadata["p"][-1].get("gif") or
                        metadata["p"][-1].get("mp4")
                    ) if metadata["p"] else None,
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
            submission.data = RedditGif(gifs)
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


    async def send_reddit_post(self, chat_id: int, submission: RedditSubmission):
        if not submission.data:

            entire_text = "🔞NSFW🔞\n" if submission.nsfw else ""
            entire_text += f"<b>{escape_html(submission.title)}</b>\n\n"
            if submission.should_hide():
                entire_text += "<tg-spoiler>"
            entire_text += escape_html(submission.text)
            if submission.should_hide():
                entire_text += "</tg-spoiler>"
            entire_text += "\n\n" + escape_html(submission.post_url)

            texts = textwrap.wrap(entire_text, 4000, fix_sentence_endings = False, replace_whitespace = False)
            if len(texts) > 1 and submission.should_hide():
                texts[0] += "</tg-spoiler>"
                texts[-1] = "<tg-spoiler>" + texts[-1]
                for i in range(1, len(texts)-1):
                    text[i] = "<tg-spoiler>" + texts[i] + "</tg-spoiler>"
            for text in texts:
                await self.bot.send_message(
                    chat_id = chat_id,
                    text = text,
                    parse_mode = "HTML"
                )
        elif isinstance(submission.data, RedditImage):
            image_sent = False
            for image in submission.data.resolutions:
                try:
                    await self.bot.send_photo(
                        chat_id = chat_id,
                        photo = image,
                        caption = submission.get_text(),
                        has_spoiler = submission.should_hide(),
                        parse_mode="HTML",
                        show_caption_above_media=True
                    )
                    image_sent = True
                    break
                except BadRequest as e:
                    if e.message not in ["Wrong file identifier/http url specified", "Wrong type of the web page content", "Failed to get http url content"]:
                        raise e
            if not image_sent:
                logger.warning("Failed sending image as photo, sending it as url instead")
                submission.text += "\n\n" + submission.data.resolutions[0]
                submission.data = None
                await self.send_reddit_post(chat_id, submission)
                
        elif isinstance(submission.data, RedditVideo):
            video_sent = False
            for video in submission.data.resolutions:
                try:
                    await self.bot.send_video(
                        chat_id = chat_id,
                        video = video,
                        duration = submission.data.duration,
                        caption = submission.get_text(),
                        width = submission.data.width,
                        height = submission.data.height,
                        parse_mode = "HTML",
                        supports_streaming = True,
                        has_spoiler = submission.should_hide(),
                        thumbnail = (await self.client.get(submission.data.thumbnail)).content if submission.data.thumbnail else None,
                        show_caption_above_media=True
                    )
                    video_sent = True
                    break
                except BadRequest as e:
                    if e.message not in ["Wrong file identifier/http url specified", "Wrong type of the web page content", "Failed to get http url content"]:
                        raise e
                    continue
            if not video_sent:
                raise Exception("something happened idk what (video)")

        elif isinstance(submission.data, RedditGif):
            gif_sent = False
            for gif in submission.data.resolutions:
                try:
                    await self.bot.send_animation(
                        chat_id = chat_id,
                        animation = gif,
                        caption = submission.get_text(),
                        has_spoiler = submission.should_hide(),
                        parse_mode="HTML",
                        show_caption_above_media=True
                    )
                    gif_sent = True
                    break
                except BadRequest as e:
                    if e.message not in ["Wrong file identifier/http url specified", "Wrong type of the web page content", "Failed to get http url content"]:
                        raise e
                    continue
            if not gif_sent:
                raise Exception("something happened idk what (gif)")
            
        elif isinstance(submission.data, RedditGallery):
            await self.bot.send_message(
                chat_id = chat_id,
                text = submission.get_text(),
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
                        #(await self.client.get(item.media)).content,
                        item.media,
                        item.caption,
                        has_spoiler=submission.should_hide()
                    )
                )
                if item.media_lower is not None:
                    gallery_lower.append(
                        InputMedia(
                            #(await self.client.get(item.media_lower)).content,
                            item.media_lower,
                            item.caption,
                            has_spoiler=submission.should_hide()
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