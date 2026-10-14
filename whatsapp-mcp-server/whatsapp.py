import sqlite3
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Tuple
import os.path
import requests
import json
import subprocess
import audio

MESSAGES_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'whatsapp-bridge', 'store', 'messages.db')
WHATSAPP_API_BASE_URL = "http://localhost:8080/api"

@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None
    local_path: Optional[str] = None
    downloadable: bool = False
    transcription: Optional[str] = None

def ensure_transcription_column() -> None:
    """Add messages.transcription if the bridge that created the database predates it."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(messages)")]
            if columns and "transcription" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN transcription TEXT")
                conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # No database yet; the bridge creates it with the column

ensure_transcription_column()

def media_columns(alias: str) -> str:
    """Extra SELECT columns for media status: transcription, local path, and whether the
    bridge has everything it needs to download the file. Read back with media_fields()."""
    return f""", {alias}.transcription, {alias}.local_path,
        ({alias}.url IS NOT NULL AND {alias}.url != ''
         AND length({alias}.media_key) > 0 AND length({alias}.file_sha256) > 0
         AND length({alias}.file_enc_sha256) > 0 AND {alias}.file_length > 0)"""

def media_fields(row) -> dict:
    """Message kwargs for the columns added by media_columns(), which come last."""
    return {"transcription": row[-3], "local_path": row[-2], "downloadable": bool(row[-1])}

@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None

    @property
    def is_group(self) -> bool:
        """Determine if chat is a group based on JID pattern."""
        return self.jid.endswith("@g.us")

@dataclass
class Contact:
    phone_number: str
    name: Optional[str]
    jid: str

@dataclass
class MessageContext:
    message: Message
    before: List[Message]
    after: List[Message]

WHATSAPP_DB_PATH = os.path.join(os.path.dirname(MESSAGES_DB_PATH), 'whatsapp.db')
CONTACT_NAMES_TABLE = "CREATE TABLE IF NOT EXISTS contact_names (jid TEXT PRIMARY KEY, name TEXT NOT NULL)"
# messages.sender holds only the user part of the JID, so names are looked up under both servers
SENDER_SERVERS = ("s.whatsapp.net", "lid")

def _sender_jids(sender_jid: str) -> List[str]:
    user = sender_jid.split('@')[0]
    return [sender_jid] + [f"{user}@{server}" for server in SENDER_SERVERS]

def whatsapp_profile_name(sender_jid: str) -> Optional[str]:
    """Name WhatsApp knows for a contact (address book, profile or business name),
    read from the bridge's session database."""
    jids = _sender_jids(sender_jid)
    try:
        conn = sqlite3.connect(f"file:{WHATSAPP_DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute(f"""
                SELECT COALESCE(NULLIF(full_name, ''), NULLIF(push_name, ''), NULLIF(business_name, ''))
                FROM whatsmeow_contacts
                WHERE their_jid IN ({','.join('?' * len(jids))})
                ORDER BY full_name != '' DESC
                LIMIT 1
            """, jids).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return row[0] if row and row[0] else None

def get_sender_name(sender_jid: str) -> str:
    """Display name for a sender: a name assigned with set_contact_name, then the
    chat name, then the name WhatsApp knows, then the JID itself."""
    user = sender_jid.split('@')[0]
    jids = _sender_jids(sender_jid)
    placeholders = ','.join('?' * len(jids))
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        try:
            conn.execute(CONTACT_NAMES_TABLE)
            row = conn.execute(f"SELECT name FROM contact_names WHERE jid IN ({placeholders}) LIMIT 1", jids).fetchone()
            if not row:
                row = conn.execute(f"SELECT name FROM chats WHERE jid IN ({placeholders}) AND name != ? LIMIT 1",
                                   (*jids, user)).fetchone()
            if not row:
                # Older rows may store the JID in another form
                row = conn.execute("SELECT name FROM chats WHERE jid LIKE ? AND name != ? LIMIT 1",
                                   (f"%{user}%", user)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"Database error while getting sender name: {e}")
        row = None
    if row and row[0]:
        return row[0]
    return whatsapp_profile_name(sender_jid) or sender_jid

def normalize_contact_jid(jid_or_phone: str) -> str:
    """Accept a JID or a phone number (with or without +, spaces or dashes)."""
    jid_or_phone = jid_or_phone.strip()
    if '@' in jid_or_phone:
        return jid_or_phone
    return ''.join(ch for ch in jid_or_phone if ch.isdigit()) + "@s.whatsapp.net"

def set_contact_name(jid_or_phone: str, name: str) -> Tuple[bool, str]:
    """Save a name for a contact, used everywhere instead of the bare number.
    An empty name removes it."""
    jid = normalize_contact_jid(jid_or_phone)
    user = jid.split('@')[0]
    if not user:
        return False, "Invalid contact"
    if jid.endswith("@g.us"):
        return False, "Groups already have a name; this is only for contacts"
    name = name.strip()
    conn = sqlite3.connect(MESSAGES_DB_PATH)
    try:
        conn.execute(CONTACT_NAMES_TABLE)
        if name:
            conn.execute("INSERT INTO contact_names (jid, name) VALUES (?, ?) "
                         "ON CONFLICT(jid) DO UPDATE SET name = excluded.name", (jid, name))
        else:
            conn.execute("DELETE FROM contact_names WHERE jid = ?", (jid,))
        # The chat shows the new name right away; with no name, the bridge looks it up again
        conn.execute("UPDATE chats SET name = ? WHERE jid = ?",
                     (name or whatsapp_profile_name(jid) or user, jid))
        conn.commit()
    finally:
        conn.close()
    return True, f"Name for {jid} set to {name}" if name else f"Name for {jid} removed"

def list_contact_names() -> List[dict]:
    """Contacts with a name assigned with set_contact_name."""
    conn = sqlite3.connect(MESSAGES_DB_PATH)
    try:
        conn.execute(CONTACT_NAMES_TABLE)
        rows = conn.execute("SELECT jid, name FROM contact_names ORDER BY name COLLATE NOCASE").fetchall()
    finally:
        conn.close()
    return [{"jid": jid, "phone": jid.split('@')[0], "name": name} for jid, name in rows]

def _known_lids() -> set:
    """User parts of the hidden-number (LID) contacts the bridge's session knows."""
    try:
        conn = sqlite3.connect(f"file:{WHATSAPP_DB_PATH}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT their_jid FROM whatsmeow_contacts WHERE their_jid LIKE '%@lid'").fetchall()
            rows += conn.execute("SELECT lid || '@lid' FROM whatsmeow_lid_map").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return set()
    return {jid.split('@')[0] for (jid,) in rows}

def list_unnamed_contacts() -> List[dict]:
    """Contacts shown only by their number: direct chats without a real name and
    group members with no chat of their own, most recent first."""
    conn = sqlite3.connect(MESSAGES_DB_PATH)
    try:
        conn.execute(CONTACT_NAMES_TABLE)
        chats = conn.execute("""
            SELECT jid, last_message_time FROM chats
            WHERE jid NOT LIKE '%@g.us' AND jid NOT LIKE '%@broadcast' AND jid NOT LIKE '%@newsletter'
              AND (name IS NULL OR name = '' OR name = substr(jid, 1, instr(jid, '@') - 1))
              AND jid NOT IN (SELECT jid FROM contact_names)
        """).fetchall()
        senders = conn.execute("""
            SELECT m.sender, MAX(m.timestamp), COUNT(DISTINCT m.chat_jid) FROM messages m
            WHERE m.chat_jid LIKE '%@g.us' AND m.is_from_me = 0 AND m.sender != ''
              AND NOT EXISTS (SELECT 1 FROM chats c WHERE c.jid IN (m.sender || '@s.whatsapp.net', m.sender || '@lid'))
              AND NOT EXISTS (SELECT 1 FROM contact_names n WHERE n.jid IN (m.sender || '@s.whatsapp.net', m.sender || '@lid'))
            GROUP BY m.sender
        """).fetchall()
    finally:
        conn.close()
    result = [{"jid": jid, "last_seen": last, "source": "chat"} for jid, last in chats]
    lids = _known_lids()
    for user, last, groups in senders:
        server = "lid" if user in lids else "s.whatsapp.net"
        result.append({"jid": f"{user}@{server}", "last_seen": last, "source": "group", "groups": groups})
    for contact in result:
        contact["phone"] = contact["jid"].split('@')[0]
        contact["hidden_number"] = contact["jid"].endswith("@lid")
        contact["suggestion"] = whatsapp_profile_name(contact["jid"])
    result.sort(key=lambda c: c["last_seen"] or "", reverse=True)
    return result

def format_message(message: Message, show_chat_info: bool = True) -> None:
    """Print a single message with consistent formatting."""
    output = ""
    
    if show_chat_info and message.chat_name:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] Chat: {message.chat_name} "
    else:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] "
        
    content_prefix = ""
    if hasattr(message, 'media_type') and message.media_type:
        if message.local_path and os.path.exists(message.local_path):
            status = f"downloaded at {message.local_path}"
        elif message.downloadable:
            status = "not downloaded yet, call download_media with this message ID and chat JID to get the file"
        else:
            status = "cannot be downloaded, WhatsApp did not send the data needed to fetch it"
        content_prefix = f"[{message.media_type} - Message ID: {message.id} - Chat JID: {message.chat_jid} - {status}] "
        if message.media_type == "audio":
            if message.transcription:
                content_prefix += f'Transcription: "{message.transcription}" '
            elif message.downloadable or message.local_path:
                content_prefix += "(call transcribe_audio to get the text) "
    
    try:
        sender_name = get_sender_name(message.sender) if not message.is_from_me else "Me"
        output += f"From: {sender_name}: {content_prefix}{message.content}\n"
    except Exception as e:
        print(f"Error formatting message: {e}")
    return output

def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> None:
    output = ""
    if not messages:
        output += "No messages to display."
        return output
    
    for message in messages:
        output += format_message(message, show_chat_info)
    return output

def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: bool = True,
    context_before: int = 1,
    context_after: int = 1
) -> List[Message]:
    """Get messages matching the specified criteria with optional context."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        # Build base query
        query_parts = ["SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type" + media_columns("messages") + " FROM messages"]
        query_parts.append("JOIN chats ON messages.chat_jid = chats.jid")
        where_clauses = []
        params = []
        
        # Add filters
        if after:
            try:
                after = datetime.fromisoformat(after)
            except ValueError:
                raise ValueError(f"Invalid date format for 'after': {after}. Please use ISO-8601 format.")
            
            where_clauses.append("messages.timestamp > ?")
            params.append(after)

        if before:
            try:
                before = datetime.fromisoformat(before)
            except ValueError:
                raise ValueError(f"Invalid date format for 'before': {before}. Please use ISO-8601 format.")
            
            where_clauses.append("messages.timestamp < ?")
            params.append(before)

        if sender_phone_number:
            where_clauses.append("messages.sender = ?")
            params.append(sender_phone_number)
            
        if chat_jid:
            where_clauses.append("messages.chat_jid = ?")
            params.append(chat_jid)
            
        if query:
            where_clauses.append("LOWER(messages.content) LIKE LOWER(?)")
            params.append(f"%{query}%")
            
        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))
            
        # Add pagination
        offset = page * limit
        query_parts.append("ORDER BY messages.timestamp DESC")
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        
        cursor.execute(" ".join(query_parts), tuple(params))
        messages = cursor.fetchall()
        
        result = []
        for msg in messages:
            message = Message(
                timestamp=datetime.fromisoformat(msg[0]),
                sender=msg[1],
                chat_name=msg[2],
                content=msg[3],
                is_from_me=msg[4],
                chat_jid=msg[5],
                id=msg[6],
                media_type=msg[7],
                **media_fields(msg)
            )
            result.append(message)
            
        if include_context and result:
            # Add context for each message
            messages_with_context = []
            for msg in result:
                context = get_message_context(msg.id, context_before, context_after)
                messages_with_context.extend(context.before)
                messages_with_context.append(context.message)
                messages_with_context.extend(context.after)
            
            return format_messages_list(messages_with_context, show_chat_info=True)
            
        # Format and display messages without context
        return format_messages_list(result, show_chat_info=True)    
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> MessageContext:
    """Get context around a specific message."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        # Get the target message first
        cursor.execute(f"""
            SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.chat_jid, messages.media_type{media_columns("messages")}
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.id = ?
        """, (message_id,))
        msg_data = cursor.fetchone()
        
        if not msg_data:
            raise ValueError(f"Message with ID {message_id} not found")
            
        target_message = Message(
            timestamp=datetime.fromisoformat(msg_data[0]),
            sender=msg_data[1],
            chat_name=msg_data[2],
            content=msg_data[3],
            is_from_me=msg_data[4],
            chat_jid=msg_data[5],
            id=msg_data[6],
            media_type=msg_data[8],
            **media_fields(msg_data)
        )
        
        # Get messages before
        cursor.execute(f"""
            SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type{media_columns("messages")}
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.chat_jid = ? AND messages.timestamp < ?
            ORDER BY messages.timestamp DESC
            LIMIT ?
        """, (msg_data[7], msg_data[0], before))
        
        before_messages = []
        for msg in cursor.fetchall():
            before_messages.append(Message(
                timestamp=datetime.fromisoformat(msg[0]),
                sender=msg[1],
                chat_name=msg[2],
                content=msg[3],
                is_from_me=msg[4],
                chat_jid=msg[5],
                id=msg[6],
                media_type=msg[7],
                **media_fields(msg)
            ))
        
        # Get messages after
        cursor.execute(f"""
            SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type{media_columns("messages")}
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.chat_jid = ? AND messages.timestamp > ?
            ORDER BY messages.timestamp ASC
            LIMIT ?
        """, (msg_data[7], msg_data[0], after))
        
        after_messages = []
        for msg in cursor.fetchall():
            after_messages.append(Message(
                timestamp=datetime.fromisoformat(msg[0]),
                sender=msg[1],
                chat_name=msg[2],
                content=msg[3],
                is_from_me=msg[4],
                chat_jid=msg[5],
                id=msg[6],
                media_type=msg[7],
                **media_fields(msg)
            ))
        
        return MessageContext(
            message=target_message,
            before=before_messages,
            after=after_messages
        )
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()


def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Chat]:
    """Get chats matching the specified criteria."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        # Build base query
        query_parts = ["""
            SELECT 
                chats.jid,
                chats.name,
                chats.last_message_time,
                messages.content as last_message,
                messages.sender as last_sender,
                messages.is_from_me as last_is_from_me
            FROM chats
        """]
        
        if include_last_message:
            query_parts.append("""
                LEFT JOIN messages ON chats.jid = messages.chat_jid 
                AND chats.last_message_time = messages.timestamp
            """)
            
        where_clauses = []
        params = []
        
        if query:
            where_clauses.append("(LOWER(chats.name) LIKE LOWER(?) OR chats.jid LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
            
        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))
            
        # Add sorting
        order_by = "chats.last_message_time DESC" if sort_by == "last_active" else "chats.name"
        query_parts.append(f"ORDER BY {order_by}")
        
        # Add pagination
        offset = (page ) * limit
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        
        cursor.execute(" ".join(query_parts), tuple(params))
        chats = cursor.fetchall()
        
        result = []
        for chat_data in chats:
            chat = Chat(
                jid=chat_data[0],
                name=chat_data[1],
                last_message_time=datetime.fromisoformat(chat_data[2]) if chat_data[2] else None,
                last_message=chat_data[3],
                last_sender=chat_data[4],
                last_is_from_me=chat_data[5]
            )
            result.append(chat)
            
        return result
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def search_contacts(query: str) -> List[Contact]:
    """Search contacts by name or phone number."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        # Split query into characters to support partial matching
        search_pattern = '%' +query + '%'
        
        cursor.execute("""
            SELECT DISTINCT 
                jid,
                name
            FROM chats
            WHERE 
                (LOWER(name) LIKE LOWER(?) OR LOWER(jid) LIKE LOWER(?))
                AND jid NOT LIKE '%@g.us'
            ORDER BY name, jid
            LIMIT 50
        """, (search_pattern, search_pattern))
        
        contacts = cursor.fetchall()
        
        result = []
        for contact_data in contacts:
            contact = Contact(
                phone_number=contact_data[0].split('@')[0],
                name=contact_data[1],
                jid=contact_data[0]
            )
            result.append(contact)
            
        return result
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Chat]:
    """Get all chats involving the contact.
    
    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT
                c.jid,
                c.name,
                c.last_message_time,
                m.content as last_message,
                m.sender as last_sender,
                m.is_from_me as last_is_from_me
            FROM chats c
            JOIN messages m ON c.jid = m.chat_jid
            WHERE m.sender = ? OR c.jid = ?
            ORDER BY c.last_message_time DESC
            LIMIT ? OFFSET ?
        """, (jid, jid, limit, page * limit))
        
        chats = cursor.fetchall()
        
        result = []
        for chat_data in chats:
            chat = Chat(
                jid=chat_data[0],
                name=chat_data[1],
                last_message_time=datetime.fromisoformat(chat_data[2]) if chat_data[2] else None,
                last_message=chat_data[3],
                last_sender=chat_data[4],
                last_is_from_me=chat_data[5]
            )
            result.append(chat)
            
        return result
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_last_interaction(jid: str) -> str:
    """Get most recent message involving the contact."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT 
                m.timestamp,
                m.sender,
                c.name,
                m.content,
                m.is_from_me,
                c.jid,
                m.id,
                m.media_type
                {media_columns("m")}
            FROM messages m
            JOIN chats c ON m.chat_jid = c.jid
            WHERE m.sender = ? OR c.jid = ?
            ORDER BY m.timestamp DESC
            LIMIT 1
        """, (jid, jid))
        
        msg_data = cursor.fetchone()
        
        if not msg_data:
            return None
            
        message = Message(
            timestamp=datetime.fromisoformat(msg_data[0]),
            sender=msg_data[1],
            chat_name=msg_data[2],
            content=msg_data[3],
            is_from_me=msg_data[4],
            chat_jid=msg_data[5],
            id=msg_data[6],
            media_type=msg_data[7],
            **media_fields(msg_data)
        )
        
        return format_message(message)
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_chat(chat_jid: str, include_last_message: bool = True) -> Optional[Chat]:
    """Get chat metadata by JID."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        query = """
            SELECT 
                c.jid,
                c.name,
                c.last_message_time,
                m.content as last_message,
                m.sender as last_sender,
                m.is_from_me as last_is_from_me
            FROM chats c
        """
        
        if include_last_message:
            query += """
                LEFT JOIN messages m ON c.jid = m.chat_jid 
                AND c.last_message_time = m.timestamp
            """
            
        query += " WHERE c.jid = ?"
        
        cursor.execute(query, (chat_jid,))
        chat_data = cursor.fetchone()
        
        if not chat_data:
            return None
            
        return Chat(
            jid=chat_data[0],
            name=chat_data[1],
            last_message_time=datetime.fromisoformat(chat_data[2]) if chat_data[2] else None,
            last_message=chat_data[3],
            last_sender=chat_data[4],
            last_is_from_me=chat_data[5]
        )
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_direct_chat_by_contact(sender_phone_number: str) -> Optional[Chat]:
    """Get chat metadata by sender phone number."""
    try:
        conn = sqlite3.connect(MESSAGES_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                c.jid,
                c.name,
                c.last_message_time,
                m.content as last_message,
                m.sender as last_sender,
                m.is_from_me as last_is_from_me
            FROM chats c
            LEFT JOIN messages m ON c.jid = m.chat_jid 
                AND c.last_message_time = m.timestamp
            WHERE c.jid LIKE ? AND c.jid NOT LIKE '%@g.us'
            LIMIT 1
        """, (f"%{sender_phone_number}%",))
        
        chat_data = cursor.fetchone()
        
        if not chat_data:
            return None
            
        return Chat(
            jid=chat_data[0],
            name=chat_data[1],
            last_message_time=datetime.fromisoformat(chat_data[2]) if chat_data[2] else None,
            last_message=chat_data[3],
            last_sender=chat_data[4],
            last_is_from_me=chat_data[5]
        )
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def send_message(recipient: str, message: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "message": message,
        }
        
        response = requests.post(url, json=payload)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_file(recipient: str, media_path: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        if not media_path:
            return False, "Media path must be provided"
        
        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "media_path": media_path
        }
        
        response = requests.post(url, json=payload)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_audio_message(recipient: str, media_path: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        if not media_path:
            return False, "Media path must be provided"
        
        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"

        if not media_path.endswith(".ogg"):
            try:
                media_path = audio.convert_to_opus_ogg_temp(media_path)
            except Exception as e:
                return False, f"Error converting file to opus ogg. You likely need to install ffmpeg: {str(e)}"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "media_path": media_path
        }
        
        response = requests.post(url, json=payload)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def download_media(message_id: str, chat_jid: str) -> Tuple[Optional[str], str]:
    """Download media from a message.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        A tuple of (local file path or None on failure, status message from the bridge)
    """
    try:
        url = f"{WHATSAPP_API_BASE_URL}/download"
        payload = {
            "message_id": message_id,
            "chat_jid": chat_jid
        }
        
        response = requests.post(url, json=payload)
        
        try:
            result = response.json()
        except json.JSONDecodeError:
            return None, f"Error: HTTP {response.status_code} - {response.text}"
        
        if response.status_code == 200 and result.get("success", False):
            return result.get("path"), result.get("message", "Media downloaded successfully")
        return None, result.get("message", f"Error: HTTP {response.status_code}")
            
    except requests.RequestException as e:
        return None, f"Request error: {str(e)} (is the WhatsApp bridge running?)"
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"


# Longest side, in pixels, of images handed to the model; larger ones are scaled down
IMAGE_PREVIEW_MAX_SIDE = 1568

def image_preview(path: str, seek_seconds: float = 0) -> Optional[bytes]:
    """Return a JPEG of the image at path (or of the frame at seek_seconds for videos and
    GIFs), scaled down to IMAGE_PREVIEW_MAX_SIDE, or None if ffmpeg cannot read it."""
    side = IMAGE_PREVIEW_MAX_SIDE
    seek = ["-ss", str(seek_seconds)] if seek_seconds else []
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", *seek, "-i", path,
             "-vf", f"scale='min({side},iw)':'min({side},ih)':force_original_aspect_ratio=decrease",
             "-frames:v", "1", "-q:v", "4", "-f", "mjpeg", "pipe:1"],
            capture_output=True, timeout=30, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if not (result and result.stdout) and seek_seconds:
        return image_preview(path)  # shorter than seek_seconds: use the first frame
    return result.stdout if result and result.stdout else None


def transcribe_audio(message_id: str, chat_jid: str, language: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Transcribe an audio message, reusing the saved transcription when there is one.

    Returns:
        A tuple of (transcription or None on failure, status message)
    """
    conn = sqlite3.connect(MESSAGES_DB_PATH)
    try:
        row = conn.execute(
            "SELECT media_type, transcription FROM messages WHERE id = ? AND chat_jid = ?",
            (message_id, chat_jid),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None, f"Message {message_id} not found in chat {chat_jid}"
    if row[0] != "audio":
        return None, f"Message {message_id} is not an audio message (media type: {row[0] or 'none'})"
    if row[1]:
        return row[1], "Transcription loaded from the database"

    path, status = download_media(message_id, chat_jid)
    if not path:
        return None, status

    import transcription  # deferred: loading faster-whisper is slow and only needed here
    try:
        text, language_found = transcription.transcribe_file(path, language)
    except Exception as e:
        return None, f"Transcription failed: {str(e)}"

    conn = sqlite3.connect(MESSAGES_DB_PATH)
    try:
        conn.execute(
            "UPDATE messages SET transcription = ? WHERE id = ? AND chat_jid = ?",
            (text, message_id, chat_jid),
        )
        conn.commit()
    finally:
        conn.close()
    return text, f"Transcribed audio (language: {language_found})"


AUTO_DOWNLOAD_TABLE = "CREATE TABLE IF NOT EXISTS auto_download_chats (jid TEXT PRIMARY KEY)"

def set_auto_download(chat_jid: str, enabled: bool) -> Tuple[bool, str]:
    """Turn automatic media download on or off for a chat."""
    conn = sqlite3.connect(MESSAGES_DB_PATH)
    try:
        if not conn.execute("SELECT 1 FROM chats WHERE jid = ?", (chat_jid,)).fetchone():
            return False, f"Chat {chat_jid} not found"
        conn.execute(AUTO_DOWNLOAD_TABLE)
        if enabled:
            conn.execute("INSERT OR IGNORE INTO auto_download_chats (jid) VALUES (?)", (chat_jid,))
        else:
            conn.execute("DELETE FROM auto_download_chats WHERE jid = ?", (chat_jid,))
        conn.commit()
    finally:
        conn.close()
    state = "enabled" if enabled else "disabled"
    return True, f"Automatic media download {state} for {chat_jid}"

def list_auto_download_chats() -> List[Chat]:
    """Chats whose incoming media is downloaded automatically."""
    conn = sqlite3.connect(MESSAGES_DB_PATH)
    try:
        conn.execute(AUTO_DOWNLOAD_TABLE)
        rows = conn.execute("""
            SELECT a.jid, c.name, c.last_message_time
            FROM auto_download_chats a
            LEFT JOIN chats c ON c.jid = a.jid
            ORDER BY c.name
        """).fetchall()
    finally:
        conn.close()
    return [
        Chat(jid=jid, name=name, last_message_time=datetime.fromisoformat(last) if last else None)
        for jid, name, last in rows
    ]
