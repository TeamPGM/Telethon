"""
Simple markdown parser which does not support nesting. Intended primarily
for use within the library, which attempts to handle emojies correctly,
since they seem to count as two characters and it's a bit strange.
"""
import html
import re
import urllib.parse
from typing import List, Union, Tuple

from .html import parse as html_parse
from ..helpers import add_surrogate, del_surrogate
from ..tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode, MessageEntityUnderline,
    MessageEntityPre, MessageEntityTextUrl, MessageEntityMentionName,
    MessageEntityStrike, MessageEntityBlockquote, MessageEntitySpoiler,
    MessageEntityCustomEmoji, MessageEntityFormattedDate,
)

BOLD_DELIM = "**"
ITALIC_DELIM = "__"
UNDERLINE_DELIM = "--"
STRIKE_DELIM = "~~"
SPOILER_DELIM = "||"
CODE_DELIM = "`"
PRE_DELIM = "```"
BLOCKQUOTE_DELIM = ">"
BLOCKQUOTE_EXPANDABLE_DELIM = "**>"
BLOCKQUOTE_EXPANDABLE_END_DELIM = "||"

MARKDOWN_RE = re.compile(r"({d})|(!?)\[(.+?)\]\((.+?)\)".format(
    d="|".join(
        ["".join(i) for i in [
            [rf"\{j}" for j in i]
            for i in [
                PRE_DELIM,
                CODE_DELIM,
                STRIKE_DELIM,
                UNDERLINE_DELIM,
                ITALIC_DELIM,
                BOLD_DELIM,
                SPOILER_DELIM
            ]
        ]]
    )))

OPENING_TAG = "<{}>"
CLOSING_TAG = "</{}>"
URL_MARKUP = '<a href="{}">{}</a>'
EMOJI_MARKUP = '<tg-emoji emoji-id="{}">{}</tg-emoji>'
DATE_TIME_MARKUP = '<tg-time unix="{}">{}</tg-time>'
FORMATTED_DATE_TIME_MARKUP = '<tg-time unix="{}" format="{}">{}</tg-time>'
FIXED_WIDTH_DELIMS = [CODE_DELIM, PRE_DELIM]


def escape_and_create_quotes(text: str, strict: bool):
    text_lines: List[Union[str, None]] = text.splitlines()

    # Indexes of Already escaped lines
    html_escaped_list: List[int] = []

    # Temporary Queue to hold lines to be quoted
    to_quote_list: List[Tuple[int, str]] = []

    def create_blockquote(expandable: bool = False) -> None:
        """
        Merges all lines in quote_queue into first line of queue
        Encloses that line in html quote
        Replaces rest of the lines with None placeholders to preserve indexes
        """
        if len(to_quote_list) == 0:
            return

        joined_lines = "\n".join([i[1] for i in to_quote_list])

        first_line_index, _ = to_quote_list[0]
        text_lines[first_line_index] = (
            f"<blockquote{' expandable' if expandable else ''}>{joined_lines}</blockquote>"
        )

        for line_to_remove in to_quote_list[1:]:
            text_lines[line_to_remove[0]] = None

        to_quote_list.clear()

    # Handle Expandable Quote
    inside_blockquote = False
    for index, line in enumerate(text_lines):
        if line.startswith(BLOCKQUOTE_EXPANDABLE_DELIM) and not inside_blockquote:
            delim_stripped_line = line[
                len(BLOCKQUOTE_EXPANDABLE_DELIM) + (1 if line.startswith(f"{BLOCKQUOTE_EXPANDABLE_DELIM} ") else 0):]
            parsed_line = (
                html.escape(delim_stripped_line) if strict else delim_stripped_line
            )

            to_quote_list.append((index, parsed_line))
            html_escaped_list.append(index)

            inside_blockquote = True
            continue

        elif line.endswith(BLOCKQUOTE_EXPANDABLE_END_DELIM) and inside_blockquote:
            if line.startswith(BLOCKQUOTE_DELIM):
                line = line[len(BLOCKQUOTE_DELIM) + (1 if line.startswith(f"{BLOCKQUOTE_DELIM} ") else 0):]

            delim_stripped_line = line[:-len(BLOCKQUOTE_EXPANDABLE_END_DELIM)]

            parsed_line = (
                html.escape(delim_stripped_line) if strict else delim_stripped_line
            )

            to_quote_list.append((index, parsed_line))
            html_escaped_list.append(index)

            inside_blockquote = False

            create_blockquote(expandable=True)

        if inside_blockquote:
            parsed_line = line[len(BLOCKQUOTE_DELIM) + (1 if line.startswith(f"{BLOCKQUOTE_DELIM} ") else 0):]
            parsed_line = html.escape(parsed_line) if strict else parsed_line
            to_quote_list.append((index, parsed_line))
            html_escaped_list.append(index)

    # Handle Single line/Continued Quote
    for index, line in enumerate(text_lines):
        if line is None:
            continue

        if line.startswith(BLOCKQUOTE_DELIM):
            delim_stripped_line = line[len(BLOCKQUOTE_DELIM) + (1 if line.startswith(f"{BLOCKQUOTE_DELIM} ") else 0):]
            parsed_line = (
                html.escape(delim_stripped_line) if strict else delim_stripped_line
            )

            to_quote_list.append((index, parsed_line))
            html_escaped_list.append(index)

        elif len(to_quote_list) > 0:
            create_blockquote()
    else:
        create_blockquote()

    if strict:
        for idx, line in enumerate(text_lines):
            if idx not in html_escaped_list:
                text_lines[idx] = html.escape(line)

    return "\n".join(
        [valid_line for valid_line in text_lines if valid_line is not None]
    )


def replace_once(source: str, old: str, new: str, start: int):
    return source[:start] + source[start:].replace(old, new, 1)


def parse(message, strict: bool = False):
    """
    Parses the given markdown message and returns its stripped representation
    plus a list of the MessageEntity's that were found.

    :param message: the message with markdown-like syntax to be parsed.
    :param delimiters: the delimiters to be used, {delimiter: type}.
    :param url_re: the URL bytes regex to be used. Must have two groups.
    :return: a tuple consisting of (clean message, [message entities]).
    """
    if not message:
        return message, []

    text = escape_and_create_quotes(message, strict=strict)
    delims = set()
    is_fixed_width = False

    for match in re.finditer(MARKDOWN_RE, text):
        start, _ = match.span()
        delim, is_emoji_like, text_url, url = match.groups()
        full = match.group(0)

        if delim in FIXED_WIDTH_DELIMS:
            is_fixed_width = not is_fixed_width

        if is_fixed_width and delim not in FIXED_WIDTH_DELIMS:
            continue

        if not is_emoji_like and text_url:
            text = replace_once(text, full, URL_MARKUP.format(url, text_url), start)
            continue

        if is_emoji_like:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            emoji_date = text_url

            if parsed.netloc == "time":
                unix_time = params.get("unix", [""])[0]
                date_time_format = params.get("format", [""])[0]

                if date_time_format:
                    markup = FORMATTED_DATE_TIME_MARKUP.format(unix_time, date_time_format, emoji_date)
                else:
                    markup = DATE_TIME_MARKUP.format(unix_time, emoji_date)
                text = replace_once(text, full, markup, start)
            elif parsed.netloc == "emoji":
                emoji_id = params.get("id", [""])[0]
                markup = EMOJI_MARKUP.format(emoji_id, emoji_date)
                text = replace_once(text, full, markup, start)

            continue

        if delim == BOLD_DELIM:
            tag = "b"
        elif delim == ITALIC_DELIM:
            tag = "i"
        elif delim == UNDERLINE_DELIM:
            tag = "u"
        elif delim == STRIKE_DELIM:
            tag = "s"
        elif delim == CODE_DELIM:
            tag = "code"
        elif delim == PRE_DELIM:
            tag = "pre"
        elif delim == SPOILER_DELIM:
            tag = "spoiler"
        else:
            continue

        if delim not in delims:
            delims.add(delim)
            tag = OPENING_TAG.format(tag)
        else:
            delims.remove(delim)
            tag = CLOSING_TAG.format(tag)

        if delim == PRE_DELIM and delim in delims:
            delim_and_language = text[text.find(PRE_DELIM):].split("\n")[0]
            language = delim_and_language[len(PRE_DELIM):]
            text = replace_once(text, delim_and_language, f'<pre language="{language}">', start)
            continue

        text = replace_once(text, delim, tag, start)

    return html_parse(text)


def unparse(text, entities):
    """
    Performs the reverse operation to .parse(), effectively returning
    markdown-like syntax given a normal text and its MessageEntity's.

    :return: a markdown-like text representing the combination of both inputs.
    """
    if not text or not entities:
        return text

    text = add_surrogate(text)

    entities_offsets = []

    for entity in entities:
        entity_type = type(entity)
        start = entity.offset
        end = start + entity.length

        if entity_type == MessageEntityBold:
            start_tag = end_tag = BOLD_DELIM
        elif entity_type == MessageEntityItalic:
            start_tag = end_tag = ITALIC_DELIM
        elif entity_type == MessageEntityUnderline:
            start_tag = end_tag = UNDERLINE_DELIM
        elif entity_type == MessageEntityStrike:
            start_tag = end_tag = STRIKE_DELIM
        elif entity_type == MessageEntityCode:
            start_tag = end_tag = CODE_DELIM
        elif entity_type == MessageEntityPre:
            language = getattr(entity, "language", "") or ""
            start_tag = f"{PRE_DELIM}{language}\n"
            end_tag = f"\n{PRE_DELIM}"
        elif entity_type == MessageEntityBlockquote:
            start_tag = BLOCKQUOTE_DELIM + " "
            end_tag = ""
            blockquote_text = text[start:end]
            lines = blockquote_text.split("\n")
            last_length = 0
            for line in lines:
                if len(line) == 0 and last_length == end:
                    continue
                start_offset = start + last_length
                last_length = last_length + len(line)
                end_offset = start_offset + last_length
                entities_offsets.append((start_tag, start_offset,))
                entities_offsets.append((end_tag, end_offset,))
                last_length = last_length + 1
            continue
        elif entity_type == MessageEntitySpoiler:
            start_tag = end_tag = SPOILER_DELIM
        elif entity_type == MessageEntityTextUrl:
            url = entity.url
            start_tag = "["
            end_tag = f"]({url})"
        elif entity_type == MessageEntityMentionName:
            user_id = entity.user_id
            start_tag = "["
            end_tag = f"](tg://user?id={user_id})"
        elif entity_type == MessageEntityCustomEmoji:
            document_id = entity.document_id
            start_tag = "!["
            end_tag = f"](tg://emoji?id={document_id})"
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

            start_tag = "!["
            if date_time_format:
                end_tag = f"](tg://time?unix={unix_time}&format={date_time_format})"
            else:
                end_tag = f"](tg://time?unix={unix_time})"
        else:
            continue

        entities_offsets.append((start_tag, start,))
        entities_offsets.append((end_tag, end,))

    entities_offsets = map(
        lambda x: x[1],
        sorted(
            enumerate(entities_offsets),
            key=lambda x: (x[1][1], x[0]),
            reverse=True
        )
    )

    for entity, offset in entities_offsets:
        text = text[:offset] + entity + text[offset:]

    return del_surrogate(text)


class StrictMarkdown:
    @staticmethod
    def parse(html: str):
        return parse(html, True)

    @staticmethod
    def unparse(text: str, entities):
        return unparse(text, entities)
