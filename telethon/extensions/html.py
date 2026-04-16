"""
Simple HTML -> Telegram entity parser.
"""
import html
import logging
import re
from html import escape
from html.parser import HTMLParser
from typing import Iterable, Tuple, List

from ..helpers import add_surrogate, del_surrogate
from ..tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode,
    MessageEntityPre, MessageEntityEmail, MessageEntitySpoiler,
    MessageEntityTextUrl, MessageEntityMentionName,
    MessageEntityUnderline, MessageEntityStrike, MessageEntityBlockquote,
    MessageEntityCustomEmoji, MessageEntityFormattedDate, TypeMessageEntity
)

log = logging.getLogger(__name__)


class Parser(HTMLParser):
    MENTION_RE = re.compile(r"tg://user\?id=(\d+)")

    def __init__(self):
        super().__init__()

        self.text = ""
        self.entities = []
        self.tag_entities = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        extra = {}

        if tag in ["b", "strong"]:
            entity = MessageEntityBold
        elif tag in ["i", "em"]:
            entity = MessageEntityItalic
        elif tag in ["u", "ins"]:
            entity = MessageEntityUnderline
        elif tag in ["s", "del", "strike"]:
            entity = MessageEntityStrike
        elif tag == "blockquote":
            entity = MessageEntityBlockquote
            extra["collapsed"] = "expandable" in attrs
        elif tag == "code":
            entity = MessageEntityCode
        elif tag == "pre":
            entity = MessageEntityPre
            extra["language"] = attrs.get("language", "")
        elif tag in ["spoiler", "tg-spoiler"]:
            entity = MessageEntitySpoiler
        elif tag == "a":
            url = attrs.get("href", "")

            mention = Parser.MENTION_RE.match(url)

            if mention:
                entity = MessageEntityMentionName
                extra["user_id"] = int(mention.group(1))
            elif url.startswith("mailto:"):
                entity = MessageEntityEmail
            else:
                entity = MessageEntityTextUrl
                extra["url"] = url
        elif tag in ["emoji", "tg-emoji"]:
            entity = MessageEntityCustomEmoji
            custom_emoji_id = attrs.get("emoji-id") if tag == "tg-emoji" else attrs.get("id")
            extra["document_id"] = int(custom_emoji_id)
        elif tag == "tg-time":
            entity = MessageEntityFormattedDate
            extra["date"] = int(attrs.get("unix"))
            date_time_format = attrs.get("format", "")

            extra["relative"] = False
            extra["short_time"] = False
            extra["long_time"] = False
            extra["short_date"] = False
            extra["long_date"] = False
            extra["day_of_week"] = False

            if date_time_format:
                if not re.fullmatch(r"r|w?[dD]?[tT]?", date_time_format):
                    raise ValueError(f"Invalid date-time format string: '{date_time_format}'")

                if date_time_format == "r":
                    extra["relative"] = True
                else:
                    if "w" in date_time_format:
                        extra["day_of_week"] = True

                    if "d" in date_time_format:
                        extra["short_date"] = True
                    elif "D" in date_time_format:
                        extra["long_date"] = True

                    if "t" in date_time_format:
                        extra["short_time"] = True
                    elif "T" in date_time_format:
                        extra["long_time"] = True
        else:
            return

        if tag not in self.tag_entities:
            self.tag_entities[tag] = []

        self.tag_entities[tag].append(entity(offset=len(self.text), length=0, **extra))

    def handle_data(self, data):
        data = html.unescape(data)

        for entities in self.tag_entities.values():
            for entity in entities:
                entity.length += len(data)

        self.text += data

    def handle_endtag(self, tag):
        try:
            self.entities.append(self.tag_entities[tag].pop())
        except (KeyError, IndexError):
            line, offset = self.getpos()
            offset += 1

            log.debug("Unmatched closing tag </%s> at line %s:%s", tag, line, offset)
        else:
            if not self.tag_entities[tag]:
                self.tag_entities.pop(tag)

    def error(self, message):
        pass


def parse(html: str) -> Tuple[str, List[TypeMessageEntity]]:
    """
    Parses the given HTML message and returns its stripped representation
    plus a list of the MessageEntity's that were found.

    :param html: the message with HTML to be parsed.
    :return: a tuple consisting of (clean message, [message entities]).
    """
    if not html:
        return html, []

    # Strip whitespaces from the beginning and the end, but preserve closing tags
    text = re.sub(r"^\s*(<[\w<>=\s\"]*>)\s*", r"\1", html)
    text = re.sub(r"\s*(</[\w</>]*>)\s*$", r"\1", text)

    parser = Parser()
    parser.feed(add_surrogate(text))
    parser.close()

    if parser.tag_entities:
        unclosed_tags = []

        for tag, entities in parser.tag_entities.items():
            unclosed_tags.append(f"<{tag}> (x{len(entities)})")

        log.info("Unclosed tags: %s", ", ".join(unclosed_tags))

    entities = parser.entities

    # Remove zero-length entities
    entities = list(filter(lambda x: x.length > 0, entities))
    entities.reverse()

    return del_surrogate(parser.text), sorted(entities, key=lambda e: e.offset) or []


ENTITY_TO_FORMATTER = {
    MessageEntityBold: "strong",
    MessageEntityItalic: "em",
    MessageEntityUnderline: "u",
    MessageEntityStrike: "del",
}


def unparse(text: str, entities: Iterable[TypeMessageEntity]) -> str:
    """
    Performs the reverse operation to .parse(), effectively returning HTML
    given a normal text and its MessageEntity's.

    :param text: the text to be reconverted into HTML.
    :param entities: the MessageEntity's applied to the text.
    :return: a HTML representation of the combination of both inputs.
    """
    if not text:
        return text
    elif not entities:
        return escape(text)

    entities = list(entities)

    def parse_one(entity: TypeMessageEntity):
        """
        Parses a single entity and returns (start_tag, start), (end_tag, end)
        """
        entity_type = type(entity)
        entity_type_name = str(entity_type.__name__).replace("MessageEntity", "")
        start = entity.offset
        end = start + entity.length

        if entity_type in (
                MessageEntityBold,
                MessageEntityItalic,
                MessageEntityUnderline,
                MessageEntityStrike,
        ):
            name = ENTITY_TO_FORMATTER.get(entity_type)
            start_tag = f"<{name}>"
            end_tag = f"</{name}>"
        elif entity_type == MessageEntityPre:
            name = entity_type_name.lower()
            language = getattr(entity, "language", "") or ""
            start_tag = f'<{name} language="{language}">' if language else f"<{name}>"
            end_tag = f"</{name}>"
        elif entity_type == MessageEntityBlockquote:
            name = entity_type_name.lower()
            expandable = getattr(entity, "collapsed", False)
            start_tag = f'<{name}{" expandable" if expandable else ""}>'
            end_tag = f"</{name}>"
        elif entity_type in (
                MessageEntityCode,
                MessageEntitySpoiler,
        ):
            name = entity_type_name.lower()
            start_tag = f"<{name}>"
            end_tag = f"</{name}>"
        elif entity_type == MessageEntityTextUrl:
            url = entity.url
            start_tag = f'<a href="{url}">'
            end_tag = "</a>"
        elif entity_type == MessageEntityMentionName:
            user_id = entity.user_id
            start_tag = f'<a href="tg://user?id={user_id}">'
            end_tag = "</a>"
        elif entity_type == MessageEntityCustomEmoji:
            custom_emoji_id = entity.custom_emoji_id
            start_tag = f'<tg-emoji emoji-id="{custom_emoji_id}">'
            end_tag = "</tg-emoji>"
        elif entity_type == MessageEntityFormattedDate:
            unix_time = entity.date

            if entity.relative:
                date_time_format = "r"
            else:
                date_time_format = ""

                if entity.day_of_week:
                    date_time_format += "w"

                if entity.short_date or entity.long_date:
                    if entity.short_date:
                        date_time_format += "d"
                    elif entity.long_date:
                        date_time_format += "D"

                if entity.short_time or entity.long_time:
                    if entity.short_time:
                        date_time_format += "t"
                    elif entity.long_time:
                        date_time_format += "T"

            if date_time_format:
                start_tag = f'<tg-time unix="{unix_time}" format="{date_time_format}">'
            else:
                start_tag = f'<tg-time unix="{unix_time}">'

            end_tag = "</tg-time>"
        else:
            return None

        return (start_tag, start), (end_tag, end)

    def recursive(entity_i: int) -> int:
        """
        Takes the index of the entity to start parsing from, returns the number of parsed entities inside it.
        Uses entities_offsets as a stack, pushing (start_tag, start) first, then parsing nested entities,
        and finally pushing (end_tag, end) to the stack.
        No need to sort at the end.
        """
        this = parse_one(entities[entity_i])
        if this is None:
            return 1
        (start_tag, start), (end_tag, end) = this
        entities_offsets.append((start_tag, start))
        internal_i = entity_i + 1
        # while the next entity is inside the current one, keep parsing
        while internal_i < len(entities) and entities[internal_i].offset < end:
            internal_i += recursive(internal_i)
        entities_offsets.append((end_tag, end))
        return internal_i - entity_i

    text = add_surrogate(text)

    entities_offsets = []

    # probably useless because entities are already sorted by telegram
    entities.sort(key=lambda e: (e.offset, -e.length))

    # main loop for first-level entities
    i = 0
    while i < len(entities):
        i += recursive(i)

    if entities_offsets:
        last_offset = entities_offsets[-1][1]
        # no need to sort, but still add entities starting from the end
        for entity, offset in reversed(entities_offsets):
            text = text[:offset] + entity + html.escape(text[offset:last_offset]) + text[last_offset:]
            last_offset = offset

    return del_surrogate(text)
