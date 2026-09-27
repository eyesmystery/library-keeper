// Visitor chat with the library assistant. Conversation state and language
// choice live only in this page's memory.
const API = `${location.origin}/api`;
const $ = id => document.getElementById(id);

const COPY = {
  ar: {
    title: 'أمين مكتبة ثلاث', description: 'أمين مكتبة ثلاث، يساعدك تختار كتابًا متوفرًا الآن.',
    switchLanguage: 'Switch to English', tagline: 'وجهتك الثقافية',
    kicker: 'أمين مكتبتك في ثلاث', hero: 'وش ودّك<br>تقرأ اليوم؟',
    welcomeBody: 'سولف معي عن مزاجك، وألقى لك كتابًا من الموجود عندنا.',
    startNote: 'تقدر تبدأ بكلمة، شعور، أو آخر كتاب عجبك.', start: 'سولف معي عن مزاجك',
    discover: 'اكتشف', ask: 'اسأل', read: 'اقرأ', newChat: 'جديدة', assistant: 'أمين المكتبة',
    testBanner: 'وضع الاختبار مفعّل: هذي المحادثة تنحفظ عشان نراجعها ونحسّن المساعد.',
    conversation: 'المحادثة', suggestedReplies: 'ردود مقترحة', inputLabel: 'اكتب رسالتك لأمين المكتبة',
    placeholder: 'اكتب رسالتك هنا…', send: 'إرسال', trust: 'ما نطلب اسمك، والمحادثة تنمسح لما تبدأ من جديد.',
    back: 'رجوع', accordingReaders: 'حسب القرّاء', closeChoice: 'اختيار قريب من وصفك', language: 'اللغة',
    findAt: 'بتلقاه في', want: 'أبغى هذا الكتاب', feedbackTitle: 'اختيار جميل',
    feedbackQuestion: 'هل كانت التوصية مناسبة؟', perfect: 'مناسبة جدًا', close: 'قريبة', no: 'غير مناسبة',
    greeting: 'أهلًا فيك! أنا أمين المكتبة. قل لي وش مزاجك اليوم، أو وش آخر كتاب عجبك، وأساعدك تلقى كتاب يناسبك من الموجود عندنا.',
    startChips: ['أبغى رواية ممتعة', 'شي خفيف مع القهوة', 'أبغى أبدأ عادة القراءة', 'هدية لشخص يحب القراءة'],
    detailsAria: (title, author) => `عرض تفاصيل كتاب ${title} للكاتب ${author}`,
    pages: n => `${n} صفحة`, unknownLength: 'الطول غير معروف', shelf: value => `الرف ${value}`,
    askStaff: 'موقعه عند الموظف', available: 'متوفر', fallbackReply: 'هذي خيارات متوفرة الآن.',
    fallbackChips: ['ورّني غيرها', 'شي أخف'], typing: 'أمين المكتبة يكتب',
    connectionError: 'تعذر الوصول لأمين المكتبة الآن. تأكد من الاتصال وجرّب مرة ثانية.',
    book: 'كتاب', shelfUnknown: 'اسأل موظف المكتبة يدلّك عليه', whereLabel: 'مكانه', unknown: 'غير معروف', currency: value => `${value} ر.س`,
    selectedAt: (title, place) => `ستجد «${title}» في ${place}. ويمكن لموظف المكتبة مساعدتك.`,
    selectedHelp: title => `اخترت «${title}». ويمكن لموظف المكتبة مساعدتك في الوصول إليه.`,
    feedbackSaved: 'شكرًا لك، تم تسجيل تقييمك.', feedbackFailed: 'ما قدرنا نحفظ تقييمك الآن، شكرًا لك على كل حال.',
    enjoy: title => `قراءة ممتعة مع «${title}»! إذا تبغى اقتراح ثاني أو تسأل عن كتاب، أنا هنا.`,
    afterFeedback: ['اقترح لي كتاب ثاني', 'محادثة جديدة'], oneCopy: 'نسخة واحدة', twoCopies: 'نسختان',
    copies: n => `${n} نسخ`, availableSuffix: ' متوفرة', switchReset: 'غيّرنا اللغة وبدأنا محادثة جديدة.',
  },
  en: {
    title: 'Thalath Book Guide', description: 'Thalath Book Guide helps you choose a book that is available now.',
    switchLanguage: 'التبديل للعربية', tagline: 'YOUR CULTURAL DESTINATION',
    kicker: 'Your book guide at Thalath', hero: 'What would you<br>like to read today?',
    welcomeBody: "Tell me what you're in the mood for, and I'll find a book that's here now.",
    startNote: 'Start with a word, a feeling, or the last book you loved.', start: "Tell me what you're in the mood for",
    discover: 'Discover', ask: 'Ask', read: 'Read', newChat: 'New', assistant: 'Book guide',
    testBanner: 'Test mode is on: this conversation is saved so we can review and improve the assistant.',
    conversation: 'Conversation', suggestedReplies: 'Suggested replies', inputLabel: 'Write a message to the book guide',
    placeholder: 'Write your message…', send: 'Send', trust: 'We do not ask for your name, and this chat clears when you start again.',
    back: 'Back', accordingReaders: 'According to readers', closeChoice: 'A close match for what you described', language: 'Language',
    findAt: 'Find it at', want: 'I want this book', feedbackTitle: 'Lovely choice',
    feedbackQuestion: 'Was this recommendation a good fit?', perfect: 'Perfect fit', close: 'Close', no: 'Not for me',
    greeting: "Welcome! I'm your Thalath book guide. Tell me your mood today, or the last book you enjoyed, and I'll help you find something available here.",
    startChips: ['A fun novel', 'Something light with coffee', 'Help me start reading', 'A gift for a reader'],
    detailsAria: (title, author) => `View details for ${title} by ${author}`,
    pages: n => `${n} pages`, unknownLength: 'Length unknown', shelf: value => `Shelf ${value}`,
    askStaff: 'Ask a bookseller for its location', available: 'Available', fallbackReply: 'Here are a few books available now.',
    fallbackChips: ['Show me others', 'Something lighter'], typing: 'The book guide is typing',
    connectionError: 'The book guide is unavailable right now. Check your connection and try again.',
    book: 'Book', shelfUnknown: 'Ask a bookseller to show you', whereLabel: 'Where to find it', unknown: 'Unknown', currency: value => `SAR ${value}`,
    selectedAt: (title, place) => `You will find “${title}” at ${place}. A bookseller can help you too.`,
    selectedHelp: title => `You chose “${title}”. A bookseller can help you find it.`,
    feedbackSaved: 'Thank you — your feedback was saved.', feedbackFailed: 'We could not save your feedback, but thank you all the same.',
    enjoy: title => `Enjoy “${title}”! If you want another suggestion or have a question, I'm here.`,
    afterFeedback: ['Suggest another book', 'New conversation'], oneCopy: '1 copy', twoCopies: '2 copies',
    copies: n => `${n} copies`, availableSuffix: ' available', switchReset: 'Language changed — we started a new conversation.',
  }
};

const GENRES_EN = {
  'رواية': 'Novel', 'روايات': 'Novels', 'أدب': 'Literature', 'قصص قصيرة': 'Short stories',
  'شعر': 'Poetry', 'سيرة': 'Biography', 'تاريخ': 'History', 'فكر': 'Thought', 'علم نفس': 'Psychology',
  'تطوير ذاتي': 'Self-development', 'علوم': 'Science', 'مسرحية': 'Play', 'أدب رحلات': 'Travel writing',
  'ديني': 'Religion', 'دين': 'Religion', 'سيرة ذاتية': 'Biography', 'سيرة نبوية': "The Prophet's biography",
  'أطفال': "Children's", 'يافعين': 'Young adult', 'أعمال': 'Business'
};

let currentLanguage = 'ar';
let currentSection = 'welcome';
let messages = [];
let conversationId = newId();
let busy = false;
let selectedBook = null;
let choiceId = null;          // id of the recorded «أبغى هذا الكتاب» choice (anonymous)
let chosenInChat = {};        // book_id -> choice id, so one book is counted once per conversation
let toastTimer = null;
const t = key => COPY[currentLanguage][key];

function applyLanguage() {
  const english = currentLanguage === 'en';
  document.documentElement.lang = currentLanguage;
  document.documentElement.dir = english ? 'ltr' : 'rtl';
  document.title = t('title');
  $('metaDescription').content = t('description');
  $('brandTagline').textContent = t('tagline');
  $('welcomeKicker').textContent = t('kicker');
  $('welcomeTitle').innerHTML = t('hero');
  $('welcomeBody').textContent = t('welcomeBody');
  $('startNote').textContent = t('startNote');
  $('startLabel').textContent = t('start');
  $('stepDiscover').textContent = t('discover');
  $('stepAsk').textContent = t('ask');
  $('stepRead').textContent = t('read');
  $('newChatBtn').textContent = t('newChat');
  $('chatTitle').textContent = t('assistant');
  $('testBanner').textContent = t('testBanner');
  $('chatLog').setAttribute('aria-label', t('conversation'));
  $('chips').setAttribute('aria-label', t('suggestedReplies'));
  $('chatInputLabel').textContent = t('inputLabel');
  $('chatInput').placeholder = t('placeholder');
  $('sendBtn').setAttribute('aria-label', t('send'));
  $('trustNote').textContent = t('trust');
  $('backBtn').textContent = t('back');
  $('readerLabel').textContent = t('accordingReaders');
  $('languageLabel').textContent = t('language');
  $('findLabel').textContent = selectedBook && !locationText(selectedBook) ? t('whereLabel') : t('findAt');
  $('wantBtn').textContent = t('want');
  $('feedbackTitle').textContent = t('feedbackTitle');
  $('feedbackQuestion').textContent = t('feedbackQuestion');
  $('feedbackPerfect').textContent = t('perfect');
  $('feedbackClose').textContent = t('close');
  $('feedbackNo').textContent = t('no');
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
  const pages = book.page_count ? t('pages')(formatNumber(book.page_count)) : t('unknownLength');
  const stock = stockLabel(book.quantity);
  const shelf = locationText(book) || t('askStaff');
  card.innerHTML = `<span class="book-card-copy"><span class="book-title">${escapeHtml(book.title)}</span><span class="book-author">${escapeHtml(book.author)}</span><span class="book-reason">${escapeHtml(book.reason || t('closeChoice'))}</span><span class="book-meta"><span class="meta-chip stock-chip">${escapeHtml(t('available'))} · ${escapeHtml(stock)}</span><span class="meta-chip">${escapeHtml(pages)}</span><span class="meta-chip">${escapeHtml(shelf)}</span></span></span>${book.cover_url ? `<span class="book-token has-cover" aria-hidden="true"><img src="${escapeHtml(book.cover_url)}" alt="" loading="lazy" /></span>` : `<span class="book-token" aria-hidden="true"><span>${escapeHtml(initial)}</span></span>`}`;
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
  try {
    const response = await fetch(`${API}/chat`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, conversation_id: conversationId, language: currentLanguage })
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const books = Array.isArray(data.books) ? data.books : [];
    setTestMode(!!data.test_mode);
    typing(false);
    botMessage(data.reply || t('fallbackReply'), books);
    messages.push({ role: 'assistant', content: data.reply || '', book_ids: books.map(book => book.book_id) });
    setChips(Array.isArray(data.suggestions) && data.suggestions.length ? data.suggestions : t('fallbackChips'));
  } catch (error) {
    typing(false);
    messages.pop();
    botMessage(t('connectionError'), [], 'msg-error');
    setChips([content]);
  } finally {
    setBusy(false);
  }
}

function newChat() {
  messages = [];
  conversationId = newId();
  chosenInChat = {};
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
  $('dAuthor').textContent = book.author;
  $('dInitial').textContent = [...String(book.title || (currentLanguage === 'ar' ? 'ك' : 'B')).trim()][0] || 'B';
  $('dGenre').textContent = currentLanguage === 'en' ? (GENRES_EN[book.genre] || book.genre || t('book')) : (book.genre || t('book'));
  $('dTraits').textContent = t('closeChoice');
  $('dPrice').textContent = t('currency')(formatNumber(book.price || 0));
  $('dStock').textContent = stockLabel(book.quantity, true);
  $('dShelf').textContent = locationText(book) || t('shelfUnknown');
  $('findLabel').textContent = locationText(book) ? t('findAt') : t('whereLabel');
  $('dPages').textContent = book.page_count ? t('pages')(formatNumber(book.page_count)) : t('unknown');
  $('dOrigin').textContent = book.origin || '';
  $('dOriginBox').hidden = !book.origin;
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

function backToChat() { showSection('chat'); scrollToEnd(); }
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
  choiceId = chosenInChat[id] || null;
  if (!choiceId) {
    try {
      const response = await fetch(`${API}/choice`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ book_id: id, match: selectedBook.match || null, language: currentLanguage, conversation_id: conversationId })
      });
      if (response.ok) { choiceId = (await response.json()).choice_id; chosenInChat[id] = choiceId; }
    } catch (error) { choiceId = null; }
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
      body: JSON.stringify(choiceId ? { rating, choice_id: choiceId, conversation_id: conversationId }
        : { rating, book_id: selectedBook ? selectedBook.book_id : null, conversation_id: conversationId })
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

function showSection(id) {
  currentSection = id;
  ['welcome', 'chat', 'bookDetail', 'feedback'].forEach(section => { $(section).hidden = section !== id; });
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
$('startBtn').addEventListener('click', newChat);
$('newChatBtn').addEventListener('click', newChat);
$('backBtn').addEventListener('click', backToChat);
$('wantBtn').addEventListener('click', chooseBook);
['welcomeLangBtn', 'chatLangBtn', 'detailLangBtn', 'feedbackLangBtn'].forEach(id => $(id).addEventListener('click', switchLanguage));
document.querySelectorAll('.feedback-button').forEach(button => button.addEventListener('click', () => sendFeedback(button.dataset.value)));

applyLanguage();
showSection('welcome');
checkTestMode();
