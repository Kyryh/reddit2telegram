class RedditSubmission:
    def __init__(self, title: str, id: str, score: int, flair: str | None, text: str, spoiler: bool, nsfw: bool):
        self.title = title
        self.id = id
        self.score = score
        self.flair = flair
        self.text = text
        self.post_url = "https://redd.it/" + id
        self.spoiler = spoiler
        self.nsfw = nsfw
        self.data: 'RedditData' = None

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
