"""signal-cli 0.14 `-o json receive` objects for tests (shapes as printed by signal-cli; never a real account)."""
OWN = "+48111"
OWN_UUID = "u-own"
OWN_NAME = "Wojciech Fiołka"
GID = "gT"


def _env(ts, body, number="+48999", uuid="u-kuba", name="Kuba Nowak"):
    env = {"source": number, "sourceNumber": number, "sourceUuid": uuid, "sourceName": name, "sourceDevice": 1,
           "timestamp": ts, "serverReceivedTimestamp": ts + 50, "serverDeliveredTimestamp": ts + 60, **body}
    return {"envelope": env, "account": OWN}


def _own_env(ts, sent):
    return _env(ts, {"syncMessage": {"sentMessage": sent}}, number=OWN, uuid=OWN_UUID, name=OWN_NAME)


def _quote(quote):
    if quote is None:
        return {}
    ts, author, text = quote
    return {"quote": {"id": ts, "author": author, "authorNumber": author if author.startswith("+") else None,
                      "authorUuid": author if not author.startswith("+") else None, "text": text, "attachments": []}}


def _group(gid):
    return {"groupInfo": {"groupId": gid, "groupName": "techarena_claude", "revision": 3, "type": "DELIVER"}}


def self_note(ts, text, quote=None, attachments=None):
    sent = {"timestamp": ts, "message": text, "expiresInSeconds": 0, "viewOnce": False, "destination": OWN,
            "destinationNumber": OWN, "destinationUuid": OWN_UUID, **_quote(quote)}
    if attachments:
        sent["attachments"] = attachments
    return _own_env(ts, sent)


def group_in(ts, text, gid=GID, name="Kuba Nowak", number="+48999", uuid="u-kuba", quote=None, attachments=None,
             mentions=None, expires=0):
    msg = {"timestamp": ts, "message": text, "expiresInSeconds": expires, "viewOnce": False, **_group(gid),
           **_quote(quote)}
    if attachments:
        msg["attachments"] = attachments
    if mentions:
        msg["mentions"] = mentions
    return _env(ts, {"dataMessage": msg}, number=number, uuid=uuid, name=name)


def group_own(ts, text, gid=GID, quote=None):
    sent = {"timestamp": ts, "message": text, "expiresInSeconds": 0, "viewOnce": False, "destination": None,
            "destinationNumber": None, "destinationUuid": None, **_group(gid), **_quote(quote)}
    return _own_env(ts, sent)


def dm(ts, text, number="+48777", name="Friend", attachments=None):
    msg = {"timestamp": ts, "message": text, "expiresInSeconds": 0, "viewOnce": False}
    if attachments:
        msg["attachments"] = attachments
    return _env(ts, {"dataMessage": msg}, number=number, uuid="u-friend", name=name)


def reaction(ts, emoji, target_ts, target_author, gid=GID, remove=False, own=False, name="Kuba Nowak",
             uuid="u-kuba"):
    r = {"emoji": emoji, "targetAuthor": target_author, "targetAuthorNumber": target_author
         if target_author.startswith("+") else None, "targetAuthorUuid": None if target_author.startswith("+")
         else target_author, "targetSentTimestamp": target_ts, "isRemove": remove}
    msg = {"timestamp": ts, "expiresInSeconds": 0, "viewOnce": False, "reaction": r}
    if gid != "self":
        msg.update(_group(gid))
    if own or gid == "self":
        return _own_env(ts, {**msg, "destination": OWN if gid == "self" else None,
                             "destinationNumber": OWN if gid == "self" else None})
    return _env(ts, {"dataMessage": msg}, uuid=uuid, name=name)


def edit(ts, target_ts, text, gid=GID):
    data = {"timestamp": ts, "message": text, "expiresInSeconds": 0, "viewOnce": False, **_group(gid)}
    return _env(ts, {"editMessage": {"targetSentTimestamp": target_ts, "dataMessage": data}})


def delete(ts, target_ts, gid=GID):
    msg = {"timestamp": ts, "expiresInSeconds": 0, "viewOnce": False, "remoteDelete": {"timestamp": target_ts},
           **_group(gid)}
    return _env(ts, {"dataMessage": msg})


def sticker(ts, gid=GID):
    msg = {"timestamp": ts, "expiresInSeconds": 0, "viewOnce": False, **_group(gid),
           "sticker": {"packId": "p", "packKey": "k", "stickerId": 3}}
    return _env(ts, {"dataMessage": msg})


def receipt(ts):
    return _env(ts, {"receiptMessage": {"when": ts, "isDelivery": True, "isRead": False, "timestamps": [ts - 1]}})


def typing(ts, gid=GID):
    return _env(ts, {"typingMessage": {"action": "STARTED", "timestamp": ts, "groupId": gid}})


def attachment(aid="x.jpg", filename="a.jpg", size=84000, content_type="image/jpeg"):
    return {"contentType": content_type, "filename": filename, "id": aid, "size": size, "width": 10, "height": 10,
            "caption": None, "uploadTimestamp": 1}
