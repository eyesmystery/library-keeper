// Visitor chat with the library assistant. Conversation state and language
// choice live only in this page's memory.
const API = `${location.origin}/api`;
const $ = id => document.getElementById(id);

const COPY = {
  ar: {
    title: 'ثلاث الذكي', description: 'ثلاث الذكي يساعدك تختار كتابًا متوفرًا الآن في مكتبة ثلاث.',
    switchLanguage: 'Switch to English', tagline: 'الذكي',
    hero: 'وش ودّك<br>تقرأ اليوم؟',
    welcomeBody: 'سولف معي عن مزاجك، وألقى لك كتابًا من الموجود عندنا.',
    startNote: 'تقدر تبدأ بكلمة، شعور، أو آخر كتاب عجبك.', start: 'سولف معي عن مزاجك',
    discover: 'اكتشف', ask: 'اسأل', read: 'اقرأ', follow: 'تابع مكتبة ثلاث', followOn: 'مكتبة ثلاث على', networks: { instagram: 'انستقرام', snapchat: 'سناب شات', tiktok: 'تيك توك', x: 'إكس' }, newChat: 'جديدة', assistant: 'ثلاث الذكي',
    modelFallback: 'النموذج غير متاح · نكمل بالنظام البديل',
    testBanner: 'وضع الاختبار مفعّل: هذي المحادثة تنحفظ عشان نراجعها ونحسّن المساعد.',
    conversation: 'المحادثة', suggestedReplies: 'ردود مقترحة', inputLabel: 'اكتب رسالتك لثلاث الذكي',
    placeholder: 'اكتب رسالتك هنا…', send: 'إرسال', trust: 'ما نطلب اسمك، والمحادثة تنمسح لما تبدأ من جديد.',
    back: 'رجوع', accordingReaders: 'حسب القرّاء', closeChoice: 'اختيار قريب من وصفك', language: 'اللغة',
    findAt: 'بتلقاه في', want: 'أبغى هذا الكتاب', feedbackTitle: 'اختيار جميل',
    feedbackQuestion: 'هل كانت التوصية مناسبة؟', perfect: 'مناسبة جدًا', close: 'قريبة', no: 'غير مناسبة',
    greeting: 'أهلًا فيك! أنا ثلاث الذكي، أمين المكتبة هنا. قل لي وش مزاجك اليوم، أو وش آخر كتاب عجبك، وأساعدك تلقى كتاب يناسبك من الموجود عندنا.',
    startChips: ['أبغى رواية ممتعة', 'شي خفيف مع القهوة', 'أبغى أبدأ عادة القراءة', 'هدية لشخص يحب القراءة'],
    detailsAria: (title, author) => `عرض تفاصيل كتاب ${title} للكاتب ${author}`,
    pages: n => `${n} صفحة`, unknownLength: 'الطول غير معروف', shelf: value => `الرف ${value}`,
    askStaff: 'موقعه عند الموظف', available: 'متوفر', fallbackReply: 'هذي خيارات متوفرة الآن.',
    fallbackChips: ['ورّني غيرها', 'شي أخف'], typing: 'ثلاث الذكي يكتب',
    connectionError: 'تعذر الوصول لثلاث الذكي الآن. تأكد من الاتصال وجرّب مرة ثانية.',
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
    eventsTitle: 'فعالياتنا', eventKicker: 'فعالية في مكتبة ثلاث', eventBook: 'عن كتاب', eventToday: 'اليوم', eventTomorrow: 'بكرة',
    eventsAria: title => `تفاصيل الفعالية: ${title}`, yearLabel: 'سنة النشر',
    addCalendar: 'أضف إلى التقويم', addGoogle: 'أو في تقويم Google', calendarPlace: 'مكتبة ثلاث',
    contextTitle: 'نبحث عن:', contextRemove: label => `إلغاء: ${label}`,
    contextRemoved: label => `شلنا «${label}». اكتب طلبك، أو اضغط «ورّني اقتراحات».`, showNow: 'ورّني اقتراحات',
    afterFeedback: ['اقترح لي كتاب ثاني', 'محادثة جديدة'], oneCopy: 'نسخة واحدة', twoCopies: 'نسختان',
    copies: n => `${n} نسخ`, availableSuffix: ' متوفرة', switchReset: 'غيّرنا اللغة وبدأنا محادثة جديدة.',
    reportBtn: 'فيه معلومة غلط؟ بلّغنا', reportTitle: 'وش الغلط في معلومات الكتاب؟', reportLead: title => `«${title}»`,
    reportReasons: [['title_author', 'العنوان أو المؤلف'], ['cover', 'الغلاف'], ['summary', 'النبذة'], ['details', 'التصنيف أو الصفحات أو اللغة'], ['location', 'المكان أو التوفر'], ['other', 'شي ثاني']],
    reportReasonsLabel: 'نوع الخطأ', reportNoteLabel: 'تفاصيل أكثر (اختياري)', reportPlaceholder: 'مثلًا: اسم المؤلف الصحيح…',
    reportSent: 'وصلنا بلاغك، شكرًا لك.', needReason: 'اختر وش الغلط أول.', needNote: 'اكتب لنا وش الغلط.',
    suggestBtn: 'عندك اقتراح؟', suggestTitle: 'اقتراحك يهمّنا', suggestLead: 'كتاب تتمنى نوفّره، أو شي يخلّي ثلاث الذكي أحسن. ما نطلب اسمك.',
    suggestNoteLabel: 'اقتراحك', suggestPlaceholder: 'اكتب اقتراحك هنا…', suggestSent: 'وصلنا اقتراحك، شكرًا لك.', needSuggestion: 'اكتب اقتراحك أول.',
    noteSend: 'إرسال', noteSending: 'جاري الإرسال…', noteCancel: 'إلغاء', noteFailed: 'ما قدرنا نرسلها الآن. جرّب مرة ثانية.', noteBusy: 'وصلتنا رسائل كثيرة من هنا. جرّب بعد شوي.',
  },
  en: {
    title: 'Smart Library Guide | Thalath', description: 'The smart library guide at Thalath helps you choose a book that is available now.',
    switchLanguage: 'التبديل للعربية', tagline: 'SMART LIBRARY GUIDE',
    hero: 'What would you<br>like to read today?',
    welcomeBody: "Tell me what you're in the mood for, and I'll find a book that's here now.",
    startNote: 'Start with a word, a feeling, or the last book you loved.', start: "Tell me what you're in the mood for",
    discover: 'Discover', ask: 'Ask', read: 'Read', follow: 'Follow Thalath', followOn: 'Thalath on', networks: { instagram: 'Instagram', snapchat: 'Snapchat', tiktok: 'TikTok', x: 'X' }, newChat: 'New', assistant: 'Book guide',
    modelFallback: 'Model unavailable · using fallback',
    testBanner: 'Test mode is on: this conversation is saved so we can review and improve the assistant.',
    conversation: 'Conversation', suggestedReplies: 'Suggested replies', inputLabel: 'Write a message to the book guide',
    placeholder: 'Write your message…', send: 'Send', trust: 'We do not ask for your name, and this chat clears when you start again.',
    back: 'Back', accordingReaders: 'According to readers', closeChoice: 'A close match for what you described', language: 'Language',
    findAt: 'Find it at', want: 'I want this book', feedbackTitle: 'Lovely choice',
    feedbackQuestion: 'Was this recommendation a good fit?', perfect: 'Perfect fit', close: 'Close', no: 'Not for me',
    greeting: "Welcome! I'm your smart library guide at Thalath. Tell me your mood today, or the last book you enjoyed, and I'll help you find something available here.",
    startChips: ['A fun novel', 'Something light with coffee', 'Help me start reading', 'A gift for a reader'],
    detailsAria: (title, author) => `View details for ${title} by ${author}`,
    pages: n => `${n} pages`, unknownLength: 'Length unknown', shelf: value => `Shelf ${value}`,
    askStaff: 'Ask a bookseller for its location', available: 'Available', fallbackReply: 'Here are a few books available now.',
    fallbackChips: ['Show me others', 'Something lighter'], typing: 'The book guide is typing',
    connectionError: 'The book guide is unavailable right now. Check your connection and try again.',
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
    eventsTitle: 'Our events', eventKicker: 'An event at Thalath', eventBook: 'About the book', eventToday: 'Today', eventTomorrow: 'Tomorrow',
    eventsAria: title => `Event details: ${title}`, yearLabel: 'Published',
    addCalendar: 'Add to calendar', addGoogle: 'or to Google Calendar', calendarPlace: 'Thalath Bookshop',
    contextTitle: 'Searching for:', contextRemove: label => `Remove: ${label}`,
    contextRemoved: label => `Removed “${label}”. Type your request, or tap “Show me suggestions”.`, showNow: 'Show me suggestions',
    afterFeedback: ['Suggest another book', 'New conversation'], oneCopy: '1 copy', twoCopies: '2 copies',
    copies: n => `${n} copies`, availableSuffix: ' available', switchReset: 'Language changed — we started a new conversation.',
    reportBtn: 'Something wrong? Tell us', reportTitle: 'What is wrong with this book’s details?', reportLead: title => `“${title}”`,
    reportReasons: [['title_author', 'Title or author'], ['cover', 'Cover'], ['summary', 'Summary'], ['details', 'Genre, pages or language'], ['location', 'Location or availability'], ['other', 'Something else']],
    reportReasonsLabel: 'What is wrong', reportNoteLabel: 'More details (optional)', reportPlaceholder: 'For example: the correct author name…',
    reportSent: 'We got your report. Thank you.', needReason: 'Choose what is wrong first.', needNote: 'Tell us what is wrong.',
    suggestBtn: 'Have a suggestion?', suggestTitle: 'We would love your suggestion', suggestLead: 'A book you wish we had, or something that would make the guide better. We do not ask for your name.',
    suggestNoteLabel: 'Your suggestion', suggestPlaceholder: 'Write your suggestion…', suggestSent: 'We got your suggestion. Thank you.', needSuggestion: 'Write your suggestion first.',
    noteSend: 'Send', noteSending: 'Sending…', noteCancel: 'Cancel', noteFailed: 'We could not send it right now. Please try again.', noteBusy: 'We received many messages from here. Try again in a little while.',
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
  ['backBtn', 'chatBackBtn', 'feedbackBackBtn'].forEach(id => { $(id).textContent = t('back'); });
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
  ['welcomeLangBtn', 'chatLangBtn', 'detailLangBtn', 'feedbackLangBtn'].forEach(id => {
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
  const stock = stockLabel(book.quantity);
  const shelf = locationText(book) || t('askStaff');
  card.innerHTML = `<span class="book-card-copy"><span class="book-title">${escapeHtml(book.title)}</span><span class="book-author">${escapeHtml(book.author)}</span><span class="book-reason">${escapeHtml(book.reason ?? t('closeChoice'))}</span><span class="book-meta"><span class="meta-chip stock-chip">${escapeHtml(t('available'))} · ${escapeHtml(stock)}</span>${pages ? `<span class="meta-chip">${escapeHtml(pages)}</span>` : ''}<span class="meta-chip">${escapeHtml(shelf)}</span></span></span>${book.cover_url ? `<span class="book-token has-cover" aria-hidden="true"><img src="${escapeHtml(book.cover_url)}" alt="" loading="lazy" /></span>` : `<span class="book-token" aria-hidden="true"><span>${escapeHtml(initial)}</span></span>`}`;
  card.addEventListener('click', () => showBook(book));
  return card;
}

function botMessage(text, books = [], extraClass = '') {
  const wrap = bubble('bot', text, extraClass);
  if (books.length) {
    const list = document.createElement('div');
    list.className = 'chat-books';
    books.forEach((book, index) => list.appendChild(bookCard(book, index)));
    wrap.appendChild(list);
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
    setChips(Array.isArray(data.suggestions) && data.suggestions.length ? data.suggestions : t('fallbackChips'));
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
  setChips(t('startChips'));
  $('chatInput').focus();
}

function showBook(book) {
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
    remove.addEventListener('click', () => removeContext(item));
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

// Each screen has a level; moving forward adds a browser history step and moving back removes steps,
// so the phone's back button returns to the previous screen instead of leaving the app.
const LEVEL = { welcome: 0, chat: 1, eventDetail: 1, bookDetail: 2, feedback: 3 };
let skipPop = false;
function showSection(id, fromHistory = false) {
  if (!fromHistory && id !== currentSection) {
    const diff = LEVEL[id] - LEVEL[currentSection];
    if (diff > 0) history.pushState({ section: id }, '');
    else if (diff < 0) { skipPop = true; history.go(diff); }
  }
  currentSection = id;
  ['welcome', 'chat', 'eventDetail', 'bookDetail', 'feedback'].forEach(section => { $(section).hidden = section !== id; });
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
$('eventBackBtn').addEventListener('click', goBack);
$('feedbackBackBtn').addEventListener('click', goBack);
window.addEventListener('popstate', event => {
  if (skipPop) { skipPop = false; return; }
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
$('backBtn').addEventListener('click', goBack);
$('wantBtn').addEventListener('click', chooseBook);
['welcomeLangBtn', 'chatLangBtn', 'detailLangBtn', 'feedbackLangBtn'].forEach(id => $(id).addEventListener('click', switchLanguage));
$('reportBtn').addEventListener('click', () => openNote('report', $('reportBtn')));
$('suggestBtn').addEventListener('click', () => openNote('suggestion', $('suggestBtn')));
$('noteForm').addEventListener('submit', event => { event.preventDefault(); submitNote(); });
$('noteCancel').addEventListener('click', closeNote);
$('noteBackdrop').addEventListener('click', closeNote);
$('noteSheet').addEventListener('modal-close', closeNote);  // Escape and the Tab loop: see «every sheet that says aria-modal»
document.querySelectorAll('.feedback-button').forEach(button => button.addEventListener('click', () => sendFeedback(button.dataset.value)));

applyLanguage();
history.replaceState({ section: 'welcome' }, '');
showSection('welcome', true);
checkTestMode();
