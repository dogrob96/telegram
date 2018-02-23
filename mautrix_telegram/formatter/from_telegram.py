# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from html import escape
import logging

from telethon.tl.types import *
from mautrix_appservice import MatrixRequestError

from .. import user as u, puppet as pu, portal as po
from ..db import Message as DBMessage
from .util import add_surrogates, remove_surrogates

log = logging.getLogger("mau.fmt.tg")


def telegram_reply_to_matrix(evt, source):
    if evt.reply_to_msg_id:
        space = (evt.to_id.channel_id
                 if isinstance(evt, Message) and isinstance(evt.to_id, PeerChannel)
                 else source.tgid)
        msg = DBMessage.query.get((evt.reply_to_msg_id, space))
        if msg:
            return {
                "m.in_reply_to": {
                    "event_id": msg.mxid,
                    "room_id": msg.mx_room,
                }
            }
    return {}


async def telegram_to_matrix(evt, source, native_replies=False, message_link_in_reply=False,
                             main_intent=None, reply_text="Reply"):
    text = add_surrogates(evt.message)
    html = _telegram_entities_to_matrix_catch(text, evt.entities) if evt.entities else None
    relates_to = {}

    if evt.fwd_from:
        if not html:
            html = escape(text)
        from_id = evt.fwd_from.from_id
        user = u.User.get_by_tgid(from_id)
        if user:
            fwd_from = f"<a href='https://matrix.to/#/{user.mxid}'>{user.mxid}</a>"
        else:
            puppet = pu.Puppet.get(from_id, create=False)
            if puppet and puppet.displayname:
                fwd_from = f"<a href='https://matrix.to/#/{puppet.mxid}'>{puppet.displayname}</a>"
            else:
                user = await source.client.get_entity(from_id)
                if user:
                    fwd_from = pu.Puppet.get_displayname(user, format=False)
                else:
                    fwd_from = None
        if not fwd_from:
            fwd_from = "Unknown user"
        html = (f"Forwarded message from <b>{fwd_from}</b><br/>"
                f"<blockquote>{html}</blockquote>")

    if evt.reply_to_msg_id:
        space = (evt.to_id.channel_id
                 if isinstance(evt, Message) and isinstance(evt.to_id, PeerChannel)
                 else source.tgid)
        msg = DBMessage.query.get((evt.reply_to_msg_id, space))
        if msg:
            if native_replies:
                relates_to["m.in_reply_to"] = {
                    "event_id": msg.mxid,
                    "room_id": msg.mx_room,
                }
                if reply_text == "Edit":
                    html = "<u>Edit:</u> " + (html or escape(text))
            else:
                try:
                    event = await main_intent.get_event(msg.mx_room, msg.mxid)
                    content = event["content"]
                    body = (content["formatted_body"]
                            if "formatted_body" in content
                            else content["body"])
                    sender = event['sender']
                    puppet = pu.Puppet.get_by_mxid(sender, create=False)
                    displayname = puppet.displayname if puppet else sender
                    reply_to_user = f"<a href='https://matrix.to/#/{sender}'>{displayname}</a>"
                    reply_to_msg = (("<a href='https://matrix.to/#/"
                                     f"{msg.mx_room}/{msg.mxid}'>{reply_text}</a>")
                                    if message_link_in_reply else "Reply")
                    quote = f"{reply_to_msg} to {reply_to_user}<blockquote>{body}</blockquote>"
                except (ValueError, KeyError, MatrixRequestError):
                    quote = "{reply_text} to unknown user <em>(Failed to fetch message)</em>:<br/>"
                if html:
                    html = quote + html
                else:
                    html = quote + escape(text)

    if isinstance(evt, Message) and evt.post and evt.post_author:
        if not html:
            html = escape(text)
        text += f"\n- {evt.post_author}"
        html += f"<br/><i>- <u>{evt.post_author}</u></i>"

    if html:
        html = html.replace("\n", "<br/>")

    return remove_surrogates(text), remove_surrogates(html), relates_to


def _telegram_entities_to_matrix_catch(text, entities):
    try:
        return _telegram_entities_to_matrix(text, entities)
    except Exception:
        log.exception("Failed to convert Telegram format:\n"
                      "message=%s\n"
                      "entities=%s",
                      text, entities)


def _telegram_entities_to_matrix(text, entities):
    if not entities:
        return text
    html = []
    last_offset = 0
    for entity in entities:
        if entity.offset > last_offset:
            html.append(escape(text[last_offset:entity.offset]))
        elif entity.offset < last_offset:
            continue

        skip_entity = False
        entity_text = escape(text[entity.offset:entity.offset + entity.length])
        entity_type = type(entity)

        if entity_type == MessageEntityBold:
            html.append(f"<strong>{entity_text}</strong>")
        elif entity_type == MessageEntityItalic:
            html.append(f"<em>{entity_text}</em>")
        elif entity_type == MessageEntityCode:
            html.append(f"<code>{entity_text}</code>")
        elif entity_type == MessageEntityPre:
            if entity.language:
                html.append("<pre>"
                            f"<code class='language-{entity.language}'>{entity_text}</code>"
                            "</pre>")
            else:
                html.append(f"<pre><code>{entity_text}</code></pre>")
        elif entity_type == MessageEntityMention:
            username = entity_text[1:]

            user = u.User.find_by_username(username) or pu.Puppet.find_by_username(username)
            if user:
                mxid = user.mxid
            else:
                portal = po.Portal.find_by_username(username)
                mxid = portal.alias or portal.mxid if portal else None

            if mxid:
                html.append(f"<a href='https://matrix.to/#/{mxid}'>{entity_text}</a>")
            else:
                skip_entity = True
        elif entity_type == MessageEntityMentionName:
            user = u.User.get_by_tgid(entity.user_id)
            if user:
                mxid = user.mxid
            else:
                puppet = pu.Puppet.get(entity.user_id, create=False)
                mxid = puppet.mxid if puppet else None
            if mxid:
                html.append(f"<a href='https://matrix.to/#/{mxid}'>{entity_text}</a>")
            else:
                skip_entity = True
        elif entity_type == MessageEntityEmail:
            html.append(f"<a href='mailto:{entity_text}'>{entity_text}</a>")
        elif entity_type in {MessageEntityTextUrl, MessageEntityUrl}:
            url = escape(entity.url) if entity_type == MessageEntityTextUrl else entity_text
            if not url.startswith(("https://", "http://", "ftp://", "magnet://")):
                url = "http://" + url
            html.append(f"<a href='{url}'>{entity_text}</a>")
        elif entity_type == MessageEntityBotCommand:
            html.append(f"<font color='blue'>!{entity_text[1:]}")
        elif entity_type == MessageEntityHashtag:
            html.append(f"<font color='blue'>{entity_text}</font>")
        else:
            skip_entity = True
        last_offset = entity.offset + (0 if skip_entity else entity.length)
    html.append(text[last_offset:])

    return "".join(html)
