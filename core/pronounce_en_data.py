"""Данные английского словаря произношения (для английских голосов). Логика: core/pronounce_en.py.

Формат: «как написано» -> «как произнести». Подмена идёт только в текст для голоса, субтитры не меняются.
"""

# Фразы и сокращения с точками (регистр не важен), применяются первыми
PHRASES = [
    (r"\be\.g\.", "for example"), (r"\bi\.e\.", "that is"), (r"\betc\.?(?!\w)", "et cetera"), (r"\bvs\.?(?!\w)", "versus"),
    (r"\bcf\.", "compare"), (r"\bviz\.", "namely"), (r"\bapprox\.", "approximately"), (r"\bca\.(?=\s*\d)", "circa"),
    (r"\bc\.(?=\s*\d{3,4}\b)", "circa"), (r"\bMr\.", "Mister"), (r"\bMrs\.", "Missus"), (r"\bMs\.", "Miz"), (r"\bDr\.", "Doctor"),
    (r"\bProf\.", "Professor"), (r"\bSr\.", "Senior"), (r"\bJr\.", "Junior"), (r"\bGen\.", "General"), (r"\bSgt\.", "Sergeant"),
    (r"\bCapt\.", "Captain"), (r"\bLt\.", "Lieutenant"), (r"\bCol\.", "Colonel"), (r"\bGov\.", "Governor"), (r"\bSen\.", "Senator"),
    (r"\bRep\.", "Representative"), (r"\bRev\.", "Reverend"), (r"\bInc\.", "Incorporated"), (r"\bCorp\.", "Corporation"),
    (r"\bLtd\.", "Limited"), (r"\bCo\.(?=\s+[a-z])", "Company"), (r"\bdept\.", "department"), (r"\best\.(?=\s*\d)", "established"),
    (r"\bw/o\b", "without"), (r"\bw/(?=\s)", "with"), (r"\bb/c\b", "because"), (r"\bb/w\b", "between"), (r"\bw/e\b", "whatever"),
    (r"\bU\.S\.A\.?", "U S A"), (r"\bU\.S\.(?!\w)", "U S"), (r"\bU\.K\.?", "U K"), (r"\bU\.N\.?", "U N"), (r"\bE\.U\.?", "E U"),
    (r"\bD\.C\.?", "D C"), (r"\bPh\.?D\.?", "P H D"), (r"\bB\.?A\.(?!\w)", "B A"), (r"\bM\.?A\.(?!\w)", "M A"), (r"\bM\.?D\.(?!\w)", "M D"),
    (r"\ba\.m\.", "A M"), (r"\bp\.m\.", "P M"), (r"\bN\.B\.", "note well"), (r"\bP\.S\.", "P S"), (r"\bR\.I\.P\.?", "rest in peace"),
    (r"\bNo\.\s?(?=\d)", "number "), (r"\bNos\.\s?(?=\d)", "numbers "), (r"\bvol\.\s?(?=\d)", "volume "), (r"\bch\.\s?(?=\d)", "chapter "),
    (r"\bpp?\.\s?(?=\d)", "page "), (r"\bfig\.\s?(?=\d)", "figure "), (r"\bamt\.", "amount"), (r"\bavg\.", "average"),
    (r"(?<!\w)#\s?(?=\d)", "number "), (r"\s&\s", " and "), (r"\s@\s", " at "), (r"\s=\s", " equals "), (r"\s\+\s", " plus "),
    (r"\bTL;DR\b", "too long didn't read"), (r"\bTBD\b", "to be determined"), (r"\bTBA\b", "to be announced"),
    (r"\bFAQ\b", "F A Q"), (r"\bDIY\b", "D I Y"), (r"\bASAP\b", "A S A P"), (r"\bFYI\b", "F Y I"), (r"\bIMO\b", "in my opinion"),
    (r"\bIMHO\b", "in my humble opinion"), (r"\bBTW\b", "by the way"), (r"\bIIRC\b", "if I recall correctly"), (r"\bAFAIK\b", "as far as I know"),
    (r"\bTBH\b", "to be honest"), (r"\bOMG\b", "oh my god"), (r"\bWTF\b", "what the f"), (r"\bLOL\b", "L O L"), (r"\bAFK\b", "A F K"),
    (r"\bGG\b", "G G"), (r"\bIRL\b", "in real life"), (r"\bDM\b", "D M"), (r"\bPOV\b", "P O V"), (r"\bNSFW\b", "N S F W"),
    (r"\bGOAT\b", "goat"), (r"\bFOMO\b", "fomo"), (r"\bYOLO\b", "yolo"),
]

# Аббревиатуры, которые читаются по буквам (регистр важен: ВЕРХНИЙ)
SPELLED = set("""
FBI CIA NSA NASA_ USA UK EU UN UAE USSR UFO UFC NBA NFL NHL MLB NCAA WWE WWF BBC CNN HBO MTV ESPN NYC LA SF DC
CEO CTO CFO COO CMO CIO HR PR PM QA UI UX AI ML AR VR XR API SDK CLI GUI IDE OS PC TV DVD CD USB HDMI VPN GPS LED OLED LCD
CPU GPU RAM ROM SSD HDD NVMe FPS HD FHD UHD HDR PDF URL HTML CSS SQL PHP JS TS IP DNS SSL TLS SSH HTTP HTTPS FTP SMS MMS QR NFC
LTE GSM ISP ISO IT ID DNA RNA MRI CT ICU ER ADHD PTSD OCD BMI ATM PIN VIP DJ MC MVP KPI ROI SEO SMM CRM ERP SaaS_ B2B B2C CTR CPM
CPC GDP GMO WHO IMF WTO NATO_ OPEC_ NGO NPC RPG MMO MOBA PVP PVE GTA PUBG BMW AMG SUV EV LGBT BTS EDM
""".split())
SPELLED = {w for w in SPELLED if not w.endswith("_")}

# Аббревиатуры, которые читаются как слово - оставляем как есть
WORDLIKE = {"NASA", "NATO", "OPEC", "UNESCO", "UNICEF", "FIFA", "UEFA", "SCUBA", "LASER", "RADAR", "SONAR", "AIDS", "COVID", "SARS", "ASCII",
            "JPEG", "GIF", "SAAS", "WYSIWYG", "FOMO", "YOLO", "GOAT", "LOL", "NERF", "OREO", "IKEA", "LEGO", "SEGA", "ASUS", "ACER"}

# Слова, бренды, имена: «как написано (регистр не важен)» -> как произнести
WORDS = {
    # техника, софт, интернет
    "nginx": "engine X", "kubectl": "cube control", "kubernetes": "koo-ber-NET-eez", "k8s": "kubernetes", "ubuntu": "oo-BOON-too",
    "linux": "LIN-ucks", "linus": "LIE-nus", "sudo": "sue-doo", "chmod": "C H mod", "sql": "S Q L", "mysql": "my S Q L",
    "postgresql": "post-gres Q L", "postgres": "post-gres", "mongodb": "mongo D B", "sqlite": "S Q lite", "redis": "REH-diss",
    "oauth": "oh-auth", "latex": "LAY-tek", "qt": "cute", "gif": "gif", "gifs": "gifs", "jpeg": "jay-peg", "jpg": "jay-peg", "png": "P N G",
    "mp3": "M P three", "mp4": "M P four", "mkv": "M K V", "wav": "wave file", "ogg": "ogg", "webm": "web M", "webp": "web P",
    "github": "GitHub", "gitlab": "git lab", "stackoverflow": "stack overflow", "wikipedia": "wiki-pee-dee-uh", "reddit": "reddit",
    "tiktok": "tick tock", "youtube": "YouTube", "ytshorts": "YouTube Shorts", "whatsapp": "what's app", "wechat": "we chat",
    "vkontakte": "V Kontakte", "vk": "V K", "telegram": "telegram", "tg": "telegram", "yandex": "YAHN-dex", "mailru": "mail dot R U",
    "iphone": "eye-phone", "ipad": "eye-pad", "ipod": "eye-pod", "imac": "eye-mac", "macbook": "mac book", "airpods": "air pods",
    "ios": "eye O S", "macos": "mac O S", "ipados": "eye-pad O S", "watchos": "watch O S", "icloud": "eye-cloud", "itunes": "eye-tunes",
    "imessage": "eye-message", "facetime": "face time", "airdrop": "air drop", "airtag": "air tag", "homekit": "home kit",
    "xbox": "X box", "playstation": "play station", "ps5": "P S five", "ps4": "P S four", "ps3": "P S three", "ps2": "P S two",
    "nvidia": "en-VID-ee-uh", "geforce": "jee-force", "radeon": "RAY-dee-on", "ryzen": "RY-zen", "intel": "intel", "amd": "A M D",
    "xiaomi": "shau-mee", "huawei": "wah-way", "oppo": "oh-poh", "oneplus": "one plus", "vivo": "vee-voh", "realme": "real me",
    "lenovo": "leh-NO-vo", "asus": "AY-sus", "acer": "AY-ser", "samsung": "sam-sung", "sony": "so-nee", "lg": "L G", "hp": "H P",
    "dell": "dell", "msi": "M S I", "gopro": "go pro", "dji": "D J I", "canon": "canon", "nikon": "NIGH-kon", "fujifilm": "foo-jee-film",
    "tesla": "tesla", "spacex": "space X", "starlink": "star link", "neuralink": "neural link", "openai": "open A I", "chatgpt": "chat G P T",
    "gpt": "G P T", "gpt-3": "G P T three", "gpt-4": "G P T four", "gpt-4o": "G P T four oh", "gpt-5": "G P T five", "llm": "L L M",
    "llms": "L L Ms", "sora": "SOR-uh", "midjourney": "mid journey", "dalle": "dolly", "dall-e": "dolly", "stablediffusion": "stable diffusion",
    "claude": "clawed", "gemini": "JEM-in-eye", "deepseek": "deep seek", "llama": "LAH-muh", "mistral": "MIS-tral", "grok": "grok",
    "wifi": "wi-fi", "bluetooth": "blue tooth", "usb-c": "U S B C", "lightning": "lightning", "thunderbolt": "thunder bolt",
    "4k": "four K", "8k": "eight K", "2k": "two K", "1080p": "ten eighty P", "720p": "seven twenty P", "1440p": "fourteen forty P",
    "2160p": "twenty-one sixty P", "60fps": "sixty F P S", "30fps": "thirty F P S", "120hz": "one twenty hertz", "144hz": "one forty-four hertz",
    "3d": "three D", "2d": "two D", "5g": "five G", "4g": "four G", "3g": "three G", "6g": "six G", "h264": "H two sixty-four",
    "h265": "H two sixty-five", "x264": "X two sixty-four", "x265": "X two sixty-five", "av1": "A V one", "vp9": "V P nine",
    "covid-19": "covid nineteen", "covid19": "covid nineteen", "mrna": "M R N A", "crispr": "crisper", "h2o": "H two O", "co2": "C O two",
    "o2": "O two", "ch4": "C H four", "nh3": "N H three", "nacl": "N A C L", "ph": "P H",
    "bitcoin": "bitcoin", "btc": "bitcoin", "eth": "ethereum", "xrp": "X R P", "doge": "doge", "dogecoin": "doge coin", "usdt": "tether",
    "usdc": "U S D C", "nft": "N F T", "nfts": "N F Ts", "defi": "dee-fi", "dao": "dow", "web3": "web three", "web2": "web two",
    "bnb": "B N B", "bsc": "B S C", "gmgn": "G M G N", "memecoin": "meme coin", "pancakeswap": "pancake swap", "uniswap": "uni swap",
    # бренды, компании
    "nike": "NIGH-kee", "adidas": "AH-dee-dahss", "puma": "POO-ma", "reebok": "REE-bok", "asics": "AY-sics", "lululemon": "loo-loo-LEM-on",
    "porsche": "POR-shuh", "hyundai": "HUN-day", "kia": "KEE-uh", "mazda": "MAHZ-duh", "subaru": "soo-BAR-oo", "peugeot": "puh-ZHOH",
    "renault": "ruh-NOH", "citroen": "SEE-tro-en", "audi": "OW-dee", "volkswagen": "FOLKS-vah-gen", "skoda": "SKOH-duh",
    "lamborghini": "lam-bor-GEE-nee", "ferrari": "feh-RAH-ree", "maserati": "mah-zeh-RAH-tee", "bugatti": "boo-GAH-tee", "bentley": "bent-lee",
    "mercedes": "mer-SAY-deez", "lexus": "LEK-sus", "toyota": "toy-YOH-tuh", "nissan": "NEE-sahn", "honda": "HON-duh", "infiniti": "in-FIN-ih-tee",
    "ikea": "eye-KEE-uh", "lego": "LEG-oh", "nutella": "noo-TEL-uh", "oreo": "OR-ee-oh", "doritos": "doh-REE-tohs", "pringles": "pring-gulls",
    "heineken": "HIGH-nuh-ken", "budweiser": "BUD-wy-zer", "corona": "koh-ROH-nuh", "pepsi": "pep-see", "fanta": "fan-tuh",
    "mcdonalds": "mick-DON-alds", "mcdonald's": "mick-DON-alds", "kfc": "K F C", "starbucks": "star-bucks", "subway": "sub way",
    "burgerking": "burger king", "dominos": "DOM-ih-nohs", "chipotle": "chi-POHT-lay", "nestle": "NESS-ul", "nespresso": "ness-PRESS-oh",
    "louis vuitton": "LOO-ee vwee-TOHN", "vuitton": "vwee-TOHN", "gucci": "GOO-chee", "prada": "PRAH-duh", "versace": "ver-SAH-chee",
    "chanel": "shuh-NEL", "hermes": "air-MEZ", "givenchy": "zhee-VON-shee", "balenciaga": "buh-len-see-AH-guh", "dior": "dee-OR",
    "burberry": "BUR-buh-ree", "rolex": "ROH-lex", "cartier": "kar-TYAY", "tiffany": "TIF-uh-nee", "zara": "ZAH-ruh", "uniqlo": "yoo-NEEK-loh",
    "h&m": "H and M", "hm": "H M", "asos": "AY-sos", "shein": "SHE-in", "aliexpress": "ali express", "alibaba": "AL-ih-bah-bah",
    "taobao": "tau-bau", "tencent": "ten-cent", "bytedance": "byte dance", "baidu": "by-doo", "wildberries": "wild berries", "ozon": "oh-zon",
    "uber": "oo-ber", "airbnb": "air B N B", "paypal": "pay pal", "venmo": "ven-mo", "stripe": "stripe", "shopify": "shop-ih-fy",
    "netflix": "net flix", "hulu": "hoo-loo", "disney": "diz-nee", "spotify": "spot-ih-fy", "soundcloud": "sound cloud", "twitch": "twitch",
    "discord": "dis-cord", "snapchat": "snap chat", "instagram": "insta-gram", "insta": "insta", "facebook": "face book", "meta": "meta",
    "linkedin": "linked in", "pinterest": "pin-ter-est", "tumblr": "tum-bler", "twitter": "twitter", "x.com": "X dot com",
    "google": "google", "gmail": "G mail", "android": "an-droid", "pixel": "pixel", "chromebook": "chrome book", "chromecast": "chrome cast",
    "amazon": "am-uh-zon", "alexa": "uh-LEX-uh", "kindle": "kin-dull", "microsoft": "micro-soft", "windows": "win-dohz", "azure": "AZH-er",
    "adobe": "uh-DOH-bee", "photoshop": "photo shop", "illustrator": "ill-us-tray-ter", "figma": "fig-muh", "canva": "can-vuh",
    "capcut": "cap cut", "tiktokshop": "tick tock shop", "mrbeast": "mister beast", "pewdiepie": "pew-dee-pie", "memesofrakhim": "memes of Rakhim",
    "rakhim": "rah-HEEM", "memes of rakhim": "memes of rah-HEEM",
    # имена и места, которые часто читают неверно
    "nietzsche": "NEE-chuh", "goethe": "GUR-tuh", "tchaikovsky": "chai-KOFF-skee", "dostoevsky": "dost-oh-YEFF-skee", "tolstoy": "TOLE-stoy",
    "chekhov": "CHEK-off", "pushkin": "POOSH-kin", "gagarin": "guh-GAR-in", "kremlin": "KREM-lin", "moscow": "MOS-koh", "putin": "POO-tin",
    "xi jinping": "shee jin-ping", "beijing": "bay-jing", "shanghai": "shang-HIGH", "guangzhou": "gwahng-joh", "shenzhen": "shen-jen",
    "kyiv": "keev", "kharkiv": "KHAR-keev", "odesa": "oh-DEH-suh", "dnipro": "nip-roh", "lviv": "luh-VEEV", "minsk": "minsk",
    "tbilisi": "tuh-BIL-ih-see", "yerevan": "yeh-reh-VAHN", "baku": "bah-KOO", "almaty": "al-MAH-tee", "tashkent": "tash-KENT",
    "reykjavik": "RAY-kyuh-veek", "worcestershire": "WOOS-ter-sheer", "leicester": "LES-ter", "edinburgh": "ED-in-bruh", "gloucester": "GLOS-ter",
    "arkansas": "AR-kan-saw", "illinois": "ill-ih-NOY", "colorado": "col-uh-RAD-oh", "nevada": "nuh-VAD-uh", "qatar": "KUH-tar", "doha": "DOH-hah",
    "versailles": "ver-SIGH", "worcester": "WOOS-ter", "quinoa": "KEEN-wah", "gyro": "YEE-roh", "pho": "fuh", "acai": "ah-sigh-EE",
    "meme": "meem", "memes": "meemz", "sigma": "sig-muh", "rizz": "riz", "skibidi": "skih-bih-dee", "yeet": "yeet", "gigachad": "giga chad",
    "tiktoker": "tick-tocker", "vlog": "vlog", "vlogger": "vlogger", "asmr": "A S M R", "ok": "okay", "okay": "okay", "pls": "please", "plz": "please",
    "thx": "thanks", "ty": "thank you", "np": "no problem", "idk": "I don't know", "imo": "in my opinion", "ngl": "not gonna lie", "fr": "for real",
    "rn": "right now", "ikr": "I know right", "smh": "shaking my head", "tbf": "to be fair", "ffs": "for f's sake", "lmao": "L M A O", "rofl": "rofl",
    "bruh": "bro", "sus": "suss", "cringe": "crinj", "lit": "lit", "salty": "salty", "flex": "flex", "vibe": "vibe", "vibes": "vibes",
}

# Единицы после числа (регистр важен): ключ -> (единственное, множественное)
UNITS = {
    "km": ("kilometer", "kilometers"), "cm": ("centimeter", "centimeters"), "mm": ("millimeter", "millimeters"), "kg": ("kilogram", "kilograms"),
    "g": ("gram", "grams"), "mg": ("milligram", "milligrams"), "lb": ("pound", "pounds"), "lbs": ("pound", "pounds"), "oz": ("ounce", "ounces"),
    "ft": ("foot", "feet"), "mi": ("mile", "miles"), "mph": ("mile per hour", "miles per hour"), "km/h": ("kilometer per hour", "kilometers per hour"),
    "kph": ("kilometer per hour", "kilometers per hour"), "m/s": ("meter per second", "meters per second"), "ml": ("milliliter", "milliliters"),
    "L": ("liter", "liters"), "GB": ("gigabyte", "gigabytes"), "MB": ("megabyte", "megabytes"), "KB": ("kilobyte", "kilobytes"),
    "TB": ("terabyte", "terabytes"), "Gb": ("gigabit", "gigabits"), "Mb": ("megabit", "megabits"), "kHz": ("kilohertz", "kilohertz"),
    "MHz": ("megahertz", "megahertz"), "GHz": ("gigahertz", "gigahertz"), "Hz": ("hertz", "hertz"), "W": ("watt", "watts"), "kW": ("kilowatt", "kilowatts"),
    "MW": ("megawatt", "megawatts"), "V": ("volt", "volts"), "mAh": ("milliamp hour", "milliamp hours"), "ms": ("millisecond", "milliseconds"),
    "sec": ("second", "seconds"), "min": ("minute", "minutes"), "hr": ("hour", "hours"), "hrs": ("hour", "hours"), "h": ("hour", "hours"),
    "fps": ("frame per second", "frames per second"), "dB": ("decibel", "decibels"), "kcal": ("calorie", "calories"), "cal": ("calorie", "calories"),
    "bpm": ("beat per minute", "beats per minute"), "rpm": ("R P M", "R P M"), "px": ("pixel", "pixels"), "wpm": ("word per minute", "words per minute"),
    "x": None,
}
UNITS.pop("x")

# Эти слова нейроголоса и так читают верно, подмена только ухудшает - убираем из встроенного словаря
# (если нужно своё чтение: /say en: слово = как читать - пользовательское правило главнее)
_NATURAL = """iphone ipad ipod imac macbook airpods youtube tiktok instagram facebook netflix spotify google amazon microsoft windows android
samsung sony nike adidas puma mercedes toyota honda audi tesla pepsi starbucks subway uber paypal discord twitch twitter reddit telegram
whatsapp github disney lego ikea nutella oreo intel dell canon xbox playstation meta pixel claude gemini llama grok stripe kindle alexa
nissan kia mazda lexus corona fanta snapchat insta tiktoker vlog vlogger lit flex vibe vibes salty meme memes sigma bruh cringe""".split()
for _k in _NATURAL:
    WORDS.pop(_k, None)
for _k, _v in list(WORDS.items()):
    if "-" in _v and " " not in _v:
        WORDS[_k] = _v.lower()
PHRASES += [(r"\bQ&A\b", "Q and A"), (r"\bR&D\b", "R and D"), (r"\bM&A\b", "M and A"), (r"\bA/B\b", "A B"), (r"\bB&W\b", "black and white")]
