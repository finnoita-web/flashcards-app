import streamlit as st
import json
from pathlib import Path
import requests
import re
from pypinyin import pinyin, Style
import datetime


DATA_FILE = Path("flashcards.json")
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

def load_data():
    if DATA_FILE.exists():
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if "english" not in data:
            data["english"] = []
        if "chinese" not in data:
            data["chinese"] = []
        if "groups" not in data:
            data["groups"] = {}
        if "study_history" not in data:
            data["study_history"] = []   # list of dates: ["2025-01-21", ...]

        return data
    return {"english": [], "chinese": [], "groups": {}}

def save_data(data):
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
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
    
def download_audio(word: str, audio_url: str):
    """Download audio file and return local path, or empty string if failed."""
    if not audio_url:
        return ""

    filename = AUDIO_DIR / f"{word.lower()}.mp3"

    try:
        r = requests.get(audio_url, timeout=10)
        if r.status_code == 200:
            with open(filename, "wb") as f:
                f.write(r.content)
            return str(filename)
    except Exception:
        return ""

    return ""


def compute_streak(history):
    if not history:
        return 0, 0

    import datetime

    dates = sorted(datetime.date.fromisoformat(d) for d in history)
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    # Current streak
    streak = 0
    day = today
    while day.isoformat() in history:
        streak += 1
        day -= datetime.timedelta(days=1)

    # If today wasn't studied but yesterday was
    if today.isoformat() not in history and yesterday.isoformat() in history:
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
    
def srs_update(entry, quality):
    import datetime

    ease = entry.get("srs_ease", 2.5)
    interval = entry.get("srs_interval", 1)
    reps = entry.get("srs_reps", 0)

    if quality == 0:  # Again
        reps = 0
        interval = 1
        ease = max(1.3, ease - 0.2)

    elif quality == 1:  # Hard
        interval = max(1, int(interval * 1.2))
        ease = max(1.3, ease - 0.15)
        reps += 1

    elif quality == 2:  # Good
        interval = int(interval * ease)
        reps += 1

    elif quality == 3:  # Easy
        interval = int(interval * ease * 1.3)
        ease += 0.1
        reps += 1

    next_due = (datetime.date.today() + datetime.timedelta(days=interval)).isoformat()

    entry["srs_interval"] = interval
    entry["srs_ease"] = ease
    entry["srs_reps"] = reps
    entry["srs_due"] = next_due

def lookup_chinese_by_pinyin(py):
    py = py.lower().replace(" ", "")
    matches = []
    for hanzi, info in cedict_dict.items():
        p = info.get("pinyin", "").lower().replace(" ", "")
        if p == py:
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

# Sidebar navigation
page = st.sidebar.radio(
    "Navigation",
    ["Add Words", "Flashcards", "Study Mode", "Study Groups", "Backup & Restore", "Statistics", "Review Mode (SRS)"]
)

# Load data
data = load_data()

# ----------------- PAGE: Add Words ----------------- #
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

            for w in words:
                w = w.lower()
                # Skip duplicates
                if any(e["word"] == w for e in data[lang_key]):
                    continue

                # ----------------- English ----------------- #
                if lang_key == "english":
                    if is_chinese(w):
                        errors.append(f"Chinese word in English mode: {w}")
                        continue

                    if not is_single_english_word(w):
                        errors.append(f"Not a single English word: {w}")
                        continue

                    info = fetch_freedict_data(w)

                    if not info:
                    # Fallback: add minimal entry
                        entry = {
                            "word": w,
                            "pron": "",
                            "meaning": "",
                            "audio": "",
                            "comment": "", 
                            "srs_interval": 1, 
                            "srs_due": datetime.date.today().isoformat(),
                            "srs_ease": 2.5, 
                            "srs_reps": 0
                        }
                        data["english"].append(entry)
                        added += 1
                        errors.append(f"Added fallback entry for: {w}")
                        continue

                    audio_path = download_audio(w, info["audio_url"])

                    entry = {
                        "word": w,
                        "pron": info["ipa"],
                        "meaning": info["meaning"],
                        "audio": audio_path,
                        "comment": "", 
                        "srs_interval": 1, 
                        "srs_due": datetime.date.today().isoformat(),
                        "srs_ease": 2.5, 
                        "srs_reps": 0
                    }
                    data["english"].append(entry)
                    added += 1


                # ----------------- Chinese ----------------- #
                else:
                    # Try pinyin lookup
                        matches = lookup_chinese_by_pinyin(w)
                        if not matches:
                            errors.append(f"Not Chinese or valid pinyin: {w}")
                            continue
                        if len(matches) > 1:
                            # Let user choose
                            chosen = st.selectbox(f"Multiple matches for '{w}'", matches)
                            w = chosen
                        else:
                            w = matches[0]

                    ced = cedict_dict.get(w, {})
                    entry = {
                        "word": w,
                        "pron": ced.get("pinyin", get_pinyin(w)),
                        "meaning": ced.get("meaning", ""),
                        "audio": "",
                        "comment": "",
                        "srs_interval": 1, 
                        "srs_due": datetime.date.today().isoformat(),
                        "srs_ease": 2.5, 
                        "srs_reps": 0
                    }
                    data["chinese"].append(entry)
                    added += 1

            save_data(data)

            st.success(f"Added {added} new word(s).")

            if errors:
                st.error("Some words could not be added:")
                for e in errors:
                    st.write("- " + e)

        with st.expander("📥 Import Words From File", expanded=False):
            uploaded = st.file_uploader("Upload CSV or TXT file", type=["csv", "txt"])

            import_lang = st.radio("Import as:", ["English", "Chinese"], key="import_lang")
            lang_key = "english" if import_lang == "English" else "chinese"

            if uploaded:
                content = uploaded.read().decode("utf-8").strip().splitlines()
                st.write(f"Detected {len(content)} lines.")

                if st.button("Import Words"):
                    added = 0
                    for line in content:
                        word = line.strip()
                        word = word.lower()
                        if not word:
                            continue

                        # Skip duplicates
                        if any(e["word"] == word for e in data[lang_key]):
                            continue

                        # English import
                        if lang_key == "english":
                            info = fetch_freedict_data(word)

                            if not info:
                                # Fallback: add minimal entry
                                entry = {
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
                                data["english"].append(entry)
                                added += 1
                                st.warning(f"Added fallback entry for: {word}")
                                continue

                            # Normal case (FreeDict found the word)
                            audio_path = download_audio(word, info["audio_url"])
                            entry = {
                                "word": word,
                                "pron": info["ipa"],
                                "meaning": info["meaning"],
                                "audio": audio_path,
                                "comment": "",
                                "srs_interval": 1,
                                "srs_due": datetime.date.today().isoformat(),
                                "srs_ease": 2.5,
                                "srs_reps": 0
                            }
                            data["english"].append(entry)
                            added += 1


                        # Chinese import
                        else:
                            ced = cedict_dict.get(word, {})
                            entry = {
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
                            data["chinese"].append(entry)
                            added += 1

                    save_data(data)
                    st.success(f"Imported {added} words.")


# ----------------- PAGE: Flashcards ----------------- #
elif page == "Flashcards":
    st.header("📄 Flashcards")
    
    language = st.radio("Select language", ["English", "Chinese"])
    lang_key = "english" if language == "English" else "chinese"
    entries = data[lang_key]

    if not entries:
        st.info("No words yet.")
        st.stop()

    with st.expander("Flashcards Table", expanded=True):

        search_query = st.text_input("Search", placeholder="Type to filter words...")
        filter_audio = st.checkbox("Only words with audio")
        filter_comment = st.checkbox("Only words with comments")

        sort_by = st.selectbox(
            "Sort by",
            ["Word (A→Z)", "Word (Z→A)", "Meaning length", "Has audio", "Has comment"]
        )

        filtered = entries

        if search_query:
            q = search_query.lower()
            filtered = [
                e for e in filtered
                if q in e["word"].lower()
                or q in e.get("meaning", "").lower()
                or q in e.get("comment", "").lower()
            ]

        if filter_audio:
            filtered = [e for e in filtered if e.get("audio")]

        if filter_comment:
            filtered = [e for e in filtered if e.get("comment")]

        if sort_by == "Word (A→Z)":
            filtered = sorted(filtered, key=lambda e: e["word"].lower())
        elif sort_by == "Word (Z→A)":
            filtered = sorted(filtered, key=lambda e: e["word"].lower(), reverse=True)
        elif sort_by == "Meaning length":
            filtered = sorted(filtered, key=lambda e: len(e.get("meaning", "")))
        elif sort_by == "Has audio":
            filtered = sorted(filtered, key=lambda e: bool(e.get("audio")), reverse=True)
        elif sort_by == "Has comment":
            filtered = sorted(filtered, key=lambda e: bool(e.get("comment")), reverse=True)

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
            
    with st.expander("Edit or Delete Word", expanded=False):

        selected_word = st.selectbox(
            "Choose a word",
            [e["word"] for e in entries]
        )

        entry = next(e for e in entries if e["word"] == selected_word)
        # Auto-open edit mode for words missing meaning
        if not entry.get("meaning"):
            st.session_state["edit_mode"] = True


        col1, col2 = st.columns(2)

        with col2:
            if st.button("🗑️ Delete Word"):
                data[lang_key] = [e for e in entries if e["word"] != selected_word]

                wid = f"{lang_key}:{selected_word}"
                for gname, glist in data["groups"].items():
                    data["groups"][gname] = [x for x in glist if x != wid]

                save_data(data)
                st.success(f"Deleted '{selected_word}'. Refresh the page.")
                st.stop()

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
                        old_word = entry["word"]

                        entry["word"] = new_word
                        entry["pron"] = new_pron
                        entry["meaning"] = new_meaning
                        entry["comment"] = new_comment

                        if new_word != old_word:
                            old_id = f"{lang_key}:{old_word}"
                            new_id = f"{lang_key}:{new_word}"

                            for gname, glist in data["groups"].items():
                                for i, wid in enumerate(glist):
                                    if wid == old_id:
                                        glist[i] = new_id

                        save_data(data)

                        st.success("Changes saved.")
                        st.session_state["edit_mode"] = False
                        st.stop()

    
    if st.button("Download/Redownload Audio"):
        if lang_key == "english":
            info = fetch_freedict_data(entry["word"])
            if info and info["audio_url"]:
                audio_path = download_audio(entry["word"], info["audio_url"])
                entry["audio"] = audio_path
                save_data(data)
                st.success("Audio downloaded.")
            else:
                st.error("No audio available for this word.")
        else:
            st.info("Audio download is only available for English words.")


# ----------------- PAGE: Study Mode ----------------- #

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
    if "study_prompt_type" not in ss:
        ss.study_prompt_type = "word"
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
                if not data["groups"]:
                    st.warning("No groups available.")
                    st.stop()
                selected_group = st.selectbox("Select a group", list(data["groups"].keys()))

            if st.button("Start Study"):
                import datetime
                today = datetime.date.today().isoformat()
                if today not in data["study_history"]:
                    data["study_history"].append(today)
                    save_data(data)

                # Build study list
                if study_source == "All English Words":
                    study_list = data["english"]
                elif study_source == "All Chinese Words":
                    study_list = data["chinese"]
                else:
                    study_list = []
                    for wid in data["groups"][selected_group]:
                        lang, word = wid.split(":", 1)
                        entry = next((e for e in data[lang] if e["word"] == word), None)
                        if entry:
                            study_list.append(entry)

                if not study_list:
                    st.warning("No words available.")
                    st.stop()

                if shuffle:
                    import random
                    random.shuffle(study_list)

                ss.study_list = study_list
                ss.study_index = 0
                ss.revealed = False
                ss.study_prompt_type = choose_prompt_type(study_list[0])
                st.rerun()

        st.info("Start a study session above.")
        st.stop()

    # ---------- Current Card ----------
    card = ss.study_list[ss.study_index]

    # ---------- Display Prompt ----------
    if ss.study_prompt_type == "word":
        st.markdown(f"## **{card['word']}**")
    elif ss.study_prompt_type == "meaning":
        st.markdown("### Meaning")
        st.write(card.get("meaning", ""))
    elif ss.study_prompt_type == "comment":
        st.markdown("### Comment")
        st.info(card.get("comment", ""))

    # ---------- Reveal ----------
    if ss.revealed:
        if ss.study_prompt_type != "word":
            st.markdown(f"### Word\n**{card['word']}**")

        st.markdown(f"**Pronunciation:** {card.get('pron', '')}")

        if ss.study_prompt_type != "meaning":
            st.markdown("### Meaning")
            st.write(card.get("meaning", ""))

        if card.get("comment") and ss.study_prompt_type != "comment":
            st.markdown("### Comment")
            st.info(card["comment"])

    # ---------- Button Row ----------
    st.markdown("""
    <style>
    .button-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        margin-top: 1rem;
        margin-bottom: 1rem;
    }
    .button-row .stButton > button {
        flex: 1;
        min-width: 100px;
        font-size: 1.1rem;
        padding: 0.6rem 1rem;
    }
    @media (max-width: 600px) {
        .button-row {
            flex-direction: row;
            justify-content: space-between;
        }
        .button-row .stButton > button {
            flex: 1;
            font-size: 1.2rem;
            padding: 0.9rem 1.2rem;
        }
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="button-row">', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button("⬅️ Previous"):
            if ss.study_index > 0:
                ss.study_index -= 1
                ss.revealed = False
                ss.study_prompt_type = choose_prompt_type(ss.study_list[ss.study_index])
                st.rerun()

    with col2:
        if st.button("Reveal"):
            ss.revealed = True
            st.rerun()

    with col3:
        if st.button("➡️ Next"):
            if ss.study_index < len(ss.study_list) - 1:
                ss.study_index += 1
                ss.revealed = False
                ss.study_prompt_type = choose_prompt_type(ss.study_list[ss.study_index])
                st.rerun()

    with col4:
        if st.button("❌ End"):
            ss.study_list = []
            ss.study_index = 0
            ss.revealed = False
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

    # ---------- Audio ----------
    audio_path = card.get("audio", "")
    if audio_path and Path(audio_path).exists():
        with open(audio_path, "rb") as f:
            st.audio(f.read())
    else:
        st.write("No audio")



# ----------------- PAGE: Study Groups ----------------- #
elif page == "Study Groups":
    st.header("📂 Study Groups")

    groups = data["groups"]

    with st.expander("Create New Group", expanded=False):
        new_group_name = st.text_input("Group name")
        if st.button("Create Group"):
            if not new_group_name.strip():
                st.warning("Group name cannot be empty.")
            elif new_group_name in groups:
                st.warning("A group with this name already exists.")
            else:
                groups[new_group_name] = []
                save_data(data)
                st.success(f"Group '{new_group_name}' created.")

    if not groups:
        st.info("No groups yet. Create one above.")
        st.stop()

    with st.expander("Select Group", expanded=True):
        selected_group = st.selectbox("Select a group", list(groups.keys()))
        group_list = groups[selected_group]

    with st.expander("Add Words to Group", expanded=False):
        language = st.radio("Select language", ["English", "Chinese"], key="group_lang")
        lang_key = "english" if language == "English" else "chinese"

        available_words = [e["word"] for e in data[lang_key]]

        if available_words:
            selected_word = st.selectbox("Choose a word to add", available_words)
            if st.button("Add to Group"):
                wid = f"{lang_key}:{selected_word}"
                if wid not in group_list:
                    group_list.append(wid)
                    save_data(data)
                    st.success(f"Added '{selected_word}' to '{selected_group}'.")
                else:
                    st.info(f"'{selected_word}' is already in this group.")
        else:
            st.info(f"No {language} words available.")

    with st.expander("Words in This Group", expanded=True):
        if not group_list:
            st.info("This group is empty.")
        else:
            display_rows = []
            for wid in group_list:
                lang, word = wid.split(":", 1)
                entry = next((e for e in data[lang] if e["word"] == word), None)
                if entry:
                    display_rows.append({
                        "Language": lang,
                        "Word": entry["word"],
                        "Pron": entry.get("pron", ""),
                        "Meaning": entry.get("meaning", ""),
                        "Comment": entry.get("comment", "")
                    })
            st.dataframe(display_rows, use_container_width=True)

    with st.expander("Remove Word From Group", expanded=False):
        if group_list:
            remove_word = st.selectbox(
                "Select word to remove",
                group_list,
                format_func=lambda wid: wid.split(":", 1)[1]
            )
            if st.button("Remove From Group"):
                group_list.remove(remove_word)
                save_data(data)
                st.success("Word removed from group.")

    with st.expander("Delete Group", expanded=False):
        if st.button("Delete This Group"):
            del groups[selected_group]
            save_data(data)
            st.success(f"Group '{selected_group}' deleted.")
            st.stop()

# ----------------- PAGE: Backup & Restore ----------------- #
elif page == "Backup & Restore":
    st.header("📦 Backup & Restore")

    st.markdown("Use this page to export your flashcards or restore them from a backup file.")

    # ----------------- BACKUP ----------------- #
    st.subheader("⬇️ Download Backup")

    import datetime
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")

    backup_json = json.dumps(data, ensure_ascii=False, indent=2)

    st.download_button(
        label="Download Backup File",
        data=backup_json,
        file_name=f"flashcards_backup_{timestamp}.json",
        mime="application/json"
    )

    st.markdown("---")

    # ----------------- RESTORE ----------------- #
    st.subheader("⬆️ Restore From Backup")

    uploaded = st.file_uploader("Upload a backup .json file", type="json")

    restore_mode = st.radio(
        "Restore mode:",
        ["Replace all data", "Merge with existing data"]
    )

    if uploaded and st.button("Restore Backup"):
        try:
            new_data = json.load(uploaded)

            # Validate structure
            if not isinstance(new_data, dict):
                st.error("Invalid file: root must be a JSON object.")
                st.stop()

            for key in ["english", "chinese", "groups"]:
                if key not in new_data:
                    st.error(f"Invalid file: missing '{key}' section.")
                    st.stop()

            # ----------------- REPLACE MODE ----------------- #
            if restore_mode == "Replace all data":
                data = new_data
                save_data(data)
                st.success("Backup restored successfully (replaced all data).")
                st.stop()

            # ----------------- MERGE MODE ----------------- #
            else:
                # Merge English
                existing_words = {e["word"]: e for e in data["english"]}
                for e in new_data["english"]:
                    existing_words[e["word"]] = e
                data["english"] = list(existing_words.values())

                # Merge Chinese
                existing_words = {e["word"]: e for e in data["chinese"]}
                for e in new_data["chinese"]:
                    existing_words[e["word"]] = e
                data["chinese"] = list(existing_words.values())

                # Merge Groups
                for gname, glist in new_data["groups"].items():
                    if gname not in data["groups"]:
                        data["groups"][gname] = glist
                    else:
                        for wid in glist:
                            if wid not in data["groups"][gname]:
                                data["groups"][gname].append(wid)

                save_data(data)
                st.success("Backup restored successfully (merged with existing data).")
                st.stop()

        except Exception as e:
            st.error(f"Failed to restore backup: {e}")

# ----------------- PAGE: Statistics ----------------- #

elif page == "Statistics":
    st.header("📊 Statistics & Progress")

    total_eng = len(data["english"])
    total_chi = len(data["chinese"])
    total_words = total_eng + total_chi

    with_audio = sum(1 for e in data["english"] if e.get("audio"))
    with_comment = sum(1 for e in data["english"] + data["chinese"] if e.get("comment"))

    st.subheader("📚 Word Stats")
    st.metric("Total Words", total_words)
    st.metric("English Words", total_eng)
    st.metric("Chinese Words", total_chi)
    st.metric("Words with Audio", with_audio)
    st.metric("Words with Comments", with_comment)

    st.subheader("📂 Group Stats")
    group_count = len(data["groups"])
    st.metric("Total Groups", group_count)

    if group_count:
        group_sizes = {g: len(wids) for g, wids in data["groups"].items()}
        largest_group = max(group_sizes.items(), key=lambda x: x[1])
        st.write(f"Most populated group: **{largest_group[0]}** ({largest_group[1]} words)")

    st.subheader("🧠 Study Insights")

    all_words = data["english"] + data["chinese"]
    if all_words:
        longest = max(all_words, key=lambda e: len(e.get("meaning", "")))
        st.write(f"Longest meaning: **{longest['word']}** — {len(longest['meaning'])} characters")

        recent = all_words[-1]
        st.write(f"Most recently added: **{recent['word']}**")

        avg_len = sum(len(e.get("meaning", "")) for e in all_words) / len(all_words)
        st.write(f"Average meaning length: **{avg_len:.1f}** characters")
    else:
        st.info("No words added yet.")

    import pandas as pd
    import datetime

    history = data.get("study_history", [])

    if history:
        df = pd.DataFrame({"date": history})
        df["date"] = pd.to_datetime(df["date"])
        df["count"] = 1

        st.subheader("📈 Study Activity Over Time")
        st.line_chart(df.set_index("date")["count"])
    else:
        st.info("No study activity recorded yet.")
        
        st.subheader("🔥 Streaks")

    current_streak, longest_streak = compute_streak(data.get("study_history", []))

    st.metric("Current Streak", f"{current_streak} days")
    st.metric("Longest Streak", f"{longest_streak} days")

# ----------------- SRS Mode ----------------- #

elif page == "Review Mode (SRS)":
    st.header("🧠 Review Mode (Spaced Repetition)")

    import datetime
    today = datetime.date.today().isoformat()

    due_words = []
    for lang in ["english", "chinese"]:
        for e in data[lang]:
            if e.get("srs_due", today) <= today:
                due_words.append(e)

    if not due_words:
        st.success("🎉 No words due for review today!")
        st.stop()

    card = due_words[0]

    st.markdown(f"## **{card['word']}**")

    if st.button("Reveal Meaning"):
        st.write(card.get("meaning", ""))
        st.write(f"Pronunciation: {card.get('pron', '')}")

    st.markdown("### Rate your recall")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button("Again"):
            srs_update(card, 0)
            save_data(data)
            st.rerun()

    with col2:
        if st.button("Hard"):
            srs_update(card, 1)
            save_data(data)
            st.rerun()

    with col3:
        if st.button("Good"):
            srs_update(card, 2)
            save_data(data)
            st.rerun()

    with col4:
        if st.button("Easy"):
            srs_update(card, 3)
            save_data(data)
            st.rerun()
