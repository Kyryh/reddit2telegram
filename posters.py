from reddit_types import RedditSubmission
from html import escape
from telegram.constants import MessageLimit 
import textwrap

class Poster:
    subreddits: list[str]
    chat: str | int
    limit: int
    sort_by: str

    def __init__(self, submission: RedditSubmission) -> None:
        self.submission = submission

    def should_post(self):
        return True

    def should_hide(self):
        return self.submission.spoiler or self.submission.nsfw
    
    def get_text(self, short = False):
        submission = self.submission
        text = "🔞NSFW🔞\n" if submission.nsfw else ""
        if submission.text:
            text += f"<b>{escape(submission.title)}</b>"
            selftext = textwrap.shorten(
                    submission.text,
                    MessageLimit.CAPTION_LENGTH-128,
                    replace_whitespace = False
                ) if short else submission.text

            if self.should_hide():
                text += f"\n\n<tg-spoiler>{selftext}</tg-spoiler>"
            else:
                text += f"\n\n{selftext}"
        else:
            text += escape(submission.title)

        text += f"\n\n{escape(submission.post_url)}"
        return text

class NSFWPoster(Poster):
    def should_hide(self):
        return self.submission.spoiler
    
    def get_text(self, short = False):
        return super().get_text(short).removeprefix("🔞NSFW🔞\n")
