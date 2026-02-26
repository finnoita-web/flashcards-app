import streamlit as st
import json
from pathlib import Path
import requests
import re
from pypinyin import pinyin, Style
import datetime

from supabase import create_client

# ---------- SUPABASE CLIENT ----------
supabase = create_client(
    st.secrets["SUPABASE_URL"],
    st.secrets["SUPABASE_KEY"]
)

# We keep `data = {}` only so the rest of the code doesn't crash
# until we replace each section with Supabase calls.
data = {}

# ---------- WORD STORAGE (Supabase) ---------- #

def db_get_words(lang):
    res = supabase.table("words").select("*").eq("lang", lang).order("word").execute()
    return res.data

def db_add_word(entry):
    """Insert a new word and return its ID."""
    resp = supabase.table("words").insert(entry).execute()

    # Supabase returns inserted rows in resp.data
    if resp.data and len(resp.data) > 0:
        return resp.data[0]["id"]

    return None


def create_word_with_lookup(word, comment=""):
    """Create a word with auto dictionary lookup (English or Chinese)."""

    # Detect language
    is_chinese = any("\u4e00" <= ch <= "\u9fff" for ch in word)
    lang_key = "chinese" if is_chinese else "english"

    # ----------------- ENGLISH LOOKUP ----------------- #
    if lang_key == "english":
        info = fetch_freedict_data(word.lower())

        pron = info["ipa"] if info else ""
        meaning = info["meaning"] if info else ""
        audio_url = info["audio_url"] if info else ""

        audio_path = ""
        if audio_url:
            audio_url = download_audio_to_supabase(word, audio_url)

    # ----------------- CHINESE LOOKUP ----------------- #
    else:
        ced = cedict_dict.get(word, {})
        pron = ced.get("pinyin", get_pinyin(word))
        meaning = ced.get("meaning", "")
        audio_path = ""  # no audio for Chinese

    entry = {
        "lang": lang_key,
        "word": word,
        "pron": pron,
        "meaning": meaning,
        "audio": audio_path,
        "comment": comment,
        "srs_interval": 1,
        "srs_due": datetime.date.today().isoformat(),
        "srs_ease": 2.5,
        "srs_reps": 0
    }

    new_id = db_add_word(entry)
    return new_id, entry



def db_update_word(word_id, updates):
    supabase.table("words").update(updates).eq("id", word_id).execute()

def db_delete_word(word_id):
    supabase.table("words").delete().eq("id", word_id).execute()


# ---------- GROUP STORAGE (Supabase) ---------- #

def db_get_groups():
    return supabase.table("groups").select("*").order("name").execute().data

def db_create_group(name):
    supabase.table("groups").insert({"name": name}).execute()

def db_delete_group(group_id):
    supabase.table("groups").delete().eq("id", group_id).execute()

def db_rename_group(group_id, new_name):
    supabase.table("groups").update({"name": new_name}).eq("id", group_id).execute()

def db_get_group_members(group_id):
    rows = supabase.table("group_members").select("word_id").eq("group_id", group_id).execute().data
    return [r["word_id"] for r in rows]

def db_add_word_to_group(group_id, word_id):
    supabase.table("group_members").insert({"group_id": group_id, "word_id": word_id}).execute()

def db_remove_word_from_group(group_id, word_id):
    supabase.table("group_members").delete().eq("group_id", group_id).eq("word_id", word_id).execute()


# ---------- USER STORAGE (Supabase) ---------- #

def db_get_users():
    return supabase.table("users").select("*").order("name").execute().data

def db_create_user(name):
    supabase.table("users").insert({"name": name}).execute()

def db_delete_user(user_id):
    supabase.table("users").delete().eq("id", user_id).execute()

# ---------- SRS STORAGE (Supabase) ---------- #

def db_get_srs(user_id, word_id):
    resp = supabase.table("user_srs") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("word_id", word_id) \
        .execute()

    if resp.data:
        return resp.data[0]
    return None


def db_get_srs_entry(user_id, word_id):
    rows = supabase.table("srs").select("*").eq("user_id", user_id).eq("word_id", word_id).execute().data
    return rows[0] if rows else None

def db_create_srs(user_id, word_id):
    entry = {
        "user_id": user_id,
        "word_id": word_id,
        "srs_interval": 1,
        "srs_due": datetime.date.today().isoformat(),
        "srs_ease": 2.5,
        "srs_reps": 0
    }
    resp = supabase.table("user_srs").insert(entry).execute()
    return resp.data[0]

def db_update_srs(srs_id, updates):
    supabase.table("user_srs") \
        .update(updates) \
        .eq("id", srs_id) \
        .execute()

def db_get_due_cards(user_id):
    today = datetime.date.today().isoformat()
    rows = supabase.table("srs").select("word_id").eq("user_id", user_id).lte("due", today).execute().data
    return [r["word_id"] for r in rows]
    
def srs_next(srs_entry, rating):
    interval = srs_entry["srs_interval"]
    ease = srs_entry["srs_ease"]
    reps = srs_entry["srs_reps"]

    if rating == 0:  # Again
        interval = 1
        ease = max(1.3, ease - 0.2)
        reps = 0
    elif rating == 1:  # Hard
        interval = max(1, int(interval * 1.2))
        ease = max(1.3, ease - 0.15)
        reps += 1
    elif rating == 2:  # Good
        interval = int(interval * ease)
        reps += 1
    elif rating == 3:  # Easy
        interval = int(interval * ease * 1.3)
        ease += 0.1
        reps += 1

    next_due = (datetime.date.today() + datetime.timedelta(days=interval)).isoformat()

    return {
        "srs_interval": interval,
        "srs_ease": ease,
        "srs_reps": reps,
        "srs_due": next_due
    }


# ---------- STUDY HISTORY (Supabase) ---------- #

def db_add_study_event(user_id, date):
    supabase.table("study_history").insert({
        "user_id": user_id,
        "date": date
    }).execute()

def db_get_study_history(user_id):
    rows = supabase.table("study_history").select("date").eq("user_id", user_id).execute().data
    return [r["date"] for r in rows]

def db_get_all_words():
    """Return all English + Chinese words as a single unified list."""
    english = db_get_words("english")
    chinese = db_get_words("chinese")

    # Add lang field to each entry
    for w in english:
        w["lang"] = "english"
    for w in chinese:
        w["lang"] = "chinese"

    return english + chinese


AUDIO_DIR = Path("audio")
AUDIO_DIR.mkdir(exist_ok=True)


# ----------------- CC‑CEDICT loader ----------------- #

cedict_dict = {}

def load_cedict(path="cedict_ts.u8"):
    global cedict_dict
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.strip().split(" ")
                if len(parts) < 3:
                    continue

                trad = parts[0]
                simp = parts[1]
                rest = " ".join(parts[2:])

                pinyin_match = re.search(r"\[(.*?)\]", rest)
                def_match = re.search(r"/(.+?)/", rest)

                if pinyin_match and def_match:
                    cedict_dict[simp] = {
                        "pinyin": pinyin_match.group(1),
                        "meaning": def_match.group(1)
                    }
    except Exception as e:
        st.error(f"CEDICT load error: {e}")
        
#Load dictionary once at startup
load_cedict()


# ----------------- Data helpers ----------------- #

def is_chinese(word: str) -> bool:
    return any('\u4e00' <= ch <= '\u9fff' for ch in word)

def is_single_english_word(word: str) -> bool:
    return word.isalpha() and " " not in word

def get_pinyin(word: str) -> str:
    try:
        py = pinyin(word, style=Style.TONE, heteronym=False)
        return "".join(syll[0] for syll in py)
    except Exception:
        return ""

def pick_uk_ipa_and_audio(phonetics):
    if not phonetics:
        return "", ""

    for ph in phonetics:
        text = ph.get("text") or ""
        audio = ph.get("audio") or ""
        if "uk" in audio.lower():
            return text, audio

    uk_vowels = ["ɒ", "əʊ", "ʌ", "ɪə", "eə", "ɔː"]
    for ph in phonetics:
        text = ph.get("text") or ""
        if any(v in text for v in uk_vowels):
            return text, ph.get("audio", "")

    for ph in phonetics:
        if ph.get("audio"):
            return ph.get("text", ""), ph.get("audio", "")

    for ph in phonetics:
        if ph.get("text"):
            return ph.get("text", ""), ph.get("audio", "")

    return "", ""

def fetch_freedict_data(word: str):
    if not is_single_english_word(word):
        return None

    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word.lower()}"

    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        entries = r.json()
        if not isinstance(entries, list) or not entries:
            return None
    except Exception:
        return None

    ipa = ""
    audio_url = ""
    meaning_list = []

    for entry in entries:
        ipa, audio_url = pick_uk_ipa_and_audio(entry.get("phonetics", []))

        for m in entry.get("meanings", []):
            for d in m.get("definitions", []):
                gloss = d.get("definition")
                if gloss:
                    meaning_list.append(gloss)

        if meaning_list:
            break

    meaning_text = "; ".join(meaning_list[:5]) if meaning_list else ""

    if not ipa and not meaning_text:
        return None

    return {
        "ipa": ipa,
        "meaning": meaning_text,
        "audio_url": audio_url
    }
    
def upload_audio_to_supabase(word: str, audio_url: str):
    """
    Downloads audio from an external URL and uploads it to Supabase Storage.
    Returns a public URL, or empty string on failure.
    """
    if not audio_url:
        return ""

    try:
        # Download audio from dictionary API
        r = requests.get(audio_url, timeout=10)
        if r.status_code != 200:
            return ""

        filename = f"{word.lower()}.mp3"

        # Upload to Supabase Storage bucket "audio"
        supabase.storage.from_("audio").upload(
            path=filename,
            file=r.content,
            file_options={"content-type": "audio/mpeg", "upsert": True}
        )

        # Get public URL
        public_url = supabase.storage.from_("audio").get_public_url(filename)
        return public_url

    except Exception as e:
        print("Audio upload error:", e)
        return ""



def strip_pinyin_tones(p: str) -> str:
    """Convert pinyin with tone marks or numbers to plain letters."""
    tone_map = {
        "ā":"a","á":"a","ǎ":"a","à":"a",
        "ē":"e","é":"e","ě":"e","è":"e",
        "ī":"i","í":"i","ǐ":"i","ì":"i",
        "ō":"o","ó":"o","ǒ":"o","ò":"o",
        "ū":"u","ú":"u","ǔ":"u","ù":"u",
        "ǖ":"ü","ǘ":"ü","ǚ":"ü","ǜ":"ü",
        "ü":"ü"
    }
    for k, v in tone_map.items():
        p = p.replace(k, v)
    p = re.sub(r"[1-5]", "", p)
    return p.lower()

def lookup_chinese_by_pinyin(py: str, tone_sensitive: bool):
    """Return hanzi whose pinyin matches the input, tone-sensitive or not."""
    raw = py.lower().replace(" ", "")
    stripped = strip_pinyin_tones(raw)

    matches = []

    for hanzi, info in cedict_dict.items():
        p = info.get("pinyin", "").lower().replace(" ", "")
        p_stripped = strip_pinyin_tones(p)

        if tone_sensitive:
            # Tone-sensitive: pinyin must match exactly (tone number or tone mark)
            if p.startswith(raw):
                matches.append(hanzi)
        else:
            # Tone-insensitive: compare stripped forms
            if p_stripped == stripped:
                matches.append(hanzi)

    return matches
    
def choose_prompt_type(card):
    """Choose word, meaning, or comment — only from non-empty fields."""
    options = ["word"]
    if card.get("meaning"):
        options.append("meaning")
    if card.get("comment"):
        options.append("comment")

    import random
    return random.choice(options)

# ----------------- Streamlit UI ----------------- #

st.set_page_config(page_title="Flashcards Web App", layout="wide")

st.markdown("""
<style>
/* Make text areas larger and nicer */
textarea {
    font-size: 1.1rem !important;
}

/* Make headers tighter */
h1, h2, h3 {
    margin-top: 0.5rem !important;
}

/* Improve spacing between widgets */
.block-container {
    padding-top: 1rem;
}

/* Make buttons larger and more tappable on mobile */
.stButton>button {
    padding: 0.6rem 1.2rem;
    font-size: 1.05rem;
}

/* Make tables more readable */
.dataframe {
    font-size: 0.95rem;
}

/* ---------------- MOBILE OPTIMIZATION ---------------- */
@media (max-width: 600px) {

    /* Full-width buttons on mobile */
    .stButton>button {
        width: 100% !important;
        margin-bottom: 0.5rem !important;
        font-size: 1.2rem !important;
        padding: 0.9rem 1.2rem !important;
    }

    /* Reduce side padding */
    .block-container {
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
    }

    /* Larger text for readability */
    h1, h2, h3 {
        font-size: 1.4rem !important;
    }

    /* Larger radio/select labels */
    .stRadio > label {
        font-size: 1.1rem !important;
    }

    .stSelectbox > div > div {
        font-size: 1.1rem !important;
    }
}

/* Sticky top bar for mobile scrolling */
.st-emotion-cache-18ni7ap {
    position: sticky;
    top: 0;
    background: white;
    z-index: 999;
    padding-top: 0.5rem;
    padding-bottom: 0.5rem;
}

/* Sticky top bar */
.st-emotion-cache-18ni7ap {
    position: sticky;
    top: 0;
    background: white;
    z-index: 999;
    padding-top: 0.5rem;
    padding-bottom: 0.5rem;
}

</style>
""", unsafe_allow_html=True)

st.title("📚 Flashcard Learning App (Web Version)")

# ----------------- USER SELECTOR (Supabase) ----------------- #

def normalize_username(name: str) -> str:
    name = name.strip()
    if not name:
        return ""
    return " ".join(part.capitalize() for part in name.split())


def get_or_create_user():
    ss = st.session_state
    if "current_user_id" not in ss:
        ss.current_user_id = None
        ss.current_user_name = None

    st.sidebar.markdown("### 👤 User")

    # Fetch users from Supabase
    users = db_get_users()
    user_names = [u["name"] for u in users]

    add_new = "➕ Add new user"

    # If users exist, show dropdown
    if user_names:
        default_index = (
            user_names.index(ss.current_user_name)
            if ss.current_user_name in user_names
            else 0
        )

        choice = st.sidebar.selectbox(
            "Select user",
            options=user_names + [add_new],
            index=default_index
        )
    else:
        # No users yet → show only "Add new user"
        choice = add_new

    # Create new profile
    if choice == add_new:
        new_name = st.sidebar.text_input("Enter your name")

        if st.sidebar.button("Create profile"):
            uname = normalize_username(new_name)

            if not uname:
                st.sidebar.warning("Please enter a non-empty name.")
            elif uname in user_names:
                st.sidebar.warning("A profile with this name already exists.")
            else:
                db_create_user(uname)
                st.sidebar.success(f"Profile '{uname}' created.")
                st.rerun()

        # If user doesn't want to create a profile → use Guest mode
        if not users:
            db_create_user("Guest")
            ss.current_user_id = db_get_users()[0]["id"]
            ss.current_user_name = "Guest"
            st.sidebar.success("Using Guest mode.")
            st.rerun()

        st.stop()


    # Existing user selected
    selected_user = next(u for u in users if u["name"] == choice)
    ss.current_user_id = selected_user["id"]
    ss.current_user_name = selected_user["name"]

    return ss.current_user_id, ss.current_user_name


current_user_id, current_user_name = get_or_create_user()

# ----------------- USER MANAGEMENT (Hidden Behind Checkbox) ----------------- #
st.sidebar.markdown("---")

show_user_mgmt = st.sidebar.checkbox("Show user management")

if show_user_mgmt:
    st.sidebar.markdown("### 🗑️ Manage Users")

    users = db_get_users()
    user_names = [u["name"] for u in users]

    if user_names:
        user_to_delete = st.sidebar.selectbox(
            "Select a user to delete:",
            user_names,
            key="delete_user_select"
        )

        if st.sidebar.button("Delete selected user"):
            if user_to_delete == current_user_name:
                st.sidebar.warning("You cannot delete the user you are currently using.")
            else:
                # Find user ID
                uid = next(u["id"] for u in users if u["name"] == user_to_delete)

                # Delete user
                db_delete_user(uid)

                st.sidebar.success(f"User '{user_to_delete}' deleted.")
                st.rerun()
    else:
        st.sidebar.info("No users to manage.")


# Sidebar navigation
page = st.sidebar.radio(
    "Navigation",
    ["Add Words", "Flashcards", "Study Mode", "Study Groups", "Backup & Restore", "Statistics", "Review Mode (SRS)", "Dictionary Lookup"]
)


# ----------------- PAGE: Add Words (Supabase) ----------------- #
if page == "Add Words":
    st.header("➕ Add New Words")

    with st.expander("Add words", expanded=True):

        language = st.radio("Select language", ["English", "Chinese"])
        lang_key = "english" if language == "English" else "chinese"

        batch_input = st.text_area("Enter words (one per line):")

        if st.button("Add Words"):
            words = [w.strip() for w in batch_input.splitlines() if w.strip()]
            added = 0
            errors = []

            # Fetch existing words for duplicate checking
            existing = db_get_words(lang_key)
            existing_words = {e["word"] for e in existing}

            for w in words:
                w = w.lower()

                # Skip duplicates
                if w in existing_words:
                    continue

                # ----------------- English ----------------- #
                if lang_key == "english":

                    # Parse optional comment using "/"
                    if "/" in w:
                        raw_word, comment = [x.strip() for x in w.split("/", 1)]
                    else:
                        raw_word, comment = w, ""

                    word = raw_word.replace("_", " ")
                    is_multiword = (" " in word)

                    # Skip duplicates again (after replacing "_")
                    if word in existing_words:
                        continue

                    # Multi-word expressions → skip FreeDict
                    if is_multiword:
                        entry = {
                            "lang": "english",
                            "word": word,
                            "pron": "",
                            "meaning": "",
                            "audio": "",
                            "comment": comment,
                            "srs_interval": 1,
                            "srs_due": datetime.date.today().isoformat(),
                            "srs_ease": 2.5,
                            "srs_reps": 0
                        }
                        db_add_word(entry)
                        added += 1
                        continue

                    # Single-word English → FreeDict lookup
                    info = fetch_freedict_data(word)

                    if not info:
                        entry = {
                            "lang": "english",
                            "word": word,
                            "pron": "",
                            "meaning": "",
                            "audio": "",
                            "comment": comment,
                            "srs_interval": 1,
                            "srs_due": datetime.date.today().isoformat(),
                            "srs_ease": 2.5,
                            "srs_reps": 0
                        }
                        db_add_word(entry)
                        added += 1
                        continue

                    # FreeDict success → upload audio to Supabase Storage
                    audio_url = upload_audio_to_supabase(word, info["audio_url"])

                    entry = {
                        "lang": "english",
                        "word": word,
                        "pron": info["ipa"],
                        "meaning": info["meaning"],
                        "audio": audio_url,   # <-- URL now, not local path
                        "comment": comment,
                        "srs_interval": 1,
                        "srs_due": datetime.date.today().isoformat(),
                        "srs_ease": 2.5,
                        "srs_reps": 0
                    }
                    db_add_word(entry)
                    added += 1

                # ----------------- Chinese ----------------- #
                else:
                    if not is_chinese(w):
                        matches = lookup_chinese_by_pinyin(w, tone_sensitive=False)
                        if not matches:
                            errors.append(f"Not Chinese or valid pinyin: {w}")
                            continue
                        if len(matches) > 1:
                            chosen = st.selectbox(f"Multiple matches for '{w}'", matches)
                            w = chosen
                        else:
                            w = matches[0]

                    ced = cedict_dict.get(w, {})
                    entry = {
                        "lang": "chinese",
                        "word": w,
                        "pron": ced.get("pinyin", get_pinyin(w)),
                        "meaning": ced.get("meaning", ""),
                        "audio": "",  # Chinese has no audio
                        "comment": "",
                        "srs_interval": 1,
                        "srs_due": datetime.date.today().isoformat(),
                        "srs_ease": 2.5,
                        "srs_reps": 0
                    }
                    db_add_word(entry)
                    added += 1

            st.success(f"Added {added} new word(s).")

            if errors:
                st.error("Some words could not be added:")
                for e in errors:
                    st.write("- " + e)

    # ----------------- IMPORT WORDS ----------------- #
    with st.expander("📥 Import Words From File", expanded=False):
        uploaded = st.file_uploader("Upload CSV or TXT file", type=["csv", "txt"])

        import_lang = st.radio("Import as:", ["English", "Chinese"], key="import_lang")
        lang_key = "english" if import_lang == "English" else "chinese"

        if uploaded:
            content = uploaded.read().decode("utf-8").strip().splitlines()
            st.write(f"Detected {len(content)} lines.")

            if st.button("Import Words"):
                added = 0

                existing = db_get_words(lang_key)
                existing_words = {e["word"] for e in existing}

                for line in content:
                    word = line.strip().lower()
                    if not word:
                        continue

                    if word in existing_words:
                        continue

                    # English import
                    if lang_key == "english":
                        info = fetch_freedict_data(word)

                        if not info:
                            entry = {
                                "lang": "english",
                                "word": word,
                                "pron": "",
                                "meaning": "",
                                "audio": "",
                                "comment": "",
                                "srs_interval": 1,
                                "srs_due": datetime.date.today().isoformat(),
                                "srs_ease": 2.5,
                                "srs_reps": 0
                            }
                            db_add_word(entry)
                            added += 1
                            continue

                        # Upload audio to Supabase Storage
                        audio_url = upload_audio_to_supabase(word, info["audio_url"])

                        entry = {
                            "lang": "english",
                            "word": word,
                            "pron": info["ipa"],
                            "meaning": info["meaning"],
                            "audio": audio_url,
                            "comment": "",
                            "srs_interval": 1,
                            "srs_due": datetime.date.today().isoformat(),
                            "srs_ease": 2.5,
                            "srs_reps": 0
                        }
                        db_add_word(entry)
                        added += 1

                    # Chinese import
                    else:
                        ced = cedict_dict.get(word, {})
                        entry = {
                            "lang": "chinese",
                            "word": word,
                            "pron": ced.get("pinyin", get_pinyin(word)),
                            "meaning": ced.get("meaning", ""),
                            "audio": "",
                            "comment": "",
                            "srs_interval": 1,
                            "srs_due": datetime.date.today().isoformat(),
                            "srs_ease": 2.5,
                            "srs_reps": 0
                        }
                        db_add_word(entry)
                        added += 1

                st.success(f"Imported {added} words.")


# ----------------- PAGE: Flashcards (Supabase) ----------------- #
elif page == "Flashcards":
    st.header("📄 Flashcards")

    language = st.radio("Select language", ["English", "Chinese"])
    lang_key = "english" if language == "English" else "chinese"

    # Fetch words from Supabase
    entries = db_get_words(lang_key)

    if not entries:
        st.info("No words yet.")
        st.stop()

    # ----------------- TABLE VIEW ----------------- #
    with st.expander("Flashcards Table", expanded=True):

        search_query = st.text_input("Search", placeholder="Type to filter words...")
        filter_audio = st.checkbox("Only words with audio")
        filter_comment = st.checkbox("Only words with comments")

        sort_by = st.selectbox(
            "Sort by",
            ["Word (A→Z)", "Word (Z→A)", "Meaning length", "Has audio", "Has comment"]
        )

        filtered = entries

        # Search filter
        if search_query:
            q = search_query.lower()
            filtered = [
                e for e in filtered
                if q in e["word"].lower()
                or q in (e.get("meaning") or "").lower()
                or q in (e.get("comment") or "").lower()
            ]

        # Audio filter
        if filter_audio:
            filtered = [e for e in filtered if e.get("audio")]

        # Comment filter
        if filter_comment:
            filtered = [e for e in filtered if e.get("comment")]

        # Sorting
        if sort_by == "Word (A→Z)":
            filtered = sorted(filtered, key=lambda e: e["word"].lower())
        elif sort_by == "Word (Z→A)":
            filtered = sorted(filtered, key=lambda e: e["word"].lower(), reverse=True)
        elif sort_by == "Meaning length":
            filtered = sorted(filtered, key=lambda e: len(e.get("meaning") or ""))
        elif sort_by == "Has audio":
            filtered = sorted(filtered, key=lambda e: bool(e.get("audio")), reverse=True)
        elif sort_by == "Has comment":
            filtered = sorted(filtered, key=lambda e: bool(e.get("comment")), reverse=True)

        # Display table
        df = [
            {
                "Word": ("❗ " + e["word"]) if not e.get("meaning") else e["word"],
                "Pronunciation": e.get("pron", ""),
                "Meaning": e.get("meaning", ""),
                "Comment": e.get("comment", ""),
                "Audio": "Yes" if e.get("audio") else "No"
            }
            for e in filtered
        ]

        st.dataframe(df, use_container_width=True, height=450)

    # ----------------- EDIT / DELETE WORD ----------------- #
    with st.expander("Edit or Delete Word", expanded=False):

        selected_word = st.selectbox(
            "Choose a word",
            [e["word"] for e in entries]
        )

        entry = next(e for e in entries if e["word"] == selected_word)

        # Auto-open edit mode for missing meaning
        if not entry.get("meaning"):
            st.session_state["edit_mode"] = True

        col1, col2 = st.columns(2)

        # DELETE WORD
        with col2:
            if st.button("🗑️ Delete Word"):
                db_delete_word(entry["id"])
                st.success(f"Deleted '{selected_word}'.")
                st.rerun()

        # EDIT WORD
        with col1:
            if st.button("✏️ Edit Word"):
                st.session_state["edit_mode"] = True

        if st.session_state.get("edit_mode", False):
            with st.expander(f"Editing: {selected_word}", expanded=True):
                with st.form("edit_form"):
                    new_word = st.text_input("Word", entry["word"])
                    new_pron = st.text_input("Pronunciation", entry.get("pron", ""))
                    new_meaning = st.text_area(
                        "Meaning",
                        entry.get("meaning", ""),
                        placeholder="Enter meaning (required for fallback words)"
                    )
                    new_comment = st.text_area("Comment", entry.get("comment", ""))

                    submitted = st.form_submit_button("Save Changes")

                    if submitted:
                        db_update_word(entry["id"], {
                            "word": new_word,
                            "pron": new_pron,
                            "meaning": new_meaning,
                            "comment": new_comment
                        })

                        st.success("Changes saved.")
                        st.session_state["edit_mode"] = False
                        st.rerun()

    # ----------------- AUDIO DOWNLOAD ----------------- #
    if st.button("Download/Redownload Audio"):
        if lang_key == "english":
            info = fetch_freedict_data(entry["word"])
            if info and info["audio_url"]:
                audio_path = download_audio(entry["word"], info["audio_url"])
                db_update_word(entry["id"], {"audio": audio_path})
                st.success("Audio downloaded.")
            else:
                st.error("No audio available for this word.")
        else:
            st.info("Audio download is only available for English words.")

# ----------------- PAGE: Study Mode (Supabase) ----------------- #
elif page == "Study Mode":
    st.header("🎓 Study Mode")

    ss = st.session_state

    # ---------- Session State Initialization ----------
    if "study_list" not in ss:
        ss.study_list = []
    if "study_index" not in ss:
        ss.study_index = 0
    if "revealed" not in ss:
        ss.revealed = False
    if "mixed_mode" not in ss:
        ss.mixed_mode = False
    if "reverse_mode" not in ss:
        ss.reverse_mode = False

    # ---------- Prompt chooser ----------
    def choose_prompt_type(card):
        if ss.reverse_mode:
            if card.get("comment"):
                return "comment"
            elif card.get("meaning"):
                return "meaning"
            else:
                return "word"
        elif ss.mixed_mode:
            options = ["word"]
            if card.get("meaning"):
                options.append("meaning")
            if card.get("comment"):
                options.append("comment")
            import random
            return random.choice(options)
        else:
            return "word"

    # ---------- Study Setup ----------
    if not ss.study_list:
        with st.expander("Study Settings", expanded=True):

            study_source = st.radio(
                "Study:",
                ["All English Words", "All Chinese Words", "Study Group"]
            )

            shuffle = st.checkbox("Shuffle cards")
            ss.mixed_mode = st.checkbox("Mixed prompt mode (word / meaning / comment)")
            ss.reverse_mode = st.checkbox("Reverse mode (meaning → word)")

            selected_group = None
            if study_source == "Study Group":
                groups = db_get_groups()
                if not groups:
                    st.warning("No groups available.")
                    st.stop()

                group_names = [g["name"] for g in groups]
                selected_group_name = st.selectbox("Select a group", group_names)
                selected_group = next(g for g in groups if g["name"] == selected_group_name)

            if st.button("Start Study"):
                today = datetime.date.today().isoformat()

                # Record study history
                history = db_get_study_history(current_user_id)
                if today not in history:
                    db_add_study_event(current_user_id, today)

                # Build study list
                if study_source == "All English Words":
                    ss.study_list = db_get_words("english")

                elif study_source == "All Chinese Words":
                    ss.study_list = db_get_words("chinese")

                else:  # Study Group
                    member_ids = db_get_group_members(selected_group["id"])
                    all_words = db_get_words("english") + db_get_words("chinese")
                    word_map = {w["id"]: w for w in all_words}
                    ss.study_list = [word_map[mid] for mid in member_ids if mid in word_map]

                if shuffle:
                    import random
                    random.shuffle(ss.study_list)

                ss.study_index = 0
                ss.revealed = False

                if not ss.study_list:
                    st.warning("No cards available.")
                    st.stop()

                st.rerun()

    # ---------- Study Session ----------
    if ss.study_list:
        card = ss.study_list[ss.study_index]
        prompt_type = choose_prompt_type(card)

        st.subheader(f"Card {ss.study_index + 1} / {len(ss.study_list)}")

        # ---------- PROMPT ----------
        if not ss.revealed:
            if prompt_type == "word":
                st.markdown(f"### **{card['word']}**")
            elif prompt_type == "meaning":
                st.markdown(f"### **{card.get('meaning', '')}**")
            else:
                st.markdown(f"### **{card.get('comment', '')}**")
        else:
            st.markdown("---")
            st.markdown(f"**Word:** {card['word']}")
            st.markdown(f"**Pronunciation:** {card.get('pron', '')}")
            st.markdown(f"**Meaning:** {card.get('meaning', '')}")
            st.markdown(f"**Comment:** {card.get('comment', '')}")

            # 🔊 AUDIO SUPPORT (Supabase Storage URL)
            audio_url = card.get("audio", "")

            # Only play if it's a valid URL
            if audio_url and audio_url.startswith("http"):
                try:
                    st.audio(audio_url)
                except Exception as e:
                    st.warning("Audio could not be played.")


        st.markdown("---")

        # ---------- BUTTON ROW ----------
        col_prev, col_reveal, col_next, col_end = st.columns(4)

        # PREVIOUS
        with col_prev:
            if st.button("Previous"):
                if ss.study_index > 0:
                    ss.study_index -= 1
                    ss.revealed = False
                    st.rerun()

        # REVEAL
        with col_reveal:
            if st.button("Reveal"):
                ss.revealed = True
                st.rerun()

        # NEXT
        with col_next:
            if st.button("Next"):
                if ss.study_index < len(ss.study_list) - 1:
                    ss.study_index += 1
                    ss.revealed = False
                    st.rerun()

        # END SESSION
        with col_end:
            if st.button("End"):
                ss.study_list = []
                ss.study_index = 0
                ss.revealed = False
                st.rerun()


# ----------------- PAGE: Study Groups ----------------- #
elif page == "Study Groups":
    st.header("📂 Study Groups")

    # Fetch groups and words
    groups = db_get_groups()  # returns list of {id, name}
    all_words = db_get_all_words()  # returns list of all words with id, lang, word, pron, meaning, comment

    # ----------------- CREATE NEW GROUP ----------------- #
    with st.expander("Create New Group", expanded=False):
        new_group_name = st.text_input("Group name", key="new_group_name")

        if st.button("Create Group"):
            if not new_group_name.strip():
                st.warning("Group name cannot be empty.")
            elif any(g["name"] == new_group_name for g in groups):
                st.warning("A group with this name already exists.")
            else:
                db_create_group(new_group_name)
                st.success(f"Group '{new_group_name}' created.")
                st.rerun()

    if not groups:
        st.info("No groups yet. Create one above.")
        st.stop()

    # ----------------- SELECT GROUP ----------------- #
    with st.expander("Select Group", expanded=True):
        group_names = [g["name"] for g in groups]
        selected_group_name = st.selectbox("Select a group", group_names)
        selected_group = next(g for g in groups if g["name"] == selected_group_name)

        # Fetch group members
        member_ids = db_get_group_members(selected_group["id"])
        group_words = [w for w in all_words if w["id"] in member_ids]

    # ----------------- ADD WORDS TO GROUP ----------------- #
    with st.expander("Add Words to Group", expanded=False):

        # ============================================================
        # 1️⃣ BATCH ADD WORDS (TEXTAREA, OLD STYLE)
        # ============================================================
        st.markdown("### Batch Add Words")

        batch_input = st.text_area(
            "Enter words (one per line). Use '_' for spaces and '/' for comments.\n"
            "Example: look_up / to search",
            key="batch_input"
        )

        if st.button("Add Batch to Group"):
            lines = [line.strip() for line in batch_input.splitlines() if line.strip()]
            added = 0

            for line in lines:
                # Parse "word / comment"
                if "/" in line:
                    raw_word, comment = [x.strip() for x in line.split("/", 1)]
                else:
                    raw_word, comment = line, ""

                word = raw_word.replace("_", " ")

                # Check if word exists
                match = next((x for x in all_words if x["word"] == word), None)

                if not match:
                    # Create with dictionary lookup
                    new_id, entry = create_word_with_lookup(word, comment)
                    match = {"id": new_id, **entry}
                    all_words.append(match)

                # Add to group
                if match["id"] not in member_ids:
                    db_add_word_to_group(selected_group["id"], match["id"])
                    added += 1

            st.success(f"Added {added} word(s) to the group.")
            st.rerun()


        st.markdown("---")

        # ============================================================
        # 2️⃣ ADD SINGLE WORD
        # ============================================================
        st.markdown("### Add Single Word")

        language = st.radio("Select language", ["English", "Chinese"], key="group_lang_single")
        lang_key = "english" if language == "English" else "chinese"

        available_words = [w for w in all_words if w["lang"] == lang_key]

        if available_words:
            selected_word = st.selectbox(
                "Choose a word to add",
                [w["word"] for w in available_words],
                key="single_word_choice"
            )

            if st.button("Add to Group (Single)"):
                match = next(w for w in available_words if w["word"] == selected_word)
                if match["id"] not in member_ids:
                    db_add_word_to_group(selected_group["id"], match["id"])
                    st.success(f"Added '{selected_word}' to group.")
                    st.rerun()
                else:
                    st.info(f"'{selected_word}' is already in this group.")
        else:
            st.info(f"No {language} words available.")

    # ----------------- WORDS IN THIS GROUP ----------------- #
    with st.expander("Words in This Group", expanded=True):
        if not group_words:
            st.info("This group is empty.")
        else:
            display_rows = []
            for w in group_words:
                display_rows.append({
                    "Word": w["word"],
                    "Pron": w.get("pron", ""),
                    "Meaning": w.get("meaning", ""),
                    "Comment": w.get("comment", "")
                })

            st.dataframe(display_rows, use_container_width=True)
            
           
    # ----------------- EDIT WORD IN GROUP ----------------- #
    with st.expander("Edit Word in This Group", expanded=False):
        if group_words:
            edit_choice = st.selectbox(
                "Select word to edit",
                group_words,
                format_func=lambda w: w["word"],
                key="edit_choice"
            )

            # Pre-fill fields
            new_word = st.text_input("Word", edit_choice["word"])
            new_pron = st.text_input("Pronunciation", edit_choice.get("pron", ""))
            new_meaning = st.text_area("Meaning", edit_choice.get("meaning", ""))
            new_comment = st.text_area("Comment", edit_choice.get("comment", ""))

            # Optional audio replacement
            new_audio = st.file_uploader("Replace audio (optional)", type=["mp3"])

            if st.button("Save Changes"):
                update_data = {
                    "word": new_word,
                    "pron": new_pron,
                    "meaning": new_meaning,
                    "comment": new_comment,
                }

                # Upload new audio to Supabase Storage
                if new_audio:
                    filename = f"{new_word.lower()}.mp3"

                    supabase.storage.from_("audio").upload(
                        path=filename,
                        file=new_audio.read(),
                        file_options={"content-type": "audio/mpeg", "upsert": True}
                    )

                    audio_url = supabase.storage.from_("audio").get_public_url(filename)
                    update_data["audio"] = audio_url

                # Update in Supabase
                db_update_word(edit_choice["id"], update_data)

                st.success("Word updated.")
                st.rerun()
        else:
            st.info("No words to edit.")


    # ----------------- REMOVE WORD ----------------- #
    with st.expander("Remove Word From Group", expanded=False):
        if group_words:
            remove_choice = st.selectbox(
                "Select word to remove",
                group_words,
                format_func=lambda w: w["word"],
                key="remove_choice"
            )

            if st.button("Remove From Group"):
                db_remove_word_from_group(selected_group["id"], remove_choice["id"])
                st.success("Word removed from group.")
                st.rerun()
        else:
            st.info("No words to remove.")

    # ----------------- DELETE GROUP ----------------- #
    with st.expander("Delete Group", expanded=False):
        if st.button("Delete This Group"):
            db_delete_group(selected_group["id"])
            st.success(f"Group '{selected_group_name}' deleted.")
            st.rerun()



# ----------------- PAGE: Backup & Restore (Supabase) ----------------- #
elif page == "Backup & Restore":
    st.header("💾 Backup & Restore")

    st.markdown("You can export all your data or restore from a backup file.")

    # ---------- EXPORT ----------
    st.subheader("📤 Export Backup")

    if st.button("Download Backup"):
        # Fetch all data from Supabase
        english = db_get_words("english")
        chinese = db_get_words("chinese")
        groups = db_get_groups()
        users = db_get_users()

        # Fetch group members
        group_members = supabase.table("group_members").select("*").execute().data

        # Fetch SRS
        srs = supabase.table("srs").select("*").execute().data

        # Fetch study history
        study_history = supabase.table("study_history").select("*").execute().data

        backup = {
            "words": {
                "english": english,
                "chinese": chinese
            },
            "groups": groups,
            "group_members": group_members,
            "users": users,
            "srs": srs,
            "study_history": study_history
        }

        st.download_button(
            label="Download JSON Backup",
            data=json.dumps(backup, ensure_ascii=False, indent=2),
            file_name="flashcards_backup.json",
            mime="application/json"
        )

    st.markdown("---")

    # ---------- IMPORT ----------
    st.subheader("📥 Restore Backup")

    uploaded = st.file_uploader("Upload backup JSON file", type=["json"])

    if uploaded:
        try:
            backup = json.loads(uploaded.read().decode("utf-8"))
        except Exception:
            st.error("Invalid JSON file.")
            st.stop()

        if st.button("Restore Backup"):
            st.warning("Restoring backup… This will overwrite existing data.")

            # Clear existing tables
            supabase.table("group_members").delete().neq("group_id", "").execute()
            supabase.table("groups").delete().neq("id", "").execute()
            supabase.table("srs").delete().neq("id", "").execute()
            supabase.table("study_history").delete().neq("id", "").execute()
            supabase.table("users").delete().neq("id", "").execute()
            supabase.table("words").delete().neq("id", "").execute()

            # Restore words
            for w in backup["words"]["english"]:
                w["lang"] = "english"
                db_add_word(w)

            for w in backup["words"]["chinese"]:
                w["lang"] = "chinese"
                db_add_word(w)

            # Restore groups
            for g in backup["groups"]:
                supabase.table("groups").insert(g).execute()

            # Restore group members
            for gm in backup["group_members"]:
                supabase.table("group_members").insert(gm).execute()

            # Restore users
            for u in backup["users"]:
                supabase.table("users").insert(u).execute()

            # Restore SRS
            for s in backup["srs"]:
                supabase.table("srs").insert(s).execute()

            # Restore study history
            for h in backup["study_history"]:
                supabase.table("study_history").insert(h).execute()

            st.success("Backup restored successfully.")
            st.rerun()


# ----------------- PAGE: Statistics (Supabase) ----------------- #
elif page == "Statistics":
    st.header("📊 Statistics")

    # ---------- Fetch study history ----------
    history = db_get_study_history(current_user_id)

    # Convert to date objects
    dates = [datetime.date.fromisoformat(d) for d in history]
    dates_sorted = sorted(dates)

    # ---------- Compute streaks ----------
    def compute_streaks(dates):
        if not dates:
            return 0, 0

        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)

        # Current streak
        streak = 0
        day = today
        while day in dates:
            streak += 1
            day -= datetime.timedelta(days=1)

        # If today wasn't studied but yesterday was
        if today not in dates and yesterday in dates:
            streak = 0

        # Longest streak
        longest = 1
        current = 1
        for i in range(1, len(dates)):
            if dates[i] == dates[i-1] + datetime.timedelta(days=1):
                current += 1
                longest = max(longest, current)
            else:
                current = 1

        return streak, longest

    current_streak, longest_streak = compute_streaks(dates_sorted)

    # ---------- Word counts ----------
    english_words = db_get_words("english")
    chinese_words = db_get_words("chinese")

    total_words = len(english_words) + len(chinese_words)

    # ---------- SRS counts ----------
    due_ids = db_get_due_cards(current_user_id)
    total_srs = len(due_ids)

    # ---------- Groups ----------
    groups = db_get_groups()
    total_groups = len(groups)

    # ---------- Display ----------
    st.subheader("📈 Overview")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Total Words", total_words)

    with col2:
        st.metric("Study Groups", total_groups)

    with col3:
        st.metric("Due for Review", total_srs)

    st.markdown("---")

    st.subheader("🔥 Study Streaks")

    colA, colB = st.columns(2)

    with colA:
        st.metric("Current Streak", current_streak)

    with colB:
        st.metric("Longest Streak", longest_streak)

    st.markdown("---")

    st.subheader("📅 Study History")

    if not dates_sorted:
        st.info("No study history yet.")
    else:
        st.write("You studied on these days:")
        for d in dates_sorted:
            st.write("• " + d.isoformat())


# ----------------- PAGE: Review Mode (SRS) ----------------- #
elif page == "Review Mode (SRS)":
    st.header("🧠 Review Mode (Spaced Repetition)")

    ss = st.session_state
    today = datetime.date.today().isoformat()

    # ---------- Session State Initialization ----------
    if "srs_list" not in ss:
        ss.srs_list = []
    if "srs_index" not in ss:
        ss.srs_index = 0
    if "srs_revealed" not in ss:
        ss.srs_revealed = False
    if "srs_prompt_type" not in ss:
        ss.srs_prompt_type = "word"
    if "srs_mixed_mode" not in ss:
        ss.srs_mixed_mode = False
    if "srs_reverse_mode" not in ss:
        ss.srs_reverse_mode = False

    # ---------- Review Source ----------
    review_source = st.radio(
        "Review:",
        ["All English Words", "All Chinese Words", "Study Group"],
        key="srs_review_source"
    )

    selected_group = None
    if review_source == "Study Group":
        groups = db_get_groups()
        if not groups:
            st.warning("No study groups available.")
            st.stop()

        group_names = [g["name"] for g in groups]
        selected_name = st.selectbox("Select a group", group_names)
        selected_group = next(g for g in groups if g["name"] == selected_name)

    # ---------- Review Settings ----------
    st.markdown("### Review Settings")
    ss.srs_mixed_mode = st.checkbox(
        "Mixed prompt mode (word / meaning / comment)",
        value=ss.srs_mixed_mode
    )
    ss.srs_reverse_mode = st.checkbox(
        "Reverse mode (meaning → word)",
        value=ss.srs_reverse_mode
    )
    shuffle = st.checkbox("Shuffle cards", value=False)
    review_ahead = st.checkbox("📅 Review ahead (ignore due dates)", value=False)

    # ---------- Prompt chooser ----------
    def choose_srs_prompt_type(word_entry):
        if ss.srs_reverse_mode:
            if word_entry.get("comment"):
                return "comment"
            if word_entry.get("meaning"):
                return "meaning"
            return "word"

        if ss.srs_mixed_mode:
            return choose_prompt_type(word_entry)

        return "word"

    # ---------- Collect Due Cards ----------
    def collect_due_cards():
        due_cards = []

        # 1. Load words based on review source
        if review_source == "All English Words":
            words = db_get_words("english")
        elif review_source == "All Chinese Words":
            words = db_get_words("chinese")
        else:
            # Study Group
            member_ids = db_get_group_members(selected_group["id"])
            all_words = db_get_all_words()
            words = [w for w in all_words if w["id"] in member_ids]

        # 2. For each word, fetch or create SRS entry
        for w in words:
            srs = db_get_srs(current_user_name, w["id"])
            if not srs:
                srs = db_create_srs(current_user_name, w["id"])

            # 3. Check due date
            if review_ahead or srs["srs_due"] <= today:
                due_cards.append((w, srs))

        return due_cards

    # ---------- Restart Button ----------
    if st.button("🔄 Restart Review"):
        ss.srs_list = []
        ss.srs_index = 0
        ss.srs_revealed = False
        st.rerun()

    # ---------- Initialize Session ----------
    if not ss.srs_list:
        due_cards = collect_due_cards()

        if not due_cards:
            st.success("🎉 No cards due today!")
            st.markdown("You can:")
            st.markdown("- Change the review source above")
            st.markdown("- Select a different study group")
            st.markdown("- Enable 'Review ahead'")
            st.markdown("- Or restart the session")
            st.stop()

        if shuffle:
            import random
            random.shuffle(due_cards)

        ss.srs_list = due_cards
        ss.srs_index = 0
        ss.srs_revealed = False
        ss.srs_prompt_type = choose_srs_prompt_type(ss.srs_list[0][0])

    # ---------- Show Current Card ----------
    word_entry, srs_entry = ss.srs_list[ss.srs_index]
    
    prompt_type = ss.srs_prompt_type

    if prompt_type == "word":
        st.markdown(f"## **{word_entry['word']}**")
    elif prompt_type == "meaning":
        st.markdown("### Meaning")
        st.write(word_entry.get("meaning", ""))
    elif prompt_type == "comment":
        st.markdown("### Comment")
        st.info(word_entry.get("comment", ""))

    # ---------- Reveal ----------
    if ss.srs_revealed:
        if prompt_type != "word":
            st.markdown(f"### Word\n**{word_entry['word']}**")

        st.markdown(f"**Pronunciation:** {word_entry.get('pron', '')}")

        if prompt_type != "meaning":
            st.markdown("### Meaning")
            st.write(word_entry.get("meaning", ""))

        if word_entry.get("comment") and prompt_type != "comment":
            st.markdown("### Comment")
            st.info(word_entry["comment"])

        # Audio playback
        if word_entry.get("audio"):
            st.audio(word_entry["audio"])

    # ---------- Reveal Button ----------
    if not ss.srs_revealed:
        if st.button("Reveal"):
            ss.srs_revealed = True
            st.rerun()
    else:
        # ---------- Rating Buttons ----------
        st.markdown("### How well did you remember?")
        col1, col2, col3, col4 = st.columns(4)

        rating = None
        if col1.button("🔁 Again"):
            rating = 0
        elif col2.button("😐 Hard"):
            rating = 1
        elif col3.button("🙂 Good"):
            rating = 2
        elif col4.button("😄 Easy"):
            rating = 3

        if rating is not None:
            updates = srs_next(srs_entry, rating)
            db_update_srs(srs_entry["id"], updates)

            ss.srs_index += 1
            ss.srs_revealed = False

            if ss.srs_index >= len(ss.srs_list):
                st.success("✅ Review complete!")
                ss.srs_list = []
                ss.srs_index = 0
            else:
                next_word, _ = ss.srs_list[ss.srs_index]
                ss.srs_prompt_type = choose_srs_prompt_type(next_word)

            st.rerun()


        
# ----------------- PAGE: Dictionary Lookup (3‑Mode Version) ----------------- #
elif page == "Dictionary Lookup":
    st.header("📖 Dictionary Lookup")

    # --- Radiobutton with state tracking for input reset ---
    lookup_mode = st.radio(
        "Choose lookup mode:",
        ["English Meaning", "Chinese Meaning", "English → Chinese Translation"],
        horizontal=True,
        key="lookup_mode"
    )

    # Reset input when lookup mode changes
    if "last_lookup_mode" not in st.session_state:
        st.session_state.last_lookup_mode = lookup_mode

    if st.session_state.last_lookup_mode != lookup_mode:
        st.session_state.lookup_input = ""   # clear input
        st.session_state.last_lookup_mode = lookup_mode
        st.rerun()

    lookup_word = st.text_input("Enter a word:", key="lookup_input").strip()
    if not lookup_word:
        st.stop()

    # Fetch user's existing words
    english_words = db_get_words("english")
    chinese_words = db_get_words("chinese")
    user_words = {w["word"] for w in english_words + chinese_words}

    # ============================================================
    #                       1️⃣ ENGLISH MEANING
    # ============================================================
    if lookup_mode == "English Meaning":
        word = lookup_word.lower()

        if not is_single_english_word(word):
            st.error("Please enter a single English word.")
            st.stop()

        info = fetch_freedict_data(word)

        if not info:
            st.warning("No dictionary entry found.")
            st.stop()

        st.markdown(f"## {word}")
        st.write(f"**IPA:** {info['ipa']}")
        st.write(f"**Meaning:** {info['meaning']}")

        # ---------- AUDIO ----------
        audio_path = ""
        if info["audio_url"]:
            audio_path = download_audio(word, info["audio_url"])
            if audio_path:
                st.audio(audio_path)

        # ---------- ADD TO WORD LIST ----------
        comment = st.text_area("Add a comment (optional):")

        if word in user_words:
            st.success("Already in your word list.")
        else:
            if st.button("➕ Add to English Words"):
                entry = {
                    "lang": "english",
                    "word": word,
                    "pron": info["ipa"],
                    "meaning": info["meaning"],
                    "audio": audio_path,
                    "comment": comment,
                    "srs_interval": 1,
                    "srs_due": datetime.date.today().isoformat(),
                    "srs_ease": 2.5,
                    "srs_reps": 0
                }
                db_add_word(entry)
                st.success(f"Added '{word}' to English words.")
                st.rerun()

        st.stop()

    # ============================================================
    #                       2️⃣ CHINESE MEANING
    # ============================================================
    if lookup_mode == "Chinese Meaning":
        tone_sensitive = st.checkbox("Tone‑sensitive search", value=False)

        # ---------- HANZI INPUT ----------
        if is_chinese(lookup_word):
            hanzi = lookup_word

        # ---------- PINYIN INPUT ----------
        else:
            matches = lookup_chinese_by_pinyin(lookup_word, tone_sensitive)

            if not matches:
                st.error("No Chinese characters found for this pinyin.")
                st.stop()

            if len(matches) == 1:
                hanzi = matches[0]
            else:
                hanzi = st.selectbox(
                    "Multiple characters match this pinyin. Choose one:",
                    matches
                )

        # ---------- DISPLAY ----------
        st.markdown(f"## {hanzi}")

        ced = cedict_dict.get(hanzi, {})
        pinyin_text = ced.get("pinyin", get_pinyin(hanzi))
        meaning_text = ced.get("meaning", "")

        st.write(f"**Pinyin:** {pinyin_text}")
        st.write(f"**Meaning:** {meaning_text}")

        # ---------- ADD TO WORD LIST ----------
        comment = st.text_area("Add a comment (optional):")

        if hanzi in user_words:
            st.success("Already in your word list.")
        else:
            if st.button("➕ Add to Chinese Words"):
                entry = {
                    "lang": "chinese",
                    "word": hanzi,
                    "pron": pinyin_text,
                    "meaning": meaning_text,
                    "audio": "",
                    "comment": comment,
                    "srs_interval": 1,
                    "srs_due": datetime.date.today().isoformat(),
                    "srs_ease": 2.5,
                    "srs_reps": 0
                }
                db_add_word(entry)
                st.success(f"Added '{hanzi}' to Chinese words.")
                st.rerun()

        st.stop()

    # ============================================================
    #                3️⃣ ENGLISH → CHINESE TRANSLATION
    # ============================================================
    if lookup_mode == "English → Chinese Translation":
        word = lookup_word.lower()

        if not is_single_english_word(word):
            st.error("Please enter a single English word.")
            st.stop()

        # Find all Chinese words whose meaning contains the English word
        matches = []
        for hanzi, info in cedict_dict.items():
            if word in info.get("meaning", "").lower():
                matches.append((hanzi, info["pinyin"], info["meaning"]))

        if not matches:
            st.warning("No Chinese translations found.")
            st.stop()

        st.markdown(f"## Chinese translations for '{word}'")

        # ---------- SHOW ALL MATCHES ----------
        for hanzi, pinyin_text, meaning_text in matches[:50]:
            st.write(f"**{hanzi}** — {pinyin_text} — {meaning_text}")

        st.markdown("---")

        # ---------- POPUP SELECTOR (C1) ----------
        hanzi_choices = [m[0] for m in matches]
        chosen = st.selectbox("Select one to add:", hanzi_choices)

        chosen_info = cedict_dict[chosen]
        chosen_pinyin = chosen_info["pinyin"]
        chosen_meaning = chosen_info["meaning"]

        comment = st.text_area("Add a comment (optional):")

        if chosen in user_words:
            st.success("Already in your word list.")
        else:
            if st.button("➕ Add selected Chinese word"):
                entry = {
                    "lang": "chinese",
                    "word": chosen,
                    "pron": chosen_pinyin,
                    "meaning": chosen_meaning,
                    "audio": "",
                    "comment": comment,
                    "srs_interval": 1,
                    "srs_due": datetime.date.today().isoformat(),
                    "srs_ease": 2.5,
                    "srs_reps": 0
                }
                db_add_word(entry)
                st.success(f"Added '{chosen}' to Chinese words.")
                st.rerun()
