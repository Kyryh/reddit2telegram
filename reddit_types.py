from html import escape
from telegram.constants import MessageLimit 
import textwrap
import re

class RedditSubmission:
    def __init__(self, title: str, id: str, score: int, text: str, spoiler: bool, nsfw: bool):
        self.title = title
        self.id = id
        self.score = score
        self.text = text
        self.post_url = "https://redd.it/" + id
        self.spoiler = spoiler
        self.nsfw = nsfw
        self.data: 'RedditData' = None

    def should_hide(self, hide_nsfw = True):
        return self.spoiler or (self.nsfw and hide_nsfw)

    def get_text(self, hide_nsfw = True, short = False, extra_text: str = None):
        text = "🔞NSFW🔞\n" if self.nsfw and hide_nsfw else ""
        text += f"<b>{escape(self.title)}</b>" if self.text else escape(self.title)
        if self.text:
            selftext = textwrap.shorten(
                    self.text,
                    MessageLimit.CAPTION_LENGTH-128,
                    replace_whitespace = False
                ) if short else self.text

            if self.should_hide(hide_nsfw):
                text += f"\n\n<tg-spoiler>{selftext}</tg-spoiler>"
            else:
                text += f"\n\n{selftext}"
        text += f"\n\n{escape(self.post_url)}"
        if extra_text:
            text += "\n"+self.format_extra_text(extra_text)
        return text
    
    def format_extra_text(self, extra_text: str) -> str:
        def parser(m: re.Match[str]) -> str:
            return eval(m.group(1), {"self": self})
        return re.sub(r"{(.*?)}", parser, extra_text, flags=re.S)

class RedditData:
    pass

class RedditVideo(RedditData):
    def __init__(self, resolutions: list[str | bytes], width: int = None, height: int = None, duration: int = None, thumbnail: str = None):
        super().__init__()
        self.resolutions = resolutions
        self.width = width
        self.height = height
        self.duration = duration
        self.thumbnail = thumbnail

class RedditGallery(RedditData):
    def __init__(self, items: list['RedditGalleryMedia']):
        super().__init__()
        self.items = items

class RedditGalleryMedia:
    def __init__(self, media: str, media_lower: str, type: str, caption: str):
        self.media = media
        self.media_lower = media_lower
        self.type = type
        self.caption = caption

class RedditGif(RedditData):
    def __init__(self, resolutions: list[str], width: int = None, height: int = None, thumbnail: str = None):
        self.resolutions = resolutions
        self.width = width
        self.height = height
        self.thumbnail = thumbnail

class RedditImage(RedditData):
    def __init__(self, resolutions: list[str]):
        self.resolutions = resolutions
