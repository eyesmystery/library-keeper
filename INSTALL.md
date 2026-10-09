# تثبيت «ثلاث الذكي» وتشغيله

*English summary for people and AI agents is at the end: [Install in English](#install-in-english).*

التطبيق سيرفر صغير (Python و Flask وقاعدة SQLite) يعمل على جهاز واحد في المكتبة. الزوار يفتحونه من جوالاتهم على نفس الواي فاي، والموظفون يفتحون لوحة الإدارة من أي متصفح. لا يحتاج Node ولا Docker ولا قاعدة بيانات خارجية.

## المتطلبات

- ويندوز 10 أو 11، أو macOS 12 أو أحدث.
- Python 3.9 أو أحدث.
- إنترنت في أول تشغيل (لتثبيت Flask في بيئة محلية داخل المجلد)، ثم حسب نموذج الذكاء الاصطناعي الذي تختاره.
- الجهاز والجوالات على نفس شبكة الواي فاي.

## ١. التحميل

افتح [صفحة الإصدارات](https://github.com/eyesmystery/library-keeper/releases/latest) ونزّل الملف المناسب: `…-windows.zip` أو `…-mac.zip` (نحو 490 ميغابايت). سجّل الدخول إلى GitHub أولًا، فالمستودع خاص.

من الطرفية، لمن عنده GitHub CLI:

```
gh release download --repo eyesmystery/library-keeper --pattern "*mac.zip"
gh release download --repo eyesmystery/library-keeper --pattern "*windows.zip"
```

فك الملف وانقل المجلد إلى مكان ثابت (مثل «المستندات»). لا تشغّله من داخل الملف المضغوط ولا من فلاش.

## ٢. ويندوز

1. ثبّت Python 3 من [python.org/downloads](https://www.python.org/downloads/) وفعّل **Add python.exe to PATH**.
2. انقر مرتين على `start_library.bat`. إذا حذّر SmartScreen: «More info» ثم «Run anyway».
3. أول مرة يجهّز بيئة بايثون (دقيقة تقريبًا). إذا سأل Windows هل تسمح لـ Python بالاتصال بالشبكة فاسمح على **الشبكات الخاصة**؛ بدون هذا لا تصل الجوالات.
4. تفتح صفحة الإدارة في المتصفح: `http://127.0.0.1:8802/admin`.

ملفات أخرى في المجلد:

| الملف | ماذا يفعل |
|---|---|
| `start_this_computer_only.bat` | تشغيل على هذا الجهاز فقط، للفحص |
| `backup_now.bat` | نسخة احتياطية الآن |
| `install_autostart.bat` | تشغيل تلقائي عند دخول مستخدم Windows، ونسخة احتياطية يومية 03:15 |
| `remove_autostart.bat` | التراجع عن التشغيل التلقائي |

اجعل «السكون» عند التوصيل بالكهرباء = أبدًا (إعدادات Windows ← النظام ← الطاقة).

## ٣. ماك

1. تأكد من Python 3: `python3 --version` في Terminal. إن لم يكن موجودًا فثبّته من [python.org/downloads](https://www.python.org/downloads/).
2. انقر مرتين على `start_library.command`. إذا منعه macOS: انقر عليه بالزر الأيمن ثم «فتح». وإن قال «permission denied» فاكتب مرة واحدة في Terminal داخل المجلد: `chmod +x *.command scripts/*.sh scripts/*.command`
3. أول مرة يجهّز بيئة بايثون (دقيقة تقريبًا)، ثم تفتح صفحة الإدارة: `http://127.0.0.1:8802/admin`.

ملفات أخرى: `start_this_computer_only.command` (هذا الجهاز فقط)، `backup_now.command` (نسخة احتياطية الآن)، `scripts/install_daily_backup.command` (نسخة يومية 03:15). ملف التشغيل يمنع سكون الجهاز ما دام السيرفر يعمل.

## ٤. أول إعداد (مرة واحدة)

1. صفحة الإدارة تطلب إنشاء **حساب المدير**: الاسم، اسم مستخدم بحروف إنجليزية، وكلمة مرور من 8 خانات على الأقل فيها حروف وأرقام. هذا النموذج يعمل من الجهاز الذي عليه السيرفر فقط.
2. الإعدادات ← الموظفون: أضف حسابًا لكل موظف.
3. الإعدادات ← الذكاء الاصطناعي: اختر النموذج واضغط «اختبار» (القسم التالي).
4. الإعدادات ← رمز QR: اطبع الرمز أو اعرضه على شاشة.

## ٥. نموذج الذكاء الاصطناعي

| الخيار | الخطوات |
|---|---|
| **Ollama** (المضبوط في الحزمة: `deepseek-v4.1-flash:cloud`) | ثبّت التطبيق من [ollama.com/download](https://ollama.com/download)، افتحه وسجّل الدخول (من التطبيق أو بالأمر `ollama signin`). النماذج السحابية تحتاج إنترنت ولا تحتاج تنزيلًا كبيرًا؛ والنماذج المحلية تحتاج تنزيلًا وذاكرة كافية. |
| **Ollama Cloud** | أنشئ مفتاحًا من [ollama.com/settings/keys](https://ollama.com/settings/keys) والصقه في صفحة الإعدادات. لا يحتاج تطبيق Ollama. |
| **مفتاح API** | Claude أو ChatGPT أو Gemini أو OpenRouter أو مزوّد متوافق مع OpenAI: اختر المزوّد والصق المفتاح. |
| **ChatGPT عبر Codex** | ثبّت Codex CLI وسجّل الدخول بالأمر `codex login`، ثم اختر «ChatGPT عبر Codex». |

- المفاتيح تُحفظ في `~/.library_keeper/keys.json` خارج مجلد التطبيق، ولا تُرسل إلى المتصفح.
- إذا لم يتصل النموذج يبقى التطبيق يعمل ويرشّح بالكلمات المفتاحية.
- البحث بالمعنى يحتاج Ollama والنموذج `bge-m3` (الأمر: `ollama pull bge-m3`). بدونه يُتخطى بصمت.

## ٦. الجوال ورمز QR

- العنوان ورمز QR في: صفحة الإدارة ← الإعدادات ← رمز QR. العنوان هو عنوان الجهاز على الواي فاي، مثل `http://192.168.1.20:8802`.
- الأفضل تثبيت عنوان الجهاز من الراوتر (DHCP reservation) ثم طباعة الرمز. إن لم يُثبَّت فاعرض الرمز على شاشة بدل طباعته، لأنه يتغير مع العنوان.
- لا تفتح السيرفر على الإنترنت (لا port forwarding ولا أنفاق). هو للشبكة المحلية.

## ٧. النسخ الاحتياطي والاسترجاع

- نسخة احتياطية قبل كل تشغيل، تُحفظ في `backups/` (آخر 30 نسخة).
- الاسترجاع: أوقف السيرفر ثم
  - ويندوز: `backend\venv-win\Scripts\python.exe scripts\restore_db.py --latest`
  - ماك: `backend/venv/bin/python scripts/restore_db.py --latest`
- الكتالوج كله في ملف واحد `data/library.db`، والأغلفة في `data/covers`.

## ٨. التحديث إلى إصدار جديد

1. شغّل `backup_now` وأوقف السيرفر.
2. نزّل الحزمة الجديدة وفكها في مجلد جديد.
3. إذا عدّلتم بيانات على جهازكم (حسابات، كتب، فعاليات): انسخ `data/library.db` ومجلدي `data/covers` و`data/event_images` من المجلد القديم إلى الجديد فوق الموجود. إن لم تعدّلوا شيئًا فاستعملوا قاعدة الحزمة الجديدة لأنها الأحدث.
4. شغّل من المجلد الجديد. احتفظ بالمجلد القديم أسبوعًا.

## ٩. إذا حصلت مشكلة

| العَرَض | الحل |
|---|---|
| الجوال لا يفتح العنوان | تأكد أنه على نفس الواي فاي، وأن Python مسموح له في جدار الحماية (ويندوز: الشبكات الخاصة)، وأن شبكة الضيوف لا تعزل الأجهزة. |
| «السيرفر شغّال من قبل» | نافذة سيرفر أخرى مفتوحة؛ أغلقها ثم شغّل من جديد. |
| `Python 3 is not installed` أو تعذر إنشاء البيئة | ثبّت Python ثم شغّل مرة ثانية. أول تشغيل يحتاج إنترنت. |
| الردود بسيطة أو بلا ذكاء اصطناعي | الإعدادات ← الذكاء الاصطناعي ← «اختبار». مع Ollama: تأكد أنه مفتوح ومسجّل الدخول. |
| نموذج إنشاء المدير يرفض | افتحه من الجهاز نفسه على `http://127.0.0.1:8802/admin`. |
| نسيت كلمة المرور | لا تُسترجع من الصفحة. المدير يغيّر كلمات مرور الموظفين من الإعدادات ← الموظفون. |

عند أي خطأ: انسخ آخر الأسطر في نافذة السيرفر، ومعها `backups/backup_log.txt` إن كان الخطأ في النسخ الاحتياطي.

## للمطورين: التشغيل من المستودع

المستودع فيه الكود فقط. القاعدة والأغلفة تؤخذ من حزمة الإصدار:

```
git clone https://github.com/eyesmystery/library-keeper.git
cd library-keeper
# انسخ مجلد data من حزمة الإصدار المفكوكة إلى هنا (data/library.db و data/covers و data/event_images)
python3 -m venv backend/venv
backend/venv/bin/pip install -r backend/requirements.txt
cd backend && LIBRARY_KEEPER_HOST=0.0.0.0 ./venv/bin/python app.py
```

على ويندوز: `py -3 -m venv backend\venv-win` ثم `backend\venv-win\Scripts\python -m pip install -r backend\requirements.txt` ثم من داخل `backend`: `set LIBRARY_KEEPER_HOST=0.0.0.0` و `venv-win\Scripts\python app.py`. احذف سطر `LIBRARY_KEEPER_HOST` للتشغيل على الجهاز نفسه فقط.

- الاختبارات: `backend/venv/bin/python tests/run_all.py`
- بناء حزمتي التحميل من مجلد عمل فيه القاعدة الحية: `backend/venv/bin/python scripts/make_release.py --version "0.9.6 beta" --no-user` (يكتب في `release/`).
- لا ترفع `data/library.db` ولا `backups/` ولا `data/test_logs/` ولا `data/llm_settings.json` ولا أي ملف مفاتيح؛ كلها في `.gitignore`.

## Install in English

Thalath Smart is a small Flask + SQLite web app (port 8802). No build step, no Node, no Docker.

1. Download the package for your system from [Releases](https://github.com/eyesmystery/library-keeper/releases/latest) (`…-windows.zip` or `…-mac.zip`, about 490 MB; the repository is private, so sign in first, or use `gh release download --repo eyesmystery/library-keeper --pattern "*mac.zip"`). Unzip it to a permanent folder.
2. Install Python 3.9+ (Windows: tick "Add python.exe to PATH").
3. Double-click `start_library.bat` (Windows) or `start_library.command` (macOS; right-click → Open the first time). The first start creates a virtual environment and needs internet. Allow Python on private networks when the system asks.
4. Open `http://127.0.0.1:8802/admin` on the same computer and create the manager account (first run only; the package ships with no accounts).
5. Settings → AI: choose a provider and press test. Options: the Ollama app (signed in; `:cloud` models need no large download), Ollama Cloud with an API key, an API key for Claude / ChatGPT / Gemini / OpenRouter / any OpenAI-compatible endpoint, or ChatGPT through the Codex CLI (`codex login`). Keys are stored outside the project in `~/.library_keeper/keys.json`. Without a model the app still answers with keyword matching.
6. Settings → QR: phones on the same Wi-Fi scan the code.

Verify (agents: run and report):

```
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8802/         # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8802/admin    # 200
curl -s http://127.0.0.1:8802/api/admin/session                        # "needs_setup": true before the manager account exists
```

Rules for agents: do not change application code or data to fix a start-up problem, report the error text; never commit databases, backups, logs or keys; do not expose the server to the internet.
