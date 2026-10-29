// Visitor chat with the library assistant. Conversation state and language
// choice live only in this page's memory.
const API = `${location.origin}/api`;
const $ = id => document.getElementById(id);

const COPY = {
  ar: {
    title: 'منار | مكتبة ثلاث', description: 'منار تساعدك تختار كتابًا متوفرًا الآن في مكتبة ثلاث.',
    switchLanguage: 'Switch to English', tagline: 'الذكي',
    slogan: 'منار - ذكاء يرشدك إلى ما يناسبك',   // Ibrahim 2026-10-23
    hero: 'وش ودّك<br>تقرأ اليوم؟',
    welcomeBody: 'سولف معي عن مزاجك، وألقى لك كتاب من الموجود عندنا.',
    startNote: 'تقدر تبدأ بكلمة، شعور، أو آخر كتاب عجبك.', start: 'سولف معي عن مزاجك',
    discover: 'اكتشف', ask: 'اسأل', read: 'اقرأ', follow: 'تابع مكتبة ثلاث', followOn: 'مكتبة ثلاث على', networks: { instagram: 'انستقرام', snapchat: 'سناب شات', tiktok: 'تيك توك', x: 'إكس' }, newChat: 'جديدة', assistant: 'منار',
    modelFallback: 'النموذج غير متاح · نكمل بالنظام البديل',
    testBanner: 'وضع الاختبار مفعّل: هذي المحادثة تنحفظ عشان نراجعها ونحسّن المساعد.',
    conversation: 'المحادثة', suggestedReplies: 'ردود مقترحة', inputLabel: 'اكتب رسالتك لمنار',
    placeholder: 'اكتب رسالتك هنا…', send: 'إرسال', trust: 'ما نطلب اسمك، والمحادثة تنمسح لما تبدأ من جديد.',
    back: 'رجوع', home: 'الرجوع للصفحة الرئيسية', accordingReaders: 'حسب القرّاء', closeChoice: 'اختيار قريب من وصفك', language: 'اللغة',
    findAt: 'بتلقاه في', want: 'أبغى هذا الكتاب', feedbackTitle: 'اختيار جميل',
    feedbackQuestion: 'هل كانت التوصية مناسبة؟', perfect: 'مناسبة جدًا', close: 'قريبة', no: 'غير مناسبة',
    greeting: 'أهلًا فيك! أنا منار. قل لي وش مزاجك اليوم، أو وش آخر كتاب عجبك، وأساعدك تلقى كتاب يناسبك من الموجود عندنا.',
    startChips: ['أدوّر كتاب معيّن', 'رشّحي لي كتاب يناسب ذوقي أو مزاجي', 'أبغى كتاب هدية', 'أبغى أبدأ قراءة', 'فاجئيني بكتب جديدة'],
    detailsAria: (title, author) => `عرض تفاصيل كتاب ${title} للكاتب ${author}`,
    pages: n => `${n} صفحة`, unknownLength: 'الطول غير معروف', shelf: value => `الرف ${value}`,
    askStaff: 'موقعه عند الموظف', available: 'متوفر', fallbackReply: 'هذي خيارات متوفرة الآن.',
    fallbackChips: ['ورّني غيرها', 'شي أخف', 'شي أقصر', 'رشّحي لي رواية', 'فاجئيني بكتاب'], typing: 'منار تكتب',
    connectionError: 'تعذر الوصول لمنار الآن. تأكد من الاتصال وجرّب مرة ثانية.',
    book: 'كتاب', shelfUnknown: 'اسأل موظف المكتبة يدلّك عليه', whereLabel: 'مكانه', unknown: 'غير معروف', currency: value => `${value} ر.س`,
    selectedAt: (title, place) => `ستجد «${title}» في ${place}. ويمكن لموظف المكتبة مساعدتك.`,
    selectedHelp: title => `اخترت «${title}». ويمكن لموظف المكتبة مساعدتك في الوصول إليه.`,
    choiceFailed: 'ما قدرنا نسجّل اختيارك الآن. جرّب مرة ثانية.',
    feedbackSaved: 'شكرًا لك، تم تسجيل تقييمك.', feedbackFailed: 'ما قدرنا نحفظ تقييمك الآن، شكرًا لك على كل حال.',
    enjoy: title => `قراءة ممتعة مع «${title}»! إذا تبغى اقتراح ثاني أو تسأل عن كتاب، أنا هنا.`,
    byAuthor: name => `كتب ${name} عندنا`, byPublisher: name => `من إصدارات ${name}`, publisherLabel: 'الدار',
    moreCount: n => `المتوفر الآن: ${n}`, moreNone: 'ما عندنا كتاب ثاني متوفر الآن.', moreLoading: 'لحظة…',
    moreFailed: 'تعذر التحميل، حاول مرة ثانية.', moreClose: 'إغلاق',
    authorAria: name => `كتب ${name} الثانية`, publisherAria: name => `كتب ثانية من ${name}`,
    picksTitle: 'اختياراتنا هذا الشهر', picksAria: (title, author) => `اختيارنا هذا الشهر: ${title}${author ? '، ' + author : ''}`,
    eventsTitle: 'فعالياتنا', eventKicker: 'فعالية في مكتبة ثلاث', eventBook: 'عن كتاب', eventToday: 'اليوم', eventTomorrow: 'بكرة',
    eventsAria: title => `تفاصيل الفعالية: ${title}`, yearLabel: 'سنة النشر',
    addCalendar: 'أضف إلى التقويم', addGoogle: 'أو في تقويم Google', calendarPlace: 'مكتبة ثلاث',
    contextTitle: 'نبحث عن:', contextRemove: label => `إلغاء: ${label}`,
    contextRemoved: label => `شلنا «${label}». اكتب طلبك، أو اضغط «ورّني اقتراحات».`, showNow: 'ورّني اقتراحات',
    afterFeedback: ['اقترح لي كتاب ثاني', 'محادثة جديدة', 'شي أقصر', 'رشّحي لي رواية', 'أبغى كتاب هدية'], oneCopy: 'نسخة واحدة', twoCopies: 'نسختان',
    copies: n => `${n} نسخ`, availableSuffix: ' متوفرة', switchReset: 'غيّرنا اللغة وبدأنا محادثة جديدة.',
    reportBtn: 'فيه معلومة غلط؟ بلّغنا', reportTitle: 'وش الغلط في معلومات الكتاب؟', reportLead: title => `«${title}»`,
    reportReasons: [['title_author', 'العنوان أو المؤلف'], ['cover', 'الغلاف'], ['summary', 'النبذة'], ['details', 'التصنيف أو الصفحات أو اللغة'], ['location', 'المكان أو التوفر'], ['other', 'شي ثاني']],
    reportReasonsLabel: 'نوع الخطأ', reportNoteLabel: 'تفاصيل أكثر (اختياري)', reportPlaceholder: 'مثلًا: اسم المؤلف الصحيح…',
    reportSent: 'وصلنا بلاغك، شكرًا لك.', needReason: 'اختر وش الغلط أول.', needNote: 'اكتب لنا وش الغلط.',
    suggestBtn: 'عندك اقتراح؟', suggestTitle: 'اقتراحك يهمّنا', suggestLead: 'كتاب تتمنى نوفّره، أو شي يخلّي منار أحسن. ما نطلب اسمك.',
    suggestNoteLabel: 'اقتراحك', suggestPlaceholder: 'اكتب اقتراحك هنا…', suggestSent: 'وصلنا اقتراحك، شكرًا لك.', needSuggestion: 'اكتب اقتراحك أول.',
    noteSend: 'إرسال', noteSending: 'جاري الإرسال…', noteCancel: 'إلغاء', noteFailed: 'ما قدرنا نرسلها الآن. جرّب مرة ثانية.', noteBusy: 'وصلتنا رسائل كثيرة من هنا. جرّب بعد شوي.',

    // «ماذا تقرأ الآن؟» (Ibrahim 2026-10-27)
    reading: 'ماذا تقرأ الآن؟', readingLead: 'اكتب اسم الكتاب اللي تقرأه أو قرأته من مكتبة ثلاث، وقل لنا رأيك فيه.',
    searchLabel: 'اسم الكتاب أو المؤلف', searchPlaceholder: 'مثلًا: لاعب الشطرنج', searching: 'لحظة…',
    noResults: 'ما لقينا هذا الكتاب في مكتبتنا. تقدر تقترحه علينا تحت.', notInStock: 'غير متوفر حاليًا',
    shelfLead: 'كتابك مو عندنا؟', shelfBtn: 'ماذا تقترح أن يكون على رفوف مكتبة ثلاث؟', shelfStart: 'اقترح كتابًا لرفوفنا',
    starsLabel: 'كم تعطيه من ٥؟', starAria: n => `${n} من ٥`, tagsLabel: 'وش تقول عنه؟ (اختياري)',
    reviewTags: [['gripping', 'يشد'], ['fun', 'ممتع'], ['moving', 'مؤثر'], ['easy', 'سهل القراءة'], ['slow', 'بطيء'], ['heavy', 'ثقيل']],
    reviewTextLabel: 'رأيك بكلماتك (اختياري)', reviewTextPlaceholder: 'وش عجبك أو ما عجبك فيه؟ بدون حرق للأحداث.',
    nameLabel: 'اسمك الأول (اختياري، يظهر مع رأيك)', reviewNote: 'رأيك يظهر للزوار بعد ما يراجعه موظفو المكتبة.',
    sendReview: 'أرسل رأيي', otherBook: 'كتاب آخر', needStars: 'اختر عدد النجوم أول.',
    reviewSent: title => `شكرًا لك! وصلنا رأيك في «${title}»، وينعرض للزوار بعد ما يراجعه موظفو المكتبة.`,
    again: 'رأي في كتاب ثاني', homeBtn: 'الصفحة الأولى',
    shelfFormLead: 'اكتب اسم الكتاب اللي تتمنى نوفّره، ونتأكد معك منه.', bookLabel: 'اسم الكتاب', authorLabel: 'المؤلف (اختياري)',
    check: 'ابحث عنه', needTitle: 'اكتب اسم الكتاب أول.', lookingUp: 'لحظة، أدوّر عليه…',
    isHere: 'عندنا هذا الكتاب ومتوفر الحين! هذي بطاقته:', notHereNow: 'هذا الكتاب في مكتبتنا، بس نسخه نافدة الحين.', wantBack: 'أبغاه يتوفر',
    foundAsk: 'لقيت هذا الكتاب. هل هو اللي تقصده؟', yesThis: 'نعم، هذا هو', notThis: 'لا، مو هو',
    sourceLabel: s => `المصدر: ${s}`, byLabel: a => `تأليف ${a}`,
    notFound: 'ما لقيت معلومات عنه. نحفظ اقتراحك مثل ما كتبته؟', saveAsTyped: 'احفظ اقتراحي',
    candidateSaved: title => `شكرًا! سجّلنا «${title}» ككتاب مرشّح لرفوف مكتبة ثلاث.`,
    wantSaved: title => `شكرًا! وصل طلبك أن يتوفر «${title}» مرة ثانية.`, suggestionSaved: 'شكرًا! وصلنا اقتراحك.',
    suggestionNote: (title, author) => `كتاب مقترح للرفوف: ${title}${author ? ' — ' + author : ''}`,
    reviewsTitle: 'آراء قرّاء مكتبة ثلاث', reviewsSum: (avg, n) => `${formatNumber(avg)} من ٥ · ${n === 1 ? 'قارئ واحد' : n === 2 ? 'قارئان' : formatNumber(n) + (n <= 10 ? ' قرّاء' : ' قارئًا')}`,
    anonymousReader: 'قارئ من مكتبة ثلاث', reviewBtn: 'قرأته؟ شاركنا رأيك',
    favoritesChip: 'رشّحي لي تفضيلات قرّاء مكتبة ثلاث', readingFailed: 'ما قدرنا نرسلها الآن. جرّب مرة ثانية.',
  },
  en: {
    title: 'Manar | Thalath', description: 'Manar, the book guide at Thalath, helps you choose a book that is available now.',
    switchLanguage: 'التبديل للعربية', tagline: 'SMART LIBRARY GUIDE',
    slogan: 'Manar - Intelligence that guides you to what fits you',   // Ibrahim 2026-10-23
    hero: 'What would you<br>like to read today?',
    welcomeBody: "Tell me what you're in the mood for, and I'll find a book that's here now.",
    startNote: 'Start with a word, a feeling, or the last book you loved.', start: "Tell me what you're in the mood for",
    discover: 'Discover', ask: 'Ask', read: 'Read', follow: 'Follow Thalath', followOn: 'Thalath on', networks: { instagram: 'Instagram', snapchat: 'Snapchat', tiktok: 'TikTok', x: 'X' }, newChat: 'New', assistant: 'Manar',
    modelFallback: 'Model unavailable · using fallback',
    testBanner: 'Test mode is on: this conversation is saved so we can review and improve the assistant.',
    conversation: 'Conversation', suggestedReplies: 'Suggested replies', inputLabel: 'Write a message to Manar',
    placeholder: 'Write your message…', send: 'Send', trust: 'We do not ask for your name, and this chat clears when you start again.',
    back: 'Back', home: 'Back to the start page', accordingReaders: 'According to readers', closeChoice: 'A close match for what you described', language: 'Language',
    findAt: 'Find it at', want: 'I want this book', feedbackTitle: 'Lovely choice',
    feedbackQuestion: 'Was this recommendation a good fit?', perfect: 'Perfect fit', close: 'Close', no: 'Not for me',
    greeting: "Welcome! I'm Manar, your book guide at Thalath. Tell me your mood today, or the last book you enjoyed, and I'll help you find something available here.",
    startChips: ['Find a specific book', 'Recommend something for my taste or mood', 'I want a gift book', 'I want to start reading', 'Surprise me with new books'],
    detailsAria: (title, author) => `View details for ${title} by ${author}`,
    pages: n => `${n} pages`, unknownLength: 'Length unknown', shelf: value => `Shelf ${value}`,
    askStaff: 'Ask a bookseller for its location', available: 'Available', fallbackReply: 'Here are a few books available now.',
    fallbackChips: ['Show me others', 'Something lighter', 'Something shorter', 'Suggest a novel', 'Surprise me'], typing: 'Manar is typing',
    connectionError: 'Manar is unavailable right now. Check your connection and try again.',
    book: 'Book', shelfUnknown: 'Ask a bookseller to show you', whereLabel: 'Where to find it', unknown: 'Unknown', currency: value => `SAR ${value}`,
    selectedAt: (title, place) => `You will find “${title}” at ${place}. A bookseller can help you too.`,
    selectedHelp: title => `You chose “${title}”. A bookseller can help you find it.`,
    choiceFailed: 'We could not record your choice right now. Please try again.',
    feedbackSaved: 'Thank you — your feedback was saved.', feedbackFailed: 'We could not save your feedback, but thank you all the same.',
    enjoy: title => `Enjoy “${title}”! If you want another suggestion or have a question, I'm here.`,
    byAuthor: name => `Books by ${name} here`, byPublisher: name => `Published by ${name}`, publisherLabel: 'Publisher',
    moreCount: n => `Available now: ${n}`, moreNone: 'We have no other book available right now.', moreLoading: 'One moment…',
    moreFailed: 'Could not load, please try again.', moreClose: 'Close',
    authorAria: name => `Other books by ${name}`, publisherAria: name => `Other books from ${name}`,
    picksTitle: 'Our picks this month', picksAria: (title, author) => `Our pick this month: ${title}${author ? ', ' + author : ''}`,
    eventsTitle: 'Our events', eventKicker: 'An event at Thalath', eventBook: 'About the book', eventToday: 'Today', eventTomorrow: 'Tomorrow',
    eventsAria: title => `Event details: ${title}`, yearLabel: 'Published',
    addCalendar: 'Add to calendar', addGoogle: 'or to Google Calendar', calendarPlace: 'Thalath Bookshop',
    contextTitle: 'Searching for:', contextRemove: label => `Remove: ${label}`,
    contextRemoved: label => `Removed “${label}”. Type your request, or tap “Show me suggestions”.`, showNow: 'Show me suggestions',
    afterFeedback: ['Suggest another book', 'New conversation', 'Something shorter', 'Suggest a novel', 'I want a gift book'], oneCopy: '1 copy', twoCopies: '2 copies',
    copies: n => `${n} copies`, availableSuffix: ' available', switchReset: 'Language changed — we started a new conversation.',
    reportBtn: 'Something wrong? Tell us', reportTitle: 'What is wrong with this book’s details?', reportLead: title => `“${title}”`,
    reportReasons: [['title_author', 'Title or author'], ['cover', 'Cover'], ['summary', 'Summary'], ['details', 'Genre, pages or language'], ['location', 'Location or availability'], ['other', 'Something else']],
    reportReasonsLabel: 'What is wrong', reportNoteLabel: 'More details (optional)', reportPlaceholder: 'For example: the correct author name…',
    reportSent: 'We got your report. Thank you.', needReason: 'Choose what is wrong first.', needNote: 'Tell us what is wrong.',
    suggestBtn: 'Have a suggestion?', suggestTitle: 'We would love your suggestion', suggestLead: 'A book you wish we had, or something that would make Manar better. We do not ask for your name.',
    suggestNoteLabel: 'Your suggestion', suggestPlaceholder: 'Write your suggestion…', suggestSent: 'We got your suggestion. Thank you.', needSuggestion: 'Write your suggestion first.',
    noteSend: 'Send', noteSending: 'Sending…', noteCancel: 'Cancel', noteFailed: 'We could not send it right now. Please try again.', noteBusy: 'We received many messages from here. Try again in a little while.',

    // «What are you reading now?» (Ibrahim 2026-10-27)
    reading: 'What are you reading now?', readingLead: 'Write the title of a book from Thalath you are reading or have read, and tell us what you think.',
    searchLabel: 'Book title or author', searchPlaceholder: 'For example: The Chess Player', searching: 'One moment…',
    noResults: 'We could not find this book in our shop. You can suggest it to us below.', notInStock: 'Not in stock now',
    shelfLead: 'Not in our shop?', shelfBtn: 'What would you like to see on Thalath’s shelves?', shelfStart: 'Suggest a book for our shelves',
    starsLabel: 'How many stars out of 5?', starAria: n => `${n} out of 5`, tagsLabel: 'How would you describe it? (optional)',
    reviewTags: [['gripping', 'Gripping'], ['fun', 'Enjoyable'], ['moving', 'Moving'], ['easy', 'Easy to read'], ['slow', 'Slow'], ['heavy', 'Heavy']],
    reviewTextLabel: 'Your words (optional)', reviewTextPlaceholder: 'What did you like or not? No spoilers, please.',
    nameLabel: 'Your first name (optional, shown with your review)', reviewNote: 'Your review is shown to visitors after the bookshop staff check it.',
    sendReview: 'Send my review', otherBook: 'Another book', needStars: 'Choose the stars first.',
    reviewSent: title => `Thank you! We got your review of “${title}”. It is shown to visitors after the staff check it.`,
    again: 'Review another book', homeBtn: 'Start page',
    shelfFormLead: 'Write the title of the book you wish we had, and we will check it with you.', bookLabel: 'Book title', authorLabel: 'Author (optional)',
    check: 'Look it up', needTitle: 'Write the book title first.', lookingUp: 'One moment, looking it up…',
    isHere: 'We have this book in stock now! Here it is:', notHereNow: 'This book is in our shop, but it is out of stock now.', wantBack: 'I want it back',
    foundAsk: 'I found this book. Is it the one you mean?', yesThis: 'Yes, that is it', notThis: 'No, not this one',
    sourceLabel: s => `Source: ${s}`, byLabel: a => `by ${a}`,
    notFound: 'I could not find details about it. Shall we keep your suggestion as you wrote it?', saveAsTyped: 'Keep my suggestion',
    candidateSaved: title => `Thank you! “${title}” is now a suggested book for Thalath’s shelves.`,
    wantSaved: title => `Thank you! We noted that you want “${title}” back in stock.`, suggestionSaved: 'Thank you! We got your suggestion.',
    suggestionNote: (title, author) => `Book suggested for the shelves: ${title}${author ? ' — ' + author : ''}`,
    reviewsTitle: 'What Thalath readers say', reviewsSum: (avg, n) => `${formatNumber(avg)} out of 5 · ${formatNumber(n)} ${n === 1 ? 'reader' : 'readers'}`,
    anonymousReader: 'A Thalath reader', reviewBtn: 'Read it? Share your review',
    favoritesChip: "Thalath readers' favourites", readingFailed: 'We could not send it right now. Please try again.',
  }
};

const GENRES_EN = {
  'رواية': 'Novel', 'روايات': 'Novels', 'أدب': 'Literature', 'قصص قصيرة': 'Short stories',
  'شعر': 'Poetry', 'سيرة': 'Biography', 'تاريخ': 'History', 'فكر': 'Thought', 'علم نفس': 'Psychology',
  'تطوير ذاتي': 'Self-development', 'علوم': 'Science', 'مسرحية': 'Play', 'أدب رحلات': 'Travel writing',
  'ديني': 'Religion', 'دين': 'Religion', 'سيرة ذاتية': 'Biography', 'سيرة نبوية': "The Prophet's biography",
  'أطفال': "Children's", 'يافعين': 'Young adult', 'أعمال': 'Business',
  'قصص مصورة': 'Comics & manga', 'مقالات ونصوص': 'Essays', 'نقد أدبي': 'Literary criticism', 'علم اجتماع': 'Sociology', 'فنون وسينما': 'Arts & cinema'
};

let currentLanguage = 'ar';
let currentSection = 'welcome';
let messages = [];
let conversationId = newId();
let busy = false;
let pendingChat = null;       // AbortController of the chat request in flight, if any
let noteSeq = 0;              // counts openings of the note sheet, so an old send cannot close a newer sheet
let selectedBook = null;
let choiceId = null;          // id of the recorded «أبغى هذا الكتاب» choice (anonymous)
let choiceToken = null;       // token returned with that choice; a rating is accepted only with both
let chosenInChat = {};        // book_id -> { id, token }, so one book is counted once per conversation
// «نبحث لـ: طفل ✕»: who the assistant is searching for, as the server understood it; the visitor
// can remove it, and the server then searches for adults until a child is named again (2026-10-08)
// «نبحث عن: رواية ✕ · قصيرة ✕ · طفلة عمرها ٦ ✕»: what the search applies now, as the server
// reports it; ✕ sends the badge back in drop_context until the visitor asks for it again (2026-10-08)
let contextItems = [];
let droppedContext = [];
let audienceCleared = false;
let toastTimer = null;
let noteMode = null;          // 'report' | 'suggestion' while the note sheet is open
let noteReason = null;
let noteOpener = null;
let noteSending = false;
const t = key => COPY[currentLanguage][key];

function applyLanguage() {
  const english = currentLanguage === 'en';
  document.documentElement.lang = currentLanguage;
  document.documentElement.dir = english ? 'ltr' : 'rtl';
  document.title = t('title');
  $('metaDescription').content = t('description');
  $('brandSlogan').textContent = t('slogan');
  $('welcomeTitle').innerHTML = t('hero');
  $('welcomeBody').textContent = t('welcomeBody');
  $('startNote').textContent = t('startNote');
  $('startLabel').textContent = t('start');
  $('stepDiscover').textContent = t('discover');
  $('stepAsk').textContent = t('ask');
  $('stepRead').textContent = t('read');
  $('socialLabel').textContent = t('follow');
  $('eventsTitle').textContent = t('eventsTitle');
  $('eKicker').textContent = t('eventKicker');
  $('eBookLabel').textContent = t('eventBook');
  $('eCalendarIcs').textContent = t('addCalendar');
  $('eCalendarGoogle').textContent = t('addGoogle');
  $('eventBackBtn').textContent = t('back');
  $('picksTitle').textContent = t('picksTitle');
  loadPicks();
  loadEvents();
  document.querySelectorAll('.social-link').forEach(link => { link.setAttribute('aria-label', `${t('followOn')} ${t('networks')[link.dataset.network]}`); });
  $('newChatBtn').textContent = t('newChat');
  $('chatTitle').textContent = t('assistant');
  $('modelStatus').textContent = t('modelFallback');
  $('testBanner').textContent = t('testBanner');
  $('chatLog').setAttribute('aria-label', t('conversation'));
  $('chips').setAttribute('aria-label', t('suggestedReplies'));
  $('chatInputLabel').textContent = t('inputLabel');
  $('chatInput').placeholder = t('placeholder');
  $('sendBtn').setAttribute('aria-label', t('send'));
  $('trustNote').textContent = t('trust');
  ['backBtn', 'chatBackBtn', 'feedbackBackBtn', 'readingBackBtn'].forEach(id => { $(id).textContent = t('back'); });
  applyReadingLanguage();
  document.querySelectorAll('.brand-home').forEach(button => button.setAttribute('aria-label', t('home')));
  renderContext();
  $('readerLabel').textContent = t('accordingReaders');
  $('languageLabel').textContent = t('language');
  $('yearLabel').textContent = t('yearLabel');
  $('findLabel').textContent = selectedBook && !locationText(selectedBook) ? t('whereLabel') : t('findAt');
  $('wantBtn').textContent = t('want');
  $('feedbackTitle').textContent = t('feedbackTitle');
  $('feedbackQuestion').textContent = t('feedbackQuestion');
  $('feedbackPerfect').textContent = t('perfect');
  $('feedbackClose').textContent = t('close');
  $('feedbackNo').textContent = t('no');
  $('reportBtn').textContent = t('reportBtn');
  $('suggestBtn').textContent = t('suggestBtn');
  if (noteMode) closeNote();
  const nextCode = english ? 'AR' : 'EN';
  ['welcomeLangBtn', 'chatLangBtn', 'detailLangBtn', 'feedbackLangBtn', 'readingLangBtn'].forEach(id => {
    $(id).textContent = nextCode;
    $(id).setAttribute('aria-label', t('switchLanguage'));
  });
  if (selectedBook && currentSection === 'bookDetail') showBook(selectedBook);
  if (selectedBook && currentSection === 'feedback') updateFeedbackLocation();
}

function switchLanguage() {
  if (busy) return;
  currentLanguage = currentLanguage === 'ar' ? 'en' : 'ar';
  const hadConversation = messages.length > 0 || currentSection !== 'welcome';
  applyLanguage();
  if (hadConversation) {
    newChat();
    showToast(t('switchReset'));
  }
}

function bubble(role, text, extraClass = '') {
  const wrap = document.createElement('div');
  wrap.className = `msg msg-${role} ${extraClass}`.trim();
  const p = document.createElement('p');
  p.className = 'msg-text';
  p.textContent = text;
  wrap.appendChild(p);
  $('chatLog').appendChild(wrap);
  return wrap;
}

// Location is never invented: a shelf typed by staff, else the library section of the book's
// category (server side), else nothing and the visitor is asked to check with a staff member.
function locationText(book) {
  if (!book || !book.location) return null;
  return book.location_kind === 'shelf' && /^[A-Za-z]?\d+[A-Za-z]?$/.test(book.location) ? t('shelf')(book.location) : book.location;
}

function bookCard(book, index) {
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'book-card';
  card.style.animationDelay = `${index * 45}ms`;
  card.setAttribute('aria-label', t('detailsAria')(book.title, book.author));
  const initial = [...String(book.title || (currentLanguage === 'ar' ? 'ك' : 'B')).trim()][0] || 'B';
  // an unknown detail is left out, not written as «غير معروف» (Ibrahim, 2026-10-08)
  const pages = book.page_count ? t('pages')(formatNumber(book.page_count)) : '';
  // the chat card says «متوفر» without the count, and shows the price (Ibrahim, 2026-10-11)
  const price = Number(book.price) > 0 ? t('currency')(formatNumber(book.price)) : '';
  const shelf = locationText(book) || t('askStaff');
  card.innerHTML = `<span class="book-card-copy"><span class="book-title">${escapeHtml(book.title)}</span><span class="book-author">${escapeHtml(book.author)}</span><span class="book-reason">${escapeHtml(book.reason ?? t('closeChoice'))}</span><span class="book-meta"><span class="meta-chip stock-chip">${escapeHtml(t('available'))}</span>${price ? `<span class="meta-chip price-chip">${escapeHtml(price)}</span>` : ''}${pages ? `<span class="meta-chip">${escapeHtml(pages)}</span>` : ''}<span class="meta-chip">${escapeHtml(shelf)}</span></span></span>${book.cover_url ? `<span class="book-token has-cover" aria-hidden="true"><img src="${escapeHtml(book.cover_url)}" alt="" loading="lazy" /></span>` : `<span class="book-token" aria-hidden="true"><span>${escapeHtml(initial)}</span></span>`}`;
  card.addEventListener('click', () => showBook(book));
  return card;
}

function botMessage(text, books = [], extraClass = '') {
  const wrap = bubble('bot', text, extraClass);
  if (books.length) {
    // the suggested books come first, then Manar's text and question (Ibrahim, 2026-10-11)
    const list = document.createElement('div');
    list.className = 'chat-books books-first';
    books.forEach((book, index) => list.appendChild(bookCard(book, index)));
    wrap.prepend(list);
  }
  if ($('chatLog').children.length > 1) wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
  return wrap;
}

function setChips(items) {
  const box = $('chips');
  box.replaceChildren();
  items.forEach(text => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'chip';
    chip.textContent = text;
    const newConversation = COPY.ar.afterFeedback[1] === text || COPY.en.afterFeedback[1] === text;
    chip.addEventListener('click', () => (newConversation ? newChat() : send(text)));
    box.appendChild(chip);
  });
  box.hidden = items.length === 0;
}

function typing(on) {
  let el = $('typing');
  if (on && !el) {
    el = document.createElement('div');
    el.id = 'typing';
    el.className = 'msg msg-bot typing';
    el.setAttribute('aria-label', t('typing'));
    el.innerHTML = '<span></span><span></span><span></span>';
    $('chatLog').appendChild(el);
    scrollToEnd();
  } else if (!on && el) el.remove();
}

function setBusy(value) {
  busy = value;
  $('sendBtn').disabled = value;
  $('sendIcon').hidden = value;
  $('sendSpinner').hidden = !value;
  $('sendBtn').setAttribute('aria-busy', String(value));
  document.querySelectorAll('.chip, .language-button').forEach(control => { control.disabled = value; });
}

function scrollToEnd() {
  const last = $('chatLog').lastElementChild;
  if (last) last.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function setModelStatus(fallbackActive) {
  $('modelStatus').hidden = !fallbackActive;
}

async function send(text) {
  const content = String(text || '').trim();
  if (!content || busy) return;
  bubble('user', content);
  messages.push({ role: 'user', content });
  $('chatInput').value = '';
  autoGrow();
  setChips([]);
  setBusy(true);
  typing(true);
  // The reply belongs to the conversation it was asked in. If the visitor starts a new chat while
  // waiting, the request is cancelled and a late answer is dropped (audit 2026-10-02).
  const askedIn = conversationId;
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  pendingChat = controller;
  try {
    const response = await fetch(`${API}/chat`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, conversation_id: conversationId, language: currentLanguage, clear_audience: audienceCleared, drop_context: droppedContext }),
      signal: controller ? controller.signal : undefined
    });
    if (askedIn !== conversationId) return;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (askedIn !== conversationId) return;
    const books = Array.isArray(data.books) ? data.books : [];
    setModelStatus(data.understanding !== 'ai');
    setTestMode(!!data.test_mode);
    if (Array.isArray(data.context)) {
      contextItems = data.context.filter(item => item && item.key && item.label);
      // a badge back in the answer was asked for again: it is no longer dropped
      droppedContext = droppedContext.filter(key => !contextItems.some(item => item.key === key));
      if (contextItems.some(item => item.key === 'audience')) audienceCleared = false;
      renderContext();
    }
    typing(false);
    botMessage(data.reply || t('fallbackReply'), books);
    messages.push({ role: 'assistant', content: data.reply || '', book_ids: books.map(book => book.book_id) });
    const chips = Array.isArray(data.suggestions) ? data.suggestions : [];
    setChips(chips.length || !books.length ? chips : t('fallbackChips'));
  } catch (error) {
    if (askedIn !== conversationId) return;
    typing(false);
    messages.pop();
    botMessage(t('connectionError'), [], 'msg-error');
    setChips([content]);
  } finally {
    if (askedIn === conversationId) { pendingChat = null; setBusy(false); }
  }
}

function newChat() {
  if (pendingChat) { try { pendingChat.abort(); } catch (error) { /* nothing to cancel */ } pendingChat = null; }
  typing(false);
  setBusy(false);
  messages = [];
  conversationId = newId();
  chosenInChat = {};
  contextItems = []; droppedContext = []; audienceCleared = false;
  renderContext();
  selectedBook = null;
  $('chatLog').replaceChildren();
  showSection('chat');
  botMessage(t('greeting'));
  // «رشّحي لي تفضيلات قرّاء مكتبة ثلاث» once enough books have readers' reviews (2026-10-27)
  setChips(favoritesReady ? t('startChips').concat([t('favoritesChip')]) : t('startChips'));
  $('chatInput').focus();
}

function showBook(book) {
  // a book opened from «ماذا تقرأ الآن؟» is one step past it in the history (AGY-20261027-01); from anywhere else the usual levels
  if (currentSection === 'reading') { LEVEL.bookDetail = LEVEL.reading + 1; LEVEL.feedback = LEVEL.bookDetail + 1; }
  else if (currentSection !== 'bookDetail' && currentSection !== 'feedback') { LEVEL.bookDetail = 2; LEVEL.feedback = 3; }
  selectedBook = book;
  $('dTitle').textContent = book.title;
  closeMore();
  // the author and the publisher open the other books we have by them (2026-10-08)
  const author = String(book.author || '').trim();
  $('dAuthor').replaceChildren();
  if (author && !/^(غير معروف|مجهول|unknown)$/i.test(author)) {
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'link-button'; btn.textContent = author;
    btn.setAttribute('aria-label', t('authorAria')(author));
    btn.addEventListener('click', () => openMore('author', author));
    $('dAuthor').appendChild(btn);
  } else $('dAuthor').textContent = author;
  const publisher = String(book.publisher || '').trim();
  $('dPublisher').replaceChildren();
  $('dPublisher').hidden = !publisher;
  if (publisher) {
    const label = document.createElement('span');
    label.textContent = t('publisherLabel') + ': ';
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'link-button'; btn.textContent = publisher;
    btn.setAttribute('aria-label', t('publisherAria')(publisher));
    btn.addEventListener('click', () => openMore('publisher', publisher));
    $('dPublisher').append(label, btn);
  }
  $('dInitial').textContent = [...String(book.title || (currentLanguage === 'ar' ? 'ك' : 'B')).trim()][0] || 'B';
  $('dGenre').textContent = currentLanguage === 'en' ? (GENRES_EN[book.genre] || book.genre || t('book')) : (book.genre || t('book'));
  $('dTraits').textContent = t('closeChoice');
  // a book opened from «اختياراتنا هذا الشهر» was not matched to anything the visitor described (2026-10-14)
  $('dTraits').parentElement.hidden = !!book.fromPicks;
  $('dPrice').textContent = book.price ? t('currency')(formatNumber(book.price)) : '';
  $('dStock').parentElement.classList.toggle('no-price', !book.price);
  $('dStock').textContent = stockLabel(book.quantity, true);
  $('dShelf').textContent = locationText(book) || t('shelfUnknown');
  $('findLabel').textContent = locationText(book) ? t('findAt') : t('whereLabel');
  $('dPages').textContent = book.page_count ? t('pages')(formatNumber(book.page_count)) : '';
  $('dOrigin').textContent = book.origin || '';
  $('dOriginBox').hidden = !book.origin;
  // the year of publication of this edition, left out when unknown (Ibrahim, 2026-10-09)
  const year = /^\d{4}/.test(String(book.publication_year || '')) ? Number(String(book.publication_year).slice(0, 4)) : null;
  $('dYear').textContent = year ? (currentLanguage === 'ar' ? String(year).replace(/\d/g, d => '٠١٢٣٤٥٦٧٨٩'[d]) : String(year)) : '';
  $('dYearBox').hidden = !year;
  const img = $('dCoverImg');
  img.hidden = !book.cover_url;
  $('detailCover').classList.toggle('has-cover', !!book.cover_url);
  if (book.cover_url) img.src = book.cover_url; else img.removeAttribute('src');
  $('dSummary').textContent = book.summary || '';
  $('dSummary').hidden = !book.summary;
  $('dReason').textContent = book.reason || '';
  $('dReason').hidden = !book.reason;
  loadBookReviews(book);
  showSection('bookDetail');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderContext() {
  $('contextTitle').textContent = t('contextTitle');
  const box = $('contextChips');
  box.replaceChildren();
  contextItems.forEach(item => {
    const chip = document.createElement('span');
    chip.className = 'context-chip';
    const text = document.createElement('span');
    text.textContent = item.label;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'context-clear';
    remove.textContent = '✕';
    remove.setAttribute('aria-label', t('contextRemove')(item.label));
    remove.dataset.key = item.key;   // removed through the click handler on #contextChips below
    chip.append(text, remove);
    box.appendChild(chip);
  });
  $('contextBar').hidden = contextItems.length === 0;
}
function removeContext(item) {
  if (busy) return;
  if (!droppedContext.includes(item.key)) droppedContext.push(item.key);
  if (item.key === 'audience') audienceCleared = true;
  contextItems = contextItems.filter(x => x.key !== item.key);
  renderContext();
  showToast(t('contextRemoved')(item.label));
  setChips([t('showNow')]);
}
// Which pill a ✕ tap removes is decided by where the finger really was, read from the current layout,
// not by the element the browser's touch adjustment delivered the click to: in Chromium a tap 5px inside
// one ✕ near the next row reached the ✕ above it and removed the wrong pill (daily audit 2026-10-12 #2).
// A tap whose point is on no ✕ target (a pill's words, the gap between rows) removes nothing.
// Keyboard and screen-reader clicks without a pointer (detail 0) use their own button.
let contextDown = null;
document.addEventListener('pointerdown', event => {
  if ($('contextBar').hidden) return;
  const under = document.elementFromPoint(event.clientX, event.clientY);
  const button = under && under.closest('.context-clear');
  contextDown = { key: button && $('contextChips').contains(button) ? button.dataset.key : null, at: event.timeStamp };
}, true);
$('contextChips').addEventListener('click', event => {
  const button = event.target.closest('.context-clear');
  if (!button) return;
  let key = button.dataset.key;
  if (event.detail !== 0 && contextDown && event.timeStamp - contextDown.at < 2000) key = contextDown.key;
  contextDown = null;
  const item = contextItems.find(x => x.key === key);
  if (item) removeContext(item);
});

// ---------------- other books by the author / from the publisher, in a sheet over the book page
let moreOpen = false;
let moreSeq = 0;
let moreOpener = null;
function closeMore() {
  if (!moreOpen) return;
  moreOpen = false;
  moreSeq += 1;
  $('moreSheet').hidden = true; $('moreBackdrop').hidden = true;
  syncModalBackground();  // the page is usable again before focus goes back to it
  if (moreOpener && document.body.contains(moreOpener)) moreOpener.focus();
}
async function openMore(kind, name) {
  if (!selectedBook) return;
  moreOpener = document.activeElement;
  moreOpen = true;
  const seq = ++moreSeq;
  $('moreTitle').textContent = kind === 'author' ? t('byAuthor')(name) : t('byPublisher')(name);
  $('moreLead').textContent = t('moreLoading');
  $('moreList').replaceChildren();
  $('moreClose').textContent = t('moreClose');
  $('moreSheet').hidden = false; $('moreBackdrop').hidden = false;
  syncModalBackground();
  $('moreClose').focus();
  try {
    const response = await fetch(`${API}/more?by=${kind}&book_id=${encodeURIComponent(selectedBook.book_id)}&lang=${currentLanguage}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (seq !== moreSeq) return;
    const books = Array.isArray(data.books) ? data.books : [];
    $('moreLead').textContent = books.length ? t('moreCount')(formatNumber(data.total || books.length)) : t('moreNone');
    books.forEach((book, i) => {
      const line = String(book.hook || book.summary || '').split(/(?<=[.!؟?])\s/)[0].slice(0, 140);
      $('moreList').appendChild(bookCard(Object.assign({}, book, { reason: line }), i));
    });
  } catch (error) {
    if (seq === moreSeq) $('moreLead').textContent = t('moreFailed');
  }
}
$('moreClose').addEventListener('click', closeMore);
$('moreBackdrop').addEventListener('click', closeMore);
$('moreSheet').addEventListener('modal-close', closeMore);

// ---------------- every sheet that says aria-modal="true" behaves as one (audit 2026-10-08):
// the page behind it is inert, Tab and Shift+Tab stay inside it, Escape closes it. A new sheet only
// needs role="dialog" aria-modal="true" in index.html and a 'modal-close' listener.
const MODAL_SELECTOR = '[role="dialog"][aria-modal="true"]';
const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';
function openModalSheet() {
  const open = [...document.querySelectorAll(MODAL_SELECTOR)].filter(el => !el.hidden);
  return open[open.length - 1] || null;
}
function modalItems(sheet) {
  return [...sheet.querySelectorAll(FOCUSABLE)].filter(el => !el.disabled && !el.closest('[hidden]'));
}
function syncModalBackground() {
  const sheet = openModalSheet();
  const shell = document.querySelector('.app-shell');
  if (!shell) return;
  [...shell.children].forEach(el => {
    // the backdrop takes the tap that closes the sheet and the toast only announces: both stay live
    if (el.classList.contains('note-backdrop') || el.id === 'toast') return;
    el.inert = !!sheet && el !== sheet;
  });
}
document.querySelectorAll(MODAL_SELECTOR).forEach(sheet =>
  new MutationObserver(syncModalBackground).observe(sheet, { attributes: true, attributeFilter: ['hidden'] }));
document.addEventListener('keydown', event => {
  const sheet = openModalSheet();
  if (!sheet) return;
  if (event.key === 'Escape') { event.preventDefault(); sheet.dispatchEvent(new CustomEvent('modal-close')); return; }
  if (event.key !== 'Tab') return;
  const items = modalItems(sheet);
  if (!items.length) { event.preventDefault(); return; }
  const first = items[0], last = items[items.length - 1];
  if (!sheet.contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
  else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});

// ---------------- «اختياراتنا هذا الشهر» (Ibrahim, 2026-10-14): five books staff approved in the admin, on the
// start page under the welcome. Each shows its cover, title, author and a short line; tapping opens the book page.
let picksSeq = 0;
async function loadPicks() {
  const seq = ++picksSeq;
  try {
    const response = await fetch(`${API}/picks?lang=${currentLanguage}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (seq !== picksSeq) return;
    renderPicks(Array.isArray(data.books) ? data.books : []);
  } catch (error) { if (seq === picksSeq) renderPicks([]); }
}
function renderPicks(list) {
  const row = $('picksRow');
  row.replaceChildren();
  list.forEach(book => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'pick-card';
    card.setAttribute('aria-label', t('picksAria')(book.title, book.author));
    const initial = [...String(book.title || 'ك').trim()][0] || 'ك';
    const cover = book.cover_url
      ? `<img src="${escapeHtml(book.cover_url)}" alt="" loading="lazy" />`
      : `<span class="pick-cover-mark" aria-hidden="true">${escapeHtml(initial)}</span>`;
    card.innerHTML = `<span class="pick-cover">${cover}</span><span class="pick-text"><strong dir="auto">${escapeHtml(book.title)}</strong><span class="pick-author" dir="auto">${escapeHtml(book.author || '')}</span>${book.line ? `<span class="pick-line" dir="auto">${escapeHtml(book.line)}</span>` : ''}</span>`;
    card.addEventListener('click', () => showBook(Object.assign({}, book, { reason: book.line || '', fromPicks: true })));
    row.appendChild(card);
  });
  $('picksBox').hidden = list.length === 0;
}

// ---------------- «فعالياتنا» (2026-10-08): upcoming library events on the start page; staff add
// them in the admin. Tapping one opens its page; an event about a book links to the book.
let selectedEvent = null;
let eventsSeq = 0;
function eventDay(day) {
  const today = new Date(); today.setHours(12, 0, 0, 0);
  const d = new Date(day + 'T12:00:00');
  const diff = Math.round((d - today) / 86400000);
  if (diff === 0) return t('eventToday');
  if (diff === 1) return t('eventTomorrow');
  try {
    return new Intl.DateTimeFormat(currentLanguage === 'ar' ? 'ar-SA-u-ca-gregory' : 'en-GB', { weekday: 'long', day: 'numeric', month: 'long' }).format(d);
  } catch (error) { return day; }
}
function eventClock(time) {
  if (!time) return '';
  const [h, m] = time.split(':').map(Number);
  const d = new Date(2000, 0, 1, h, m);
  try { return new Intl.DateTimeFormat(currentLanguage === 'ar' ? 'ar-SA' : 'en-GB', { hour: 'numeric', minute: '2-digit', hour12: true }).format(d); }
  catch (error) { return time; }
}
function eventWhen(e) {
  // plain words instead of «·» between the date and the times: the mixed-direction separator
  // made them run into each other on phones («أكتوبر٧:٣٠»)
  const ar = currentLanguage === 'ar';
  let text = eventDay(e.event_date);
  if (e.event_time) {
    text += (ar ? '، الساعة ' : ', ') + eventClock(e.event_time);
    if (e.end_time) text += (ar ? ' إلى ' : ' to ') + eventClock(e.end_time);
  }
  return text;
}
async function loadEvents() {
  const seq = ++eventsSeq;
  try {
    const response = await fetch(`${API}/events?lang=${currentLanguage}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (seq !== eventsSeq) return;
    renderEvents(Array.isArray(data.events) ? data.events : []);
  } catch (error) { if (seq === eventsSeq) renderEvents([]); }
}
function renderEvents(list) {
  const row = $('eventsRow');
  row.replaceChildren();
  list.forEach(e => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'event-card';
    card.setAttribute('aria-label', t('eventsAria')(e.title));
    card.innerHTML = `${e.image_url ? `<img src="${escapeHtml(e.image_url)}" alt="" loading="lazy" />` : '<span class="event-card-mark" aria-hidden="true"></span>'}<span class="event-card-text"><strong>${escapeHtml(e.title)}</strong><span>${escapeHtml(eventWhen(e))}</span></span>`;
    card.addEventListener('click', () => showEvent(e));
    row.appendChild(card);
  });
  $('eventsBox').hidden = list.length === 0;
}
// «أضف إلى التقويم» (Ibrahim, 2026-10-09): the phone's own calendar opens the .ics file; Google Calendar
// gets a link of its own (Android phones do not always open a downloaded .ics). Times are Riyadh (UTC+3).
function calendarUtc(date, time) {
  const [y, mo, d] = date.split('-').map(Number);
  const [h, mi] = time.split(':').map(Number);
  return new Date(Date.UTC(y, mo - 1, d, h - 3, mi));
}
function setCalendarLinks(e) {
  $('eCalendarIcs').href = `${API}/events/${e.event_id}/calendar.ics`;
  $('eCalendarIcs').setAttribute('download', `thalath-event-${e.event_id}.ics`);
  const stamp = x => x.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  let dates;
  if (e.event_time) {
    const start = calendarUtc(e.event_date, e.event_time);
    let end = e.end_time ? calendarUtc(e.event_date, e.end_time) : new Date(start.getTime() + 3600000);
    if (end <= start) end = new Date(end.getTime() + 86400000);
    dates = `${stamp(start)}/${stamp(end)}`;
  } else {
    const day = e.event_date.replace(/-/g, '');
    const next = new Date(Date.UTC(...e.event_date.split('-').map((v, i) => i === 1 ? v - 1 : +v)) + 86400000);
    dates = `${day}/${next.toISOString().slice(0, 10).replace(/-/g, '')}`;
  }
  const q = new URLSearchParams({ action: 'TEMPLATE', text: e.title, dates, location: t('calendarPlace'), details: e.description || '' });
  $('eCalendarGoogle').href = `https://calendar.google.com/calendar/render?${q}`;
}
function showEvent(e) {
  selectedEvent = e;
  $('eTitle').textContent = e.title;
  $('eWhen').textContent = eventWhen(e);
  setCalendarLinks(e);
  $('eDesc').textContent = e.description || '';
  $('eDesc').hidden = !e.description;
  $('eImageBox').hidden = !e.image_url;
  if (e.image_url) $('eImage').src = e.image_url; else $('eImage').removeAttribute('src');
  const box = $('eBook');
  box.replaceChildren();
  $('eBookBox').hidden = !e.book;
  // the book's own short line, not a chat reason («اختيار قريب من وصفك» means nothing here)
  const line = String(e.book && (e.book.hook || e.book.summary) || '').split(/(?<=[.!؟?])\s/)[0].slice(0, 140);
  if (e.book) box.appendChild(bookCard(Object.assign({}, e.book, { reason: line }), 0));
  showSection('eventDetail');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function backToChat() { showSection('chat'); scrollToEnd(); }
// Back buttons and the phone's own back gesture walk the same path: feedback → book → chat → start page.
function goBack() { if (noteMode) closeNote(); closeMore(); history.back(); }
// the «منار» logo at the top of chat and detail pages goes back to the start page; the conversation is kept (like «رجوع»)
function goHome() { if (noteMode) closeNote(); closeMore(); showSection('welcome'); }
function updateFeedbackLocation() {
  if (!selectedBook) return;
  const place = locationText(selectedBook);
  $('selectedLocation').textContent = place
    ? t('selectedAt')(selectedBook.title, place)
    : t('selectedHelp')(selectedBook.title);
}
async function chooseBook() {
  // Records the choice for the library's statistics. It never changes the stock count.
  if (!selectedBook) return;
  const id = selectedBook.book_id;
  const known = chosenInChat[id] || null;
  choiceId = known ? known.id : null;
  choiceToken = known ? known.token : null;
  if (!choiceId) {
    try {
      const response = await fetch(`${API}/choice`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ book_id: id, match: selectedBook.match || null, language: currentLanguage, conversation_id: conversationId })
      });
      if (response.ok) {
        const saved = await response.json();
        choiceId = saved.choice_id; choiceToken = saved.choice_token || null;
        chosenInChat[id] = { id: choiceId, token: choiceToken };
      }
    } catch (error) { choiceId = null; }
    // not recorded (refused or no connection): say so and stay on the book, so the visitor can try again
    if (!choiceId) { showToast(t('choiceFailed')); return; }
  }
  showFeedback();
}

function showFeedback() {
  if (!selectedBook) return;
  updateFeedbackLocation();
  showSection('feedback');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function sendFeedback(rating) {
  const buttons = document.querySelectorAll('.feedback-button');
  buttons.forEach(button => { button.disabled = true; });
  try {
    const response = await fetch(`${API}/feedback`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating, choice_id: choiceId, choice_token: choiceToken, conversation_id: conversationId })
    });
    if (!response.ok && response.status !== 409) throw new Error(`HTTP ${response.status}`);
    showToast(t('feedbackSaved'));
  } catch (error) {
    showToast(t('feedbackFailed'));
  } finally {
    setTimeout(() => {
      buttons.forEach(button => { button.disabled = false; });
      const title = selectedBook ? selectedBook.title : t('book');
      showSection('chat');
      botMessage(t('enjoy')(title));
      setChips(t('afterFeedback'));
    }, 1100);
  }
}

// One bottom sheet for both: reporting a mistake in a book's information, and sending a suggestion.
// Anonymous: only the chosen reason and the typed note are sent (no conversation, no visitor id).
function openNote(mode, opener) {
  if (mode === 'report' && !selectedBook) return;
  noteMode = mode;
  noteSeq += 1;
  noteReason = null;
  noteOpener = opener || null;
  const report = mode === 'report';
  $('noteTitle').textContent = t(report ? 'reportTitle' : 'suggestTitle');
  $('noteLead').textContent = report ? t('reportLead')(selectedBook.title) : t('suggestLead');
  $('noteLabel').textContent = t(report ? 'reportNoteLabel' : 'suggestNoteLabel');
  $('noteText').placeholder = t(report ? 'reportPlaceholder' : 'suggestPlaceholder');
  $('noteText').value = '';
  $('noteError').textContent = '';
  $('noteSend').textContent = t('noteSend');
  $('noteCancel').textContent = t('noteCancel');
  const box = $('noteReasons');
  box.replaceChildren();
  box.hidden = !report;
  box.setAttribute('aria-label', t('reportReasonsLabel'));
  if (report) t('reportReasons').forEach(([value, label]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'note-reason';
    button.textContent = label;
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      noteReason = value;
      box.querySelectorAll('.note-reason').forEach(other => other.setAttribute('aria-pressed', String(other === button)));
      $('noteError').textContent = '';
    });
    box.appendChild(button);
  });
  $('noteBackdrop').hidden = false;
  $('noteSheet').hidden = false;
  syncModalBackground();
  (report ? box.querySelector('.note-reason') : $('noteText')).focus();
}

function closeNote() {
  if (!noteMode) return;
  noteMode = null;
  $('noteBackdrop').hidden = true;
  $('noteSheet').hidden = true;
  syncModalBackground();
  if (noteOpener && !noteOpener.closest('[hidden]')) noteOpener.focus();
  noteOpener = null;
}

async function submitNote() {
  if (!noteMode || noteSending) return;
  const sentFrom = noteSeq;   // the sheet this send belongs to
  const report = noteMode === 'report';
  const note = $('noteText').value.trim();
  if (report && !noteReason) { $('noteError').textContent = t('needReason'); return; }
  if (report && noteReason === 'other' && !note) { $('noteError').textContent = t('needNote'); $('noteText').focus(); return; }
  if (!report && note.length < 3) { $('noteError').textContent = t('needSuggestion'); $('noteText').focus(); return; }
  noteSending = true;
  $('noteSend').disabled = true;
  $('noteSend').textContent = t('noteSending');
  $('noteError').textContent = '';
  try {
    const response = await fetch(`${API}/${report ? 'report' : 'suggestion'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(report ? { book_id: selectedBook.book_id, reason: noteReason, note, language: currentLanguage }
        : { note, language: currentLanguage })
    });
    if (!response.ok) throw new Error(response.status === 429 ? 'busy' : `HTTP ${response.status}`);
    // only the sheet that sent it is closed: a sheet opened again meanwhile keeps its new draft
    if (sentFrom === noteSeq) closeNote();
    showToast(t(report ? 'reportSent' : 'suggestSent'));
  } catch (error) {
    // The sheet stays open with the visitor's text, so nothing typed is lost.
    if (sentFrom === noteSeq) $('noteError').textContent = t(error.message === 'busy' ? 'noteBusy' : 'noteFailed');
    else showToast(t('noteFailed'));
  } finally {
    noteSending = false;
    $('noteSend').disabled = false;
    if (noteMode) $('noteSend').textContent = t('noteSend');
  }
}


// ---------------- «ماذا تقرأ الآن؟» (Ibrahim 2026-10-27, from a visitor's proposal): the visitor finds a book of the
// library (in stock or not) and rates it — stars, quick traits, optional words and first name — shown to visitors after
// staff approve it. A book we do not have is suggested for the shelves: one we hold is shown to the visitor, one we do
// not hold is looked up outside and the visitor is asked whether it is the right book before it is kept as a candidate.
let favoritesReady = false;
let reviewBook = null;
let reviewStars = 0;
let reviewTags = [];
let reviewSending = false;
let readingFromBook = false;
// Ibrahim 2026-10-29: the shelf suggestion opened straight from the start page — its «رجوع» goes back to the start page
let shelfFromStart = false;
let searchTimer = null;
let searchSeq = 0;
let shelfSeq = 0;
async function loadReviewStatus() {
  try {
    const response = await fetch(`${API}/reviews/status`);
    if (response.ok) favoritesReady = !!(await response.json()).favorites_ready;
  } catch (error) { favoritesReady = false; }
}
function applyReadingLanguage() {
  $('readingLabel').textContent = t('reading');
  $('shelfStartLabel').textContent = t('shelfStart');
  $('rTitle').textContent = t('reading');
  if (!$('sForm').hidden) $('rTitle').textContent = t('shelfStart');
  $('rLead').textContent = t('readingLead');
  $('rSearchLabel').textContent = t('searchLabel');
  $('rSearch').placeholder = t('searchPlaceholder');
  $('rShelfLead').textContent = t('shelfLead');
  $('rShelfLabel').textContent = t('shelfBtn');
  $('rStarsLabel').textContent = t('starsLabel');
  $('rTagsLabel').textContent = t('tagsLabel');
  $('rTextLabel').textContent = t('reviewTextLabel');
  $('rText').placeholder = t('reviewTextPlaceholder');
  $('rNameLabel').textContent = t('nameLabel');
  $('rNote').textContent = t('reviewNote');
  $('rSend').textContent = t('sendReview');
  $('rChange').textContent = t('otherBook');
  $('sLead').textContent = t('shelfFormLead');
  $('sBookLabel').textContent = t('bookLabel');
  $('sAuthorLabel').textContent = t('authorLabel');
  $('sCheck').textContent = t('check');
  $('sBack').textContent = t('back');
  $('rAgain').textContent = t('again');
  $('rHome').textContent = t('homeBtn');
  $('reviewBtn').textContent = t('reviewBtn');
  $('dReviewsTitle').textContent = t('reviewsTitle');
  if (reviewBook && !$('rForm').hidden) { renderReviewBook(); renderStars(); renderTags(); }
  if (!$('rFind').hidden && $('rSearch').value.trim()) searchReview($('rSearch').value);
}
function readingStep(step) {
  $('rTitle').textContent = t(step === 'shelf' ? 'shelfStart' : 'reading');
  $('rFind').hidden = step !== 'find';
  $('rForm').hidden = step !== 'form';
  $('sForm').hidden = step !== 'shelf';
  $('rDone').hidden = step !== 'done';
}
function openReading(book = null) {
  readingFromBook = !!book;
  shelfFromStart = false;
  // one step forward in the browser history from wherever it is opened, so Back returns there — to the book page when
  // opened from «قرأته؟ شاركنا رأيك» (AGY-20261027-01: the fixed level 1 went back one step from the book page)
  if (currentSection !== 'reading') LEVEL.reading = LEVEL[currentSection] + 1;
  $('rSearch').value = '';
  $('rResults').replaceChildren();
  $('rFindState').textContent = '';
  showSection('reading');
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (book) selectReviewBook(book);
  else { readingStep('find'); $('rSearch').focus(); }
}
function readingBack() {
  // inside the page (a review form or the shelf suggestion opened from the search) «رجوع» returns to the search;
  // otherwise it is the browser's Back: to the book page it was opened from, or to the start page (AGY-20261027-01)
  if (!readingFromBook && !shelfFromStart && (!$('rForm').hidden || !$('sForm').hidden)) { readingStep('find'); return; }
  goBack();
}
function miniBook(book, note) {
  const box = document.createElement('div');
  box.className = 'r-mini';
  const initial = [...String(book.title || 'ك').trim()][0] || 'ك';
  box.innerHTML = `<span class="r-mini-cover">${book.cover_url ? `<img src="${escapeHtml(book.cover_url)}" alt="" loading="lazy" />` : `<span aria-hidden="true">${escapeHtml(initial)}</span>`}</span>`
    + `<span class="r-mini-text"><strong dir="auto">${escapeHtml(book.title)}</strong><span dir="auto">${escapeHtml(book.author || '')}</span>${note ? `<em>${escapeHtml(note)}</em>` : ''}</span>`;
  return box;
}
async function searchReview(text) {
  const q = String(text || '').trim();
  const seq = ++searchSeq;
  if (q.length < 2) { $('rResults').replaceChildren(); $('rFindState').textContent = ''; return; }
  $('rFindState').textContent = t('searching');
  try {
    const response = await fetch(`${API}/reviews/search?q=${encodeURIComponent(q)}&lang=${currentLanguage}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (seq !== searchSeq) return;
    const books = Array.isArray(data.books) ? data.books : [];
    $('rResults').replaceChildren();
    $('rFindState').textContent = books.length ? '' : t('noResults');
    books.forEach(book => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'r-result';
      button.appendChild(miniBook(book, book.available ? t('available') : t('notInStock')));
      button.addEventListener('click', () => selectReviewBook(book));
      $('rResults').appendChild(button);
    });
  } catch (error) { if (seq === searchSeq) $('rFindState').textContent = t('readingFailed'); }
}
function renderReviewBook() {
  $('rBook').replaceChildren(miniBook(reviewBook, reviewBook.available ? t('available') : t('notInStock')));
}
function renderStars() {
  const box = $('rStars');
  box.replaceChildren();
  for (let n = 1; n <= 5; n += 1) {
    const star = document.createElement('button');
    star.type = 'button';
    star.className = 'star';
    star.setAttribute('role', 'radio');
    star.setAttribute('aria-checked', String(n === reviewStars));
    star.setAttribute('aria-label', t('starAria')(formatNumber(n)));
    star.tabIndex = n === (reviewStars || 1) ? 0 : -1;
    star.classList.toggle('on', n <= reviewStars);
    star.textContent = '★';
    star.addEventListener('click', () => { reviewStars = n; $('rError').textContent = ''; renderStars(); box.children[n - 1].focus(); });
    star.addEventListener('keydown', event => {
      const step = { ArrowRight: currentLanguage === 'ar' ? -1 : 1, ArrowLeft: currentLanguage === 'ar' ? 1 : -1, ArrowUp: 1, ArrowDown: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      reviewStars = Math.min(5, Math.max(1, (reviewStars || n) + step));
      renderStars(); box.children[reviewStars - 1].focus();
    });
    box.appendChild(star);
  }
}
function renderTags() {
  const box = $('rTags');
  box.replaceChildren();
  t('reviewTags').forEach(([key, label]) => {
    const tag = document.createElement('button');
    tag.type = 'button';
    tag.className = 'note-reason';
    tag.textContent = label;
    tag.setAttribute('aria-pressed', String(reviewTags.includes(key)));
    tag.addEventListener('click', () => {
      reviewTags = reviewTags.includes(key) ? reviewTags.filter(k => k !== key) : reviewTags.concat([key]);
      tag.setAttribute('aria-pressed', String(reviewTags.includes(key)));
    });
    box.appendChild(tag);
  });
}
function selectReviewBook(book) {
  reviewBook = book;
  reviewStars = 0;
  reviewTags = [];
  $('rText').value = '';
  $('rName').value = '';
  $('rError').textContent = '';
  renderReviewBook(); renderStars(); renderTags();
  readingStep('form');
  $('rStars').firstElementChild.focus();
}
async function submitReview() {
  if (!reviewBook || reviewSending) return;
  if (!reviewStars) { $('rError').textContent = t('needStars'); $('rStars').firstElementChild.focus(); return; }
  reviewSending = true;
  $('rSend').disabled = true;
  $('rSend').textContent = t('noteSending');
  $('rError').textContent = '';
  try {
    const response = await fetch(`${API}/reviews`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ book_id: reviewBook.book_id, stars: reviewStars, tags: reviewTags, text: $('rText').value.trim(),
        first_name: $('rName').value.trim(), language: currentLanguage })
    });
    if (!response.ok) throw new Error(response.status === 429 ? 'busy' : `HTTP ${response.status}`);
    showReadingDone(t('reviewSent')(reviewBook.title));
  } catch (error) {
    $('rError').textContent = t(error.message === 'busy' ? 'noteBusy' : 'readingFailed');
  } finally {
    reviewSending = false;
    $('rSend').disabled = false;
    $('rSend').textContent = t('sendReview');
  }
}
function showReadingDone(text) {
  $('rDoneText').textContent = text;
  readingStep('done');
  $('rAgain').focus();
}
function openShelf(prefill) {
  $('sBook').value = String(prefill || '').trim();
  $('sAuthor').value = '';
  $('sError').textContent = '';
  $('sResult').replaceChildren();
  readingStep('shelf');
  $('sBook').focus();
}
function shelfButton(label, primary, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = primary ? 'want-button' : 'note-cancel';
  button.textContent = label;
  button.addEventListener('click', onClick);
  return button;
}
async function shelfSend(body, doneText, buttons) {
  buttons.forEach(b => { b.disabled = true; });
  try {
    const response = await fetch(`${API}/${body.note ? 'suggestion' : 'shelf/suggest'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(response.status === 429 ? 'busy' : `HTTP ${response.status}`);
    showReadingDone(doneText);
  } catch (error) {
    buttons.forEach(b => { b.disabled = false; });
    $('sError').textContent = t(error.message === 'busy' ? 'noteBusy' : 'readingFailed');
  }
}
async function checkShelf() {
  const title = $('sBook').value.trim(), author = $('sAuthor').value.trim();
  if (title.length < 2) { $('sError').textContent = t('needTitle'); $('sBook').focus(); return; }
  const seq = ++shelfSeq;
  const out = $('sResult');
  $('sError').textContent = '';
  $('sCheck').disabled = true;
  out.replaceChildren();
  const wait = document.createElement('p'); wait.className = 'note-lead'; wait.textContent = t('lookingUp'); out.appendChild(wait);
  const asTyped = { note: t('suggestionNote')(title, author), language: currentLanguage };
  try {
    const response = await fetch(`${API}/shelf/check`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title, author, language: currentLanguage })
    });
    if (!response.ok) throw new Error(response.status === 429 ? 'busy' : `HTTP ${response.status}`);
    const data = await response.json();
    if (seq !== shelfSeq) return;
    out.replaceChildren();
    const say = text => { const p = document.createElement('p'); p.className = 's-say'; p.textContent = text; out.appendChild(p); };
    if (data.result === 'available') {
      // a book we have in stock: the visitor is told so and sees its card (Ibrahim 2026-10-27)
      say(t('isHere'));
      const list = document.createElement('div'); list.className = 'more-list';
      (data.books || []).forEach((book, i) => list.appendChild(bookCard(Object.assign({}, book, { reason: String(book.hook || book.summary || '').split(/(?<=[.!؟?])\s/)[0].slice(0, 140) }), i)));
      out.appendChild(list);
    } else if (data.result === 'out_of_stock') {
      say(t('notHereNow'));
      out.appendChild(miniBook(data.book, t('notInStock')));
      const actions = document.createElement('div'); actions.className = 'note-actions';
      const want = shelfButton(t('wantBack'), true, () => shelfSend({ book_id: data.book.book_id, asked_as: title }, t('wantSaved')(data.book.title), [want]));
      actions.appendChild(want); out.appendChild(actions);
    } else if (data.result === 'found_outside' && data.candidate) {
      // looked up outside: the visitor confirms it is the book before it is kept (Ibrahim 2026-10-27)
      const c = data.candidate;
      say(t('foundAsk'));
      const card = document.createElement('div'); card.className = 's-candidate';
      card.innerHTML = `<strong dir="auto">${escapeHtml(c.title || title)}</strong>`
        + (c.author ? `<span dir="auto">${escapeHtml(t('byLabel')(c.author))}${c.year ? ` · ${escapeHtml(String(c.year))}` : ''}</span>` : (c.year ? `<span>${escapeHtml(String(c.year))}</span>` : ''))
        + (c.summary ? `<p dir="auto">${escapeHtml(c.summary)}</p>` : '')
        + (c.sources && c.sources.length ? `<em>${escapeHtml(t('sourceLabel')(c.sources.join('، ')))}</em>` : '');
      out.appendChild(card);
      const actions = document.createElement('div'); actions.className = 'note-actions';
      const yes = shelfButton(t('yesThis'), true, () => shelfSend({ key: c.key, asked_as: title }, t('candidateSaved')(c.title || title), [yes, no]));
      const no = shelfButton(t('notThis'), false, () => shelfSend(asTyped, t('suggestionSaved'), [yes, no]));
      actions.append(yes, no); out.appendChild(actions);
    } else {
      say(t('notFound'));
      const actions = document.createElement('div'); actions.className = 'note-actions';
      const save = shelfButton(t('saveAsTyped'), true, () => shelfSend(asTyped, t('suggestionSaved'), [save]));
      actions.appendChild(save); out.appendChild(actions);
    }
  } catch (error) {
    if (seq !== shelfSeq) return;
    out.replaceChildren();
    $('sError').textContent = t(error.message === 'busy' ? 'noteBusy' : 'readingFailed');
  } finally {
    if (seq === shelfSeq) $('sCheck').disabled = false;
  }
}
// the book page: what Thalath readers said (approved reviews of every edition of the work)
let bookReviewsSeq = 0;
async function loadBookReviews(book) {
  const seq = ++bookReviewsSeq;
  $('dReviews').hidden = true;
  try {
    const response = await fetch(`${API}/reviews/book/${encodeURIComponent(book.book_id)}?lang=${currentLanguage}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (seq !== bookReviewsSeq || !data.count) return;
    $('dReviewsSum').textContent = '★ ' + t('reviewsSum')(data.avg, data.count);
    const tags = $('dReviewsTags'); tags.replaceChildren();
    (data.tags || []).slice(0, 5).forEach(tag => {
      const chip = document.createElement('span'); chip.className = 'meta-chip'; chip.textContent = `${tag.label} (${formatNumber(tag.n)})`; tags.appendChild(chip);
    });
    const list = $('dReviewsList'); list.replaceChildren();
    (data.reviews || []).forEach(r => {
      const item = document.createElement('blockquote'); item.className = 'review-item';
      item.innerHTML = `<p dir="auto">${escapeHtml(r.text)}</p><footer>${'★'.repeat(r.stars)}<span dir="auto"> — ${escapeHtml(r.name || t('anonymousReader'))}</span></footer>`;
      list.appendChild(item);
    });
    $('dReviews').hidden = false;
  } catch (error) { /* no reviews shown */ }
}

// Each screen has a level; moving forward adds a browser history step and moving back removes steps,
// so the phone's back button returns to the previous screen instead of leaving the app.
// AGY-20261029-02: going back counts the steps really pushed (navStack mirrors the app's own history entries), not the
// level difference — a jump of two levels pushes one step, so history.go(-2) used to leave «منار» altogether.
const LEVEL = { welcome: 0, chat: 1, eventDetail: 1, reading: 1, bookDetail: 2, feedback: 3 };
let skipPop = false, pushAfterPop = null;
const navStack = ['welcome'];
function showSection(id, fromHistory = false) {
  if (!fromHistory && id !== currentSection) {
    const diff = LEVEL[id] - LEVEL[currentSection];
    if (diff > 0) {
      navStack.push(id);
      history.pushState({ section: id, depth: navStack.length - 1 }, '');
    } else if (diff < 0) {
      let steps = 0;
      while (navStack.length > 1 && LEVEL[navStack[navStack.length - 1]] >= LEVEL[id]) { navStack.pop(); steps += 1; }
      if (navStack[navStack.length - 1] !== id) pushAfterPop = id;   // a screen not in the history: one step after it
      if (steps > 0) { skipPop = true; history.go(-steps); }
      else if (pushAfterPop) { navStack.push(id); history.pushState({ section: id, depth: navStack.length - 1 }, ''); pushAfterPop = null; }
    } else {
      navStack[navStack.length - 1] = id;
      history.replaceState({ section: id, depth: navStack.length - 1 }, '');
    }
  }
  currentSection = id;
  ['welcome', 'chat', 'eventDetail', 'reading', 'bookDetail', 'feedback'].forEach(section => { $(section).hidden = section !== id; });
}
function showToast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3000);
}
function formatNumber(value) {
  return new Intl.NumberFormat(currentLanguage === 'ar' ? 'ar-SA' : 'en-US', { maximumFractionDigits: 2 }).format(value);
}
function stockLabel(value, withAvailability = false) {
  const quantity = Number(value) || 0;
  let label = quantity === 1 ? t('oneCopy') : quantity === 2 ? t('twoCopies') : t('copies')(formatNumber(quantity));
  if (withAvailability) label += t('availableSuffix');
  return label;
}
function escapeHtml(value) {
  const node = document.createElement('div');
  node.textContent = String(value ?? '');
  return node.innerHTML;
}
function setTestMode(on) {
  $('testBanner').hidden = !on;
  $('trustNote').hidden = on;
}
function newId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
async function checkTestMode() {
  try {
    const response = await fetch(`${API}/test-mode`);
    setTestMode(!!(await response.json()).enabled);
  } catch (error) { /* Banner stays hidden. */ }
}
function autoGrow() {
  const field = $('chatInput');
  field.style.height = 'auto';
  field.style.height = `${Math.min(field.scrollHeight, 140)}px`;
}

$('composer').addEventListener('submit', event => { event.preventDefault(); send($('chatInput').value); });
$('chatInput').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send($('chatInput').value);
  }
});
$('chatInput').addEventListener('input', autoGrow);
// coming back to the start page keeps the conversation; «جديدة» in the chat starts a fresh one
$('startBtn').addEventListener('click', () => { if (messages.length) { showSection('chat'); scrollToEnd(); } else newChat(); });
$('chatBackBtn').addEventListener('click', goBack);
document.querySelectorAll('.brand-home').forEach(button => button.addEventListener('click', goHome));
$('eventBackBtn').addEventListener('click', goBack);
$('feedbackBackBtn').addEventListener('click', goBack);
window.addEventListener('popstate', event => {
  if (skipPop) {
    skipPop = false;
    if (pushAfterPop) { navStack.push(pushAfterPop); history.pushState({ section: pushAfterPop, depth: navStack.length - 1 }, ''); pushAfterPop = null; }
    return;
  }
  const depth = Math.max(0, (event.state && event.state.depth) || 0);
  navStack.length = Math.min(navStack.length, depth + 1);
  while (navStack.length < depth + 1) navStack.push((event.state && event.state.section) || 'welcome');
  navStack[depth] = (event.state && event.state.section) || 'welcome';
  if (noteMode) closeNote();
  closeMore();
  let section = (event.state && event.state.section) || 'welcome';
  if ((section === 'bookDetail' || section === 'feedback') && !selectedBook) section = 'chat';
  if (section === 'chat' && !messages.length && !$('chatLog').children.length) section = 'welcome';
  if (section === 'eventDetail' && !selectedEvent) section = 'welcome';
  showSection(section, true);
  if (section === 'chat') scrollToEnd();
  if (section === 'feedback') updateFeedbackLocation();
});
$('newChatBtn').addEventListener('click', newChat);
$('readingBtn').addEventListener('click', () => openReading());
$('shelfStartBtn').addEventListener('click', () => { openReading(); shelfFromStart = true; openShelf(''); });
$('readingBackBtn').addEventListener('click', readingBack);
$('reviewBtn').addEventListener('click', () => { if (selectedBook) openReading(selectedBook); });
$('rSearch').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => searchReview($('rSearch').value), 300); });
$('rSearch').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); clearTimeout(searchTimer); searchReview($('rSearch').value); } });
$('rShelfBtn').addEventListener('click', () => openShelf($('rSearch').value));
$('rForm').addEventListener('submit', event => { event.preventDefault(); submitReview(); });
$('rChange').addEventListener('click', () => { readingFromBook = false; readingStep('find'); $('rSearch').focus(); });
$('sForm').addEventListener('submit', event => { event.preventDefault(); checkShelf(); });
$('sBack').addEventListener('click', () => { if (shelfFromStart) { goBack(); return; } readingStep('find'); $('rSearch').focus(); });
$('rAgain').addEventListener('click', () => openReading());
$('rHome').addEventListener('click', goHome);
$('backBtn').addEventListener('click', goBack);
$('wantBtn').addEventListener('click', chooseBook);
['welcomeLangBtn', 'chatLangBtn', 'detailLangBtn', 'feedbackLangBtn', 'readingLangBtn'].forEach(id => $(id).addEventListener('click', switchLanguage));
$('reportBtn').addEventListener('click', () => openNote('report', $('reportBtn')));
$('suggestBtn').addEventListener('click', () => openNote('suggestion', $('suggestBtn')));
$('noteForm').addEventListener('submit', event => { event.preventDefault(); submitNote(); });
$('noteCancel').addEventListener('click', closeNote);
$('noteBackdrop').addEventListener('click', closeNote);
$('noteSheet').addEventListener('modal-close', closeNote);  // Escape and the Tab loop: see «every sheet that says aria-modal»
document.querySelectorAll('.feedback-button').forEach(button => button.addEventListener('click', () => sendFeedback(button.dataset.value)));

applyLanguage();
loadReviewStatus();
history.replaceState({ section: 'welcome', depth: 0 }, '');
showSection('welcome', true);
checkTestMode();
